import json
import re
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from services import log_service, web_fetch

GOOGLE_ARTICLE = re.compile(r"^https?://news\.google\.com/(?:rss/)?articles/([A-Za-z0-9_-]+)")
TRACKING_PARAM = re.compile(r"^(utm_\w+|fbclid|gclid|dclid|mc_cid|mc_eid|ocid|cmpid|at_\w+)$", re.IGNORECASE)
ARTICLE_PAGE = "https://news.google.com/articles/{}"
DECODE_RPC = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
SIGNATURE = re.compile(r'data-n-a-sg="([^"]+)"')
TIMESTAMP = re.compile(r'data-n-a-ts="([^"]+)"')


def google_id(url: Optional[str]) -> Optional[str]:
    match = GOOGLE_ARTICLE.match(url or "")
    return match.group(1) if match else None


def is_google(url: Optional[str]) -> bool:
    return google_id(url) is not None


def clean(url: str) -> str:
    parts = urlsplit(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if not TRACKING_PARAM.match(k)])
    return urlunsplit((parts.scheme or "https", parts.netloc, parts.path, query, ""))


def identity(url: str) -> str:
    parts = urlsplit(clean(url))
    return f"{parts.netloc.lower().removeprefix('www.')}{parts.path.rstrip('/')}" + (
        f"?{parts.query}" if parts.query else "")


USAGE = ("news", "google_news_decode")


async def decode(link_id: str) -> Optional[str]:
    policy = web_fetch.google_news_policy()
    try:
        page = await web_fetch.get(ARTICLE_PAGE.format(link_id), policy, USAGE, retries=1)
        page.raise_for_status()
        signature, timestamp = SIGNATURE.search(page.text), TIMESTAMP.search(page.text)
        if not signature or not timestamp:
            raise ValueError("article page has no decode signature")
        request = ["Fbv4je", f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,'
                             f'null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{link_id}",'
                             f'{timestamp.group(1)},"{signature.group(1)}"]']
        reply = await web_fetch.post(DECODE_RPC, policy, USAGE, retries=1, data={"f.req": json.dumps([[request]])})
        reply.raise_for_status()
        payload = json.loads(json.loads(reply.text.split("\n\n")[1])[0][2])
        url = payload[1] if isinstance(payload, list) and len(payload) > 1 else None
        if not isinstance(url, str) or not url.startswith("http"):
            raise ValueError("decode reply has no address")
    except web_fetch.HostResting:
        raise
    except Exception as e:
        log_service.throttled("news_link_decode", f"News: could not resolve a Google News link "
                                                  f"({type(e).__name__}: {e})")
        return None
    return clean(url)
