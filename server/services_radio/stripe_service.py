import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from database.models import User, StripeWebhookEvent
from routers.deps import get_current_user
from services import log_service
from services.rate_limit_service import rate_limit_service
from services.user_data_cache_service import user_data_cache

stripe.api_key = settings.STRIPE_SECRET_KEY
stripe.max_network_retries = 2

stripe_router = APIRouter()

PREMIUM_STATUSES = frozenset({"active", "trialing"})
GRACE_STATUSES = frozenset({"past_due"})
SUBSCRIBED_STATUSES = PREMIUM_STATUSES | GRACE_STATUSES
HANDLED_EVENTS = frozenset({
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.paid",
    "invoice.payment_failed",
})
UNAVAILABLE_DETAIL = "Billing is temporarily unavailable. Please try again in a moment."


class WebhookIgnored(Exception):
    pass


def _plair_price_ids() -> frozenset:
    return frozenset(p for p in {settings.STRIPE_PRICE_ID, *settings.STRIPE_ADDITIONAL_PRICE_IDS} if p)


def _billing_configured() -> bool:
    return bool(settings.STRIPE_SECRET_KEY and settings.STRIPE_PRICE_ID)


def _require_billing():
    if not _billing_configured():
        raise HTTPException(status_code=503, detail="Billing is not available right now.")


def _require_user(user: Optional[User]) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Please sign in to manage your subscription.")
    return user


def _key_is_live() -> bool:
    return "_live_" in (settings.STRIPE_SECRET_KEY or "")


async def _stripe(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


def _plain(obj: Any) -> Any:
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return obj


def _get(obj: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _ref_id(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        value = value.get("id")
    return value if isinstance(value, str) and value else None


def _is_plair_metadata(metadata: Any) -> bool:
    return isinstance(metadata, dict) and metadata.get("app") == settings.STRIPE_APP_TAG


def _parse_user_id(value: Any) -> Optional[int]:
    text = str(value).strip() if value is not None else ""
    return int(text) if text.isdigit() and len(text) < 12 else None


def _subscription_items(sub: dict) -> list:
    return _get(sub, "items", "data") or []


def _subscription_price_ids(sub: dict) -> set:
    ids = set()
    for item in _subscription_items(sub):
        price_id = _ref_id(item.get("price")) or _ref_id(item.get("plan"))
        if price_id:
            ids.add(price_id)
    return ids


def _has_plair_price(sub: dict) -> bool:
    return bool(_subscription_price_ids(sub) & _plair_price_ids())


def _subscription_period_end(sub: dict) -> Optional[datetime]:
    candidates = [sub.get("current_period_end")]
    candidates += [item.get("current_period_end") for item in _subscription_items(sub)]
    stamps = [c for c in candidates if isinstance(c, (int, float))]
    if not stamps:
        return None
    return datetime.fromtimestamp(max(stamps), tz=timezone.utc)


def _invoice_subscription_id(invoice: dict) -> Optional[str]:
    return (
        _ref_id(invoice.get("subscription"))
        or _ref_id(_get(invoice, "parent", "subscription_details", "subscription"))
    )


def _invoice_metadata(invoice: dict) -> dict:
    return (
        _get(invoice, "parent", "subscription_details", "metadata")
        or _get(invoice, "subscription_details", "metadata")
        or {}
    )


def _invoice_price_ids(invoice: dict) -> set:
    ids = set()
    for line in _get(invoice, "lines", "data") or []:
        price_id = (
            _ref_id(line.get("price"))
            or _ref_id(_get(line, "pricing", "price_details", "price"))
            or _ref_id(line.get("plan"))
        )
        if price_id:
            ids.add(price_id)
    return ids


async def _retrieve_subscription(subscription_id: str) -> Optional[dict]:
    try:
        return _plain(await _stripe(stripe.Subscription.retrieve, subscription_id))
    except stripe.InvalidRequestError as e:
        log_service.warning(f"[Stripe] Subscription {subscription_id} not retrievable: {e.user_message or e.code}")
        return None


async def _find_user(
    db: AsyncSession,
    metadata: Any,
    customer_id: Optional[str],
    client_reference_id: Any = None
) -> Optional[User]:
    candidates = [client_reference_id]
    if _is_plair_metadata(metadata):
        candidates.append(metadata.get("user_id"))
    for candidate in candidates:
        user_id = _parse_user_id(candidate)
        if user_id is None:
            continue
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if user is not None:
            return user
    if customer_id:
        return (await db.execute(select(User).where(User.stripe_customer_id == customer_id))).scalar_one_or_none()
    return None


async def _link_customer(db: AsyncSession, user: User, customer_id: Optional[str]):
    if not customer_id or user.stripe_customer_id == customer_id:
        return
    owner = (await db.execute(select(User).where(User.stripe_customer_id == customer_id))).scalar_one_or_none()
    if owner is not None and owner.id != user.id:
        log_service.error(f"[Stripe] Customer {customer_id} already linked to user {owner.id}, not relinking to {user.id}")
        return
    user.stripe_customer_id = customer_id


def _apply_subscription(user: User, sub: dict) -> bool:
    status = sub.get("status")
    sub_id = sub.get("id")
    if (
        user.stripe_subscription_id
        and user.stripe_subscription_id != sub_id
        and status not in PREMIUM_STATUSES
        and user.subscription_status in SUBSCRIBED_STATUSES
    ):
        log_service.info(f"[Stripe] Ignoring {status} for {sub_id}; user {user.id} is on {user.stripe_subscription_id}")
        return False

    user.stripe_subscription_id = sub_id
    user.subscription_status = status
    user.current_period_end = _subscription_period_end(sub)
    if status in PREMIUM_STATUSES:
        user.tier = "premium"
        user.subscribed = True
    elif status not in GRACE_STATUSES:
        user.tier = "basic"
        user.subscribed = False
    log_service.info(f"[Stripe] User {user.id}: subscription {sub_id} status={status} tier={user.tier}")
    return True


async def _sync_subscription(db: AsyncSession, sub: dict, metadata: Any = None, client_reference_id: Any = None) -> User:
    if not _has_plair_price(sub):
        raise WebhookIgnored("subscription price is not PLAiR's")
    customer_id = _ref_id(sub.get("customer"))
    user = await _find_user(db, metadata if metadata is not None else sub.get("metadata"), customer_id, client_reference_id)
    if user is None:
        log_service.error(f"[Stripe] No user for subscription {sub.get('id')} (customer {customer_id})")
        raise WebhookIgnored("user not found")
    await _link_customer(db, user, customer_id)
    _apply_subscription(user, sub)
    return user


async def _handle_checkout_completed(db: AsyncSession, session: dict) -> User:
    if session.get("mode") != "subscription":
        raise WebhookIgnored("not a subscription checkout")
    subscription_id = _ref_id(session.get("subscription"))
    if not subscription_id:
        raise WebhookIgnored("no subscription on session")
    sub = await _retrieve_subscription(subscription_id)
    if sub is None:
        raise WebhookIgnored("subscription missing")
    if not _has_plair_price(sub):
        raise WebhookIgnored("subscription price is not PLAiR's")
    paid = session.get("payment_status") == "paid" or sub.get("status") in PREMIUM_STATUSES
    if not paid:
        log_service.info(f"[Stripe] Checkout {session.get('id')} completed without payment (status {sub.get('status')})")
    return await _sync_subscription(db, sub, session.get("metadata") or sub.get("metadata"), session.get("client_reference_id"))


async def _handle_subscription_event(db: AsyncSession, payload_sub: dict) -> User:
    if not (_has_plair_price(payload_sub) or _is_plair_metadata(payload_sub.get("metadata"))):
        raise WebhookIgnored("not a PLAiR subscription")
    if not payload_sub.get("id"):
        raise WebhookIgnored("subscription has no id")
    sub = await _retrieve_subscription(payload_sub["id"]) or payload_sub
    return await _sync_subscription(db, sub)


async def _handle_invoice(db: AsyncSession, invoice: dict, failed: bool) -> User:
    metadata = _invoice_metadata(invoice)
    if not (_invoice_price_ids(invoice) & _plair_price_ids() or _is_plair_metadata(metadata)):
        raise WebhookIgnored("not a PLAiR invoice")
    subscription_id = _invoice_subscription_id(invoice)
    if not subscription_id:
        raise WebhookIgnored("invoice has no subscription")
    sub = await _retrieve_subscription(subscription_id)
    if sub is None:
        raise WebhookIgnored("subscription missing")
    user = await _sync_subscription(db, sub)
    if failed and user.subscription_status in PREMIUM_STATUSES and user.stripe_subscription_id == subscription_id:
        user.subscription_status = "past_due"
        log_service.warning(f"[Stripe] Payment failed for user {user.id}; premium kept while Stripe retries")
    return user


async def _process_event(db: AsyncSession, event: dict) -> Optional[User]:
    event_type = event.get("type")
    obj = _get(event, "data", "object") or {}
    if event_type == "checkout.session.completed":
        return await _handle_checkout_completed(db, obj)
    if event_type and event_type.startswith("customer.subscription."):
        return await _handle_subscription_event(db, obj)
    if event_type in ("invoice.paid", "invoice.payment_failed"):
        return await _handle_invoice(db, obj, failed=event_type == "invoice.payment_failed")
    return None


def _status_payload(user: User) -> dict:
    period_end = user.current_period_end
    return {
        "tier": user.tier or "basic",
        "subscribed": bool(user.subscribed),
        "status": user.subscription_status,
        "current_period_end": period_end.isoformat() if period_end else None,
        "price_label": settings.STRIPE_PRICE_LABEL,
        "has_billing_account": bool(user.stripe_customer_id),
        "billing_available": _billing_configured(),
        "generation_usage": rate_limit_service.get_generation_usage(int(user.id), str(user.id), user),
    }


async def _ensure_customer(db: AsyncSession, user: User) -> str:
    if user.stripe_customer_id:
        try:
            customer = _plain(await _stripe(stripe.Customer.retrieve, user.stripe_customer_id))
            if not customer.get("deleted"):
                return customer["id"]
        except stripe.InvalidRequestError:
            log_service.warning(f"[Stripe] Stored customer for user {user.id} not found; creating a new one")

    minute = int(time.time() // 60)
    customer = await _stripe(
        stripe.Customer.create,
        metadata={"app": settings.STRIPE_APP_TAG, "user_id": str(user.id), "username": user.username or ""},
        idempotency_key=f"plair-customer-{user.id}-{minute}",
    )
    user.stripe_customer_id = customer.id
    await db.commit()
    await user_data_cache.invalidate_user(int(user.id))
    log_service.info(f"[Stripe] Created customer {customer.id} for user {user.id}")
    return customer.id


async def _find_subscribed_plair_subscription(customer_id: str) -> Optional[dict]:
    result = _plain(await _stripe(stripe.Subscription.list, customer=customer_id, status="all", limit=20))
    for sub in result.get("data") or []:
        if sub.get("status") in SUBSCRIBED_STATUSES and _has_plair_price(sub):
            return sub
    return None


@stripe_router.post("/create-checkout-session")
async def create_checkout_session(
    user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user = _require_user(user)
    _require_billing()
    if user.tier == "premium" or user.subscription_status in SUBSCRIBED_STATUSES:
        raise HTTPException(status_code=409, detail="You already have Premium. Use Manage subscription to make changes.")

    try:
        customer_id = await _ensure_customer(db, user)
        existing = await _find_subscribed_plair_subscription(customer_id)
        if existing is not None:
            _apply_subscription(user, existing)
            await db.commit()
            await user_data_cache.invalidate_user(int(user.id))
            raise HTTPException(status_code=409, detail="You already have Premium. Use Manage subscription to make changes.")

        metadata = {"app": settings.STRIPE_APP_TAG, "user_id": str(user.id)}
        base = settings.PUBLIC_BASE_URL
        session = await _stripe(
            stripe.checkout.Session.create,
            mode="subscription",
            customer=customer_id,
            line_items=[{"price": settings.STRIPE_PRICE_ID, "quantity": 1}],
            client_reference_id=str(user.id),
            metadata=metadata,
            subscription_data={"metadata": metadata},
            success_url=f"{base}/?billing=success&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base}/?billing=cancelled",
            idempotency_key=f"plair-checkout-{user.id}-{int(time.time() // 60)}",
        )
    except stripe.StripeError as e:
        log_service.error(f"[Stripe] Checkout creation failed for user {user.id}: {type(e).__name__} {e.code or ''} {e.user_message or ''}")
        raise HTTPException(status_code=502, detail=UNAVAILABLE_DETAIL)

    log_service.info(f"[Stripe] Checkout session {session.id} created for user {user.id}")
    return {"url": session.url}


@stripe_router.post("/portal")
async def create_portal_session(user: Optional[User] = Depends(get_current_user)):
    user = _require_user(user)
    _require_billing()
    if not user.stripe_customer_id:
        raise HTTPException(status_code=400, detail="No billing account found for this user.")

    params = {"customer": user.stripe_customer_id, "return_url": f"{settings.PUBLIC_BASE_URL}/"}
    if settings.STRIPE_PORTAL_CONFIGURATION_ID:
        params["configuration"] = settings.STRIPE_PORTAL_CONFIGURATION_ID
    try:
        session = await _stripe(stripe.billing_portal.Session.create, **params)
    except stripe.StripeError as e:
        log_service.error(f"[Stripe] Portal creation failed for user {user.id}: {type(e).__name__} {e.code or ''} {e.user_message or ''}")
        raise HTTPException(status_code=502, detail=UNAVAILABLE_DETAIL)
    return {"url": session.url}


@stripe_router.get("/status")
async def get_billing_status(
    session_id: Optional[str] = None,
    user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user = _require_user(user)
    if session_id and _billing_configured() and session_id.startswith("cs_") and len(session_id) < 256:
        try:
            checkout = _plain(await _stripe(stripe.checkout.Session.retrieve, session_id))
            owned = checkout.get("client_reference_id") == str(user.id) and _is_plair_metadata(checkout.get("metadata"))
            subscription_id = _ref_id(checkout.get("subscription"))
            if owned and subscription_id:
                sub = await _retrieve_subscription(subscription_id)
                if sub is not None and _has_plair_price(sub):
                    await _link_customer(db, user, _ref_id(sub.get("customer")))
                    _apply_subscription(user, sub)
                    await db.commit()
                    await user_data_cache.invalidate_user(int(user.id))
        except stripe.StripeError as e:
            log_service.warning(f"[Stripe] Checkout sync failed for user {user.id}: {type(e).__name__} {e.code or ''}")
    return _status_payload(user)


@stripe_router.post("/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    if not settings.STRIPE_WEBHOOK_SECRET:
        log_service.error("[Stripe] Webhook received but STRIPE_WEBHOOK_SECRET is not configured")
        return JSONResponse({"error": "Webhook not configured"}, status_code=503)

    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature")
    if not sig_header:
        return JSONResponse({"error": "Invalid signature"}, status_code=400)
    try:
        payload_text = payload.decode("utf-8")
        stripe.WebhookSignature.verify_header(
            payload_text, sig_header, settings.STRIPE_WEBHOOK_SECRET, settings.STRIPE_WEBHOOK_TOLERANCE_S
        )
        event = json.loads(payload_text)
    except stripe.SignatureVerificationError:
        log_service.warning("[Stripe] Webhook signature verification failed")
        return JSONResponse({"error": "Invalid signature"}, status_code=400)
    except (UnicodeDecodeError, ValueError):
        log_service.warning("[Stripe] Webhook payload could not be parsed")
        return JSONResponse({"error": "Invalid payload"}, status_code=400)

    event_id = event.get("id") if isinstance(event, dict) else None
    event_type = event.get("type") if isinstance(event, dict) else None
    if not event_id or not event_type:
        return JSONResponse({"error": "Invalid payload"}, status_code=400)

    if event_type not in HANDLED_EVENTS:
        return JSONResponse({"status": "ignored"})
    if bool(event.get("livemode")) != _key_is_live():
        log_service.warning(f"[Stripe] Ignoring {event_type} {event_id}: livemode does not match configured key")
        return JSONResponse({"status": "ignored"})

    if await db.get(StripeWebhookEvent, event_id) is not None:
        log_service.info(f"[Stripe] Duplicate event {event_id} ({event_type}) skipped")
        return JSONResponse({"status": "duplicate"})

    try:
        user = await _process_event(db, event)
    except WebhookIgnored as reason:
        await db.rollback()
        log_service.info(f"[Stripe] Ignored {event_type} {event_id}: {reason}")
        return JSONResponse({"status": "ignored"})
    except stripe.StripeError as e:
        await db.rollback()
        log_service.error(f"[Stripe] Stripe API error handling {event_type} {event_id}: {type(e).__name__} {e.code or ''}")
        return JSONResponse({"error": "Webhook processing failed"}, status_code=500)
    except Exception as e:
        await db.rollback()
        log_service.error(f"[Stripe] Error handling {event_type} {event_id}: {type(e).__name__}: {e}")
        return JSONResponse({"error": "Webhook processing failed"}, status_code=500)

    db.add(StripeWebhookEvent(id=event_id, event_type=event_type))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        log_service.info(f"[Stripe] Event {event_id} processed concurrently; skipped")
        return JSONResponse({"status": "duplicate"})

    if user is not None:
        await user_data_cache.invalidate_user(int(user.id))
    log_service.info(f"[Stripe] Handled {event_type} {event_id}")
    return JSONResponse({"status": "success"})


async def close_billing_for_deleted_account(user: User) -> None:
    customer_id = user.stripe_customer_id
    subscription_id = user.stripe_subscription_id
    if not customer_id and not subscription_id:
        return
    if not settings.STRIPE_SECRET_KEY:
        raise RuntimeError("Billing is not available right now, so the subscription can't be cancelled")
    try:
        if customer_id:
            await _stripe(stripe.Customer.delete, customer_id)
        else:
            await _stripe(stripe.Subscription.cancel, subscription_id)
    except stripe.InvalidRequestError as e:
        if getattr(e, "code", None) != "resource_missing":
            raise
    log_service.info(f"[Stripe] Closed billing for deleted account {log_service.who(user_id=user.id)}")
