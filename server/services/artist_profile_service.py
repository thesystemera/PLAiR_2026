import json
import re
import unicodedata
from typing import Dict, List, Optional

from sqlalchemy import func, select

from database import AsyncSessionLocal
from database.models import ArtistProfile, User
from services import log_service

NAME_MAX = 80
BIO_MAX = 2000
LINKS_MAX = 8
LINK_MAX = 300


def slugify(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:60] or "artist"


def clean_name(name: Optional[str]) -> str:
    return " ".join((name or "").split())[:NAME_MAX]


def clean_links(links) -> List[str]:
    if isinstance(links, str):
        links = [links]
    cleaned = []
    for link in links or []:
        link = str(link or "").strip()[:LINK_MAX]
        if link and re.match(r"^https?://", link, re.IGNORECASE) and link not in cleaned:
            cleaned.append(link)
    return cleaned[:LINKS_MAX]


def to_dict(profile: ArtistProfile) -> Dict:
    try:
        links = json.loads(profile.links) if profile.links else []
    except (TypeError, ValueError):
        links = []
    return {"id": profile.id, "name": profile.name, "slug": profile.slug, "bio": profile.bio or "",
            "links": links, "owner_user_id": profile.owner_user_id}


async def _unique_slug(db, name: str, exclude_id: Optional[int] = None) -> str:
    base = slugify(name)
    slug, n = base, 1
    while True:
        query = select(ArtistProfile.id).where(ArtistProfile.slug == slug)
        if exclude_id is not None:
            query = query.where(ArtistProfile.id != exclude_id)
        if (await db.execute(query)).first() is None:
            return slug
        n += 1
        slug = f"{base}-{n}"


async def list_for_user(user_id: int) -> List[Dict]:
    async with AsyncSessionLocal() as db:
        rows = await db.execute(select(ArtistProfile).where(ArtistProfile.owner_user_id == user_id)
                                .order_by(ArtistProfile.created_at))
        return [to_dict(p) for p in rows.scalars().all()]


async def get(profile_id: int) -> Optional[Dict]:
    async with AsyncSessionLocal() as db:
        profile = await db.get(ArtistProfile, profile_id)
        return to_dict(profile) if profile else None


async def create(user_id: int, name: str, bio: str = "", links=None) -> Dict:
    name = clean_name(name)
    if not name:
        raise ValueError("Artist name is required")
    async with AsyncSessionLocal() as db:
        existing = await db.execute(select(ArtistProfile).where(
            ArtistProfile.owner_user_id == user_id, func.lower(ArtistProfile.name) == name.lower()))
        if existing.scalar_one_or_none() is not None:
            raise ValueError("You already have an artist with that name")
        profile = ArtistProfile(owner_user_id=user_id, name=name, slug=await _unique_slug(db, name),
                                bio=(bio or "").strip()[:BIO_MAX] or None, links=json.dumps(clean_links(links)))
        db.add(profile)
        await db.commit()
        await db.refresh(profile)
        log_service.listener(f"{log_service.who(user_id=user_id)}: created artist profile '{name}'")
        return to_dict(profile)


async def update(user_id: int, profile_id: int, fields: Dict) -> Dict:
    async with AsyncSessionLocal() as db:
        profile = await db.get(ArtistProfile, profile_id)
        if profile is None or profile.owner_user_id != user_id:
            raise LookupError("Artist not found")
        if "name" in fields:
            name = clean_name(fields["name"])
            if not name:
                raise ValueError("Artist name is required")
            if name != profile.name:
                profile.name = name
                profile.slug = await _unique_slug(db, name, exclude_id=profile.id)
        if "bio" in fields:
            profile.bio = (fields["bio"] or "").strip()[:BIO_MAX] or None
        if "links" in fields:
            profile.links = json.dumps(clean_links(fields["links"]))
        await db.commit()
        await db.refresh(profile)
        return to_dict(profile)


async def delete(user_id: int, profile_id: int) -> None:
    async with AsyncSessionLocal() as db:
        profile = await db.get(ArtistProfile, profile_id)
        if profile is None or profile.owner_user_id != user_id:
            raise LookupError("Artist not found")
        await db.delete(profile)
        await db.commit()


async def resolve_for_upload(user: User, profile_id: Optional[int] = None) -> Dict:
    async with AsyncSessionLocal() as db:
        owned = (await db.execute(select(ArtistProfile).where(ArtistProfile.owner_user_id == user.id)
                                  .order_by(ArtistProfile.created_at))).scalars().all()
        by_id = {p.id: p for p in owned}
        chosen = by_id.get(profile_id) or by_id.get(getattr(user, "last_artist_profile_id", None))
        if chosen is None and owned:
            chosen = owned[0]
        if chosen is None:
            chosen = ArtistProfile(owner_user_id=user.id, name=clean_name(user.username) or "Unknown Artist",
                                   slug=await _unique_slug(db, user.username or "artist"))
            db.add(chosen)
            await db.flush()
        db_user = await db.get(User, user.id)
        if db_user is not None:
            db_user.last_artist_profile_id = chosen.id
        await db.commit()
        await db.refresh(chosen)
        return to_dict(chosen)
