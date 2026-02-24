from fastapi import APIRouter, Request, Depends, Header
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
import stripe

from keys import api_secrets
from database.models import User
from services import log_service, auth_service

stripe.api_key = api_secrets.STRIPE_SECRET_KEY

stripe_router = APIRouter()

async def get_db():
    from database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        yield session

async def get_current_user(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db)
) -> Optional[User]:
    if not authorization or not authorization.startswith("Bearer "):
        return None

    jwt_token = authorization.replace("Bearer ", "")
    payload = auth_service.decode_token(jwt_token)

    if not payload:
        return None

    user_id = payload.get("sub")
    if not user_id:
        return None

    return await auth_service.get_user_by_id(db, int(user_id))

@stripe_router.get('/stripe_key')
async def get_stripe_key():
    return JSONResponse({'publishableKey': api_secrets.STRIPE_PUBLISHABLE_KEY})

@stripe_router.post('/create-checkout-session')
async def create_checkout_session(
    request: Request,
    user: User = Depends(get_current_user)
):
    if not user:
        return JSONResponse({'error': 'User not authenticated'}, status_code=401)

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price': 'price_1PNSp5H7yntqOgI3RNgLyo5h',
                'quantity': 1,
            }],
            mode='subscription',
            success_url=str(request.base_url) + 'success',
            cancel_url=str(request.base_url) + 'cancel',
            client_reference_id=str(user.id),
        )
        log_service.info(f"Action: Checkout session created: {checkout_session.id} for user: {user.id}")
        return JSONResponse({'id': checkout_session.id})
    except stripe.error.CardError as e:  # type: ignore
        error_message = str(e)
        log_service.error(f"Stripe error: {error_message}")
        return JSONResponse({'error': error_message}, status_code=403)
    except Exception as e:
        error_message = str(e)
        log_service.error(f"Error: {error_message}")
        return JSONResponse({'error': error_message}, status_code=500)

@stripe_router.post('/webhook')
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    payload = await request.body()
    sig_header = request.headers.get('Stripe-Signature')
    endpoint_secret = api_secrets.STRIPE_WEBHOOK_SECRET

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
    except ValueError:
        log_service.error("Invalid payload")
        return JSONResponse({'error': 'Invalid payload'}, status_code=400)
    except stripe.error.SignatureVerificationError:  # type: ignore
        log_service.error("Invalid signature")
        return JSONResponse({'error': 'Invalid signature'}, status_code=400)

    if event['type'] == 'checkout.session.completed':
        await handle_checkout_session_completed(event['data']['object'], db)
    elif event['type'] == 'customer.subscription.deleted':
        await handle_subscription_deleted(event['data']['object'], db)
    elif event['type'] == 'invoice.payment_succeeded':
        await handle_invoice_payment_succeeded(event['data']['object'])
    elif event['type'] == 'invoice.payment_failed':
        await handle_invoice_payment_failed(event['data']['object'])

    log_service.info(f"Action: Handled event: {event['type']}")
    return JSONResponse({'status': 'success'})

async def handle_checkout_session_completed(session, db: AsyncSession):
    log_service.info(f"Action: Checkout session completed: {session}")
    user_id = session.get('client_reference_id')
    if user_id:
        result = await db.execute(select(User).where(User.id == int(user_id)))
        user = result.scalar_one_or_none()
        if user:
            user.subscribed = True  # type: ignore
            user.tier = "premium"  # type: ignore
            await db.commit()
            log_service.info(f"Database: Updated subscription for user {user_id}: subscribed=True, tier=premium")
        else:
            log_service.error(f"User not found for ID: {user_id}")
    else:
        log_service.error("No client_reference_id found in session")

async def handle_subscription_deleted(subscription, db: AsyncSession):
    log_service.info(f"Action: Subscription deleted: {subscription}")
    customer_id = subscription.get('customer')
    if customer_id:
        result = await db.execute(select(User).where(User.stripe_customer_id == customer_id))
        user = result.scalar_one_or_none()
        if user:
            user.subscribed = False  # type: ignore
            user.tier = "basic"  # type: ignore
            await db.commit()
            log_service.info(f"Database: Updated subscription for user {user.id}: subscribed=False, tier=basic")
        else:
            log_service.error(f"User not found for Stripe customer ID: {customer_id}")
    else:
        log_service.error("No customer ID found in subscription")

async def handle_invoice_payment_succeeded(invoice):
    log_service.info(f"Action: Invoice payment succeeded: {invoice}")

async def handle_invoice_payment_failed(invoice):
    log_service.error(f"Action: Invoice payment failed: {invoice}")