import hashlib
import json
import re
import secrets
import unicodedata

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ClickLog, ThreadsPost, ThreadsPostLink
from app.schemas import AnalyticsSummary, AnalyticsTopPost, ThreadsDraft


def create_slug() -> str:
    return secrets.token_urlsafe(8)


def create_tracking_url(slug: str) -> str:
    return f"{get_settings().base_url.rstrip('/')}/go/{slug}"


def hash_ip(ip: str) -> str:
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()


def hashtags_to_json(tags: list[str]) -> str:
    clean = [tag.strip().lstrip("#") for tag in tags if tag.strip()]
    return json.dumps(clean[:3], ensure_ascii=False)


def hashtags_from_json(raw: str) -> list[str]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [str(tag) for tag in value if str(tag).strip()]


def create_post(
    db: Session,
    *,
    keyword: str,
    product_name: str,
    draft: ThreadsDraft,
    status: str,
    affiliate_url: str | None = None,
) -> ThreadsPost:
    slug = create_slug() if affiliate_url else None
    post = ThreadsPost(
        keyword=keyword,
        product_name=product_name,
        affiliate_url=affiliate_url,
        slug=slug,
        tracking_url=create_tracking_url(slug) if slug else None,
        content=draft.content,
        cta=draft.cta,
        hashtags=hashtags_to_json(draft.hashtags),
        status=status,
        quality_score=draft.quality_score,
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


def create_group_post(
    db: Session,
    *,
    keyword: str,
    product_name: str,
    draft: ThreadsDraft,
    links: list[dict[str, str]],
    status: str = "draft",
) -> ThreadsPost:
    post = ThreadsPost(
        keyword=keyword,
        product_name=product_name,
        affiliate_url=None,
        slug=None,
        tracking_url=None,
        content=draft.content,
        cta=draft.cta,
        hashtags=hashtags_to_json(draft.hashtags),
        status=status,
        quality_score=draft.quality_score,
    )
    db.add(post)
    db.flush()

    for item in links:
        slug = create_slug()
        db.add(
            ThreadsPostLink(
                post_id=post.id,
                product_name=item["product_name"],
                affiliate_url=item["affiliate_url"],
                product_url=item.get("product_url") or None,
                price=item.get("price") or None,
                shop_name=item.get("shop_name") or None,
                slug=slug,
                tracking_url=create_tracking_url(slug),
            )
        )

    db.commit()
    db.refresh(post)
    return post


def get_post(db: Session, post_id: int) -> ThreadsPost | None:
    return db.get(ThreadsPost, post_id)


def get_post_by_slug(db: Session, slug: str) -> ThreadsPost | None:
    return db.scalar(select(ThreadsPost).where(ThreadsPost.slug == slug))


def get_post_link_by_slug(db: Session, slug: str) -> ThreadsPostLink | None:
    return db.scalar(select(ThreadsPostLink).where(ThreadsPostLink.slug == slug))


def get_post_by_affiliate_url(db: Session, affiliate_url: str) -> ThreadsPost | None:
    post = db.scalar(select(ThreadsPost).where(ThreadsPost.affiliate_url == affiliate_url))
    if post:
        return post

    link = db.scalar(select(ThreadsPostLink).where(ThreadsPostLink.affiliate_url == affiliate_url))
    return link.post if link else None


def get_post_links(db: Session, post_id: int) -> list[ThreadsPostLink]:
    return list(
        db.scalars(
            select(ThreadsPostLink)
            .where(ThreadsPostLink.post_id == post_id)
            .order_by(ThreadsPostLink.id.asc())
        )
    )


def _normalize_search_text(value: str) -> str:
    value = value.replace("đ", "d").replace("Đ", "D")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _search_tokens(value: str) -> list[str]:
    stopwords = {
        "ao",
        "cai",
        "cho",
        "co",
        "cua",
        "de",
        "gia",
        "hang",
        "loai",
        "mau",
        "mot",
        "nam",
        "nu",
        "san",
        "pham",
        "shopee",
        "tim",
        "va",
    }
    return [
        token
        for token in _normalize_search_text(value).split()
        if len(token) >= 2 and token not in stopwords
    ]


def find_catalog_links(db: Session, keyword: str, limit: int = 5, min_score: int = 2) -> list[ThreadsPostLink]:
    tokens = _search_tokens(keyword)
    if not tokens:
        return []

    links = list(
        db.scalars(
            select(ThreadsPostLink)
            .order_by(ThreadsPostLink.id.desc())
            .limit(500)
        )
    )

    scored: list[tuple[int, ThreadsPostLink]] = []
    seen_urls: set[str] = set()
    for link in links:
        if link.affiliate_url in seen_urls:
            continue
        seen_urls.add(link.affiliate_url)
        haystack = _normalize_search_text(
            " ".join(
                part
                for part in [
                    link.product_name,
                    link.shop_name or "",
                    link.price or "",
                    link.post.keyword if link.post else "",
                ]
                if part
            )
        )
        score = sum(2 if token in haystack.split() else 1 for token in tokens if token in haystack)
        if score >= min_score:
            scored.append((score, link))

    scored.sort(key=lambda item: (item[0], item[1].id), reverse=True)
    return [link for _, link in scored[: max(1, limit)]]


def find_best_catalog_link(db: Session, keyword: str, min_score: int = 2) -> ThreadsPostLink | None:
    links = find_catalog_links(db, keyword, limit=1, min_score=min_score)
    return links[0] if links else None


def list_catalog_links(db: Session, limit: int = 50) -> list[ThreadsPostLink]:
    links = list(
        db.scalars(
            select(ThreadsPostLink)
            .order_by(ThreadsPostLink.id.desc())
            .limit(max(50, limit * 3))
        )
    )

    unique_links: list[ThreadsPostLink] = []
    seen_urls: set[str] = set()
    for link in links:
        if link.affiliate_url in seen_urls:
            continue
        seen_urls.add(link.affiliate_url)
        unique_links.append(link)
        if len(unique_links) >= limit:
            break

    return unique_links


def add_affiliate_link(db: Session, post_id: int, affiliate_url: str) -> ThreadsPost | None:
    post = get_post(db, post_id)
    if not post or post.status == "deleted":
        return None

    post.affiliate_url = affiliate_url
    post.slug = post.slug or create_slug()
    post.tracking_url = create_tracking_url(post.slug)
    post.status = "draft"
    db.commit()
    db.refresh(post)
    return post


def update_status(db: Session, post_id: int, status: str) -> ThreadsPost | None:
    post = get_post(db, post_id)
    if not post:
        return None
    post.status = status
    db.commit()
    db.refresh(post)
    return post


def update_draft_content(db: Session, post_id: int, draft: ThreadsDraft) -> ThreadsPost | None:
    post = get_post(db, post_id)
    if not post:
        return None

    post.content = draft.content
    post.cta = draft.cta
    post.hashtags = hashtags_to_json(draft.hashtags)
    post.quality_score = draft.quality_score
    db.commit()
    db.refresh(post)
    return post


def list_recent_posts(db: Session, limit: int = 10) -> list[ThreadsPost]:
    return list(
        db.scalars(
            select(ThreadsPost)
            .where(ThreadsPost.status != "deleted")
            .order_by(ThreadsPost.id.desc())
            .limit(limit)
        )
    )


def list_posts_by_status(db: Session, status: str, limit: int | None = None) -> list[ThreadsPost]:
    query = (
        select(ThreadsPost)
        .where(ThreadsPost.status == status)
        .order_by(ThreadsPost.id.desc())
    )
    if limit is not None:
        query = query.limit(limit)
    return list(db.scalars(query))


def previous_similar_posts(db: Session, keyword: str, limit: int = 5) -> list[str]:
    return list(
        db.scalars(
            select(ThreadsPost.content)
            .where(ThreadsPost.keyword.ilike(f"%{keyword}%"), ThreadsPost.status != "deleted")
            .order_by(ThreadsPost.id.desc())
            .limit(limit)
        )
    )


def log_click(
    db: Session,
    *,
    post_id: int,
    slug: str,
    referrer: str | None,
    user_agent: str | None,
    ip: str,
) -> None:
    db.add(
        ClickLog(
            post_id=post_id,
            slug=slug,
            source="threads",
            referrer=referrer,
            user_agent=user_agent,
            ip_hash=hash_ip(ip),
        )
    )
    db.commit()


def analytics_summary(db: Session) -> AnalyticsSummary:
    total_posts = db.scalar(select(func.count()).select_from(ThreadsPost).where(ThreadsPost.status != "deleted")) or 0
    total_clicks = db.scalar(select(func.count()).select_from(ClickLog)) or 0

    def count_status(status: str) -> int:
        return db.scalar(select(func.count()).select_from(ThreadsPost).where(ThreadsPost.status == status)) or 0

    rows = db.execute(
        select(ClickLog.post_id, ThreadsPost.keyword, func.count(ClickLog.id).label("clicks"))
        .join(ThreadsPost, ThreadsPost.id == ClickLog.post_id)
        .group_by(ClickLog.post_id, ThreadsPost.keyword)
        .order_by(func.count(ClickLog.id).desc())
        .limit(5)
    ).all()

    return AnalyticsSummary(
        total_posts=total_posts,
        draft=count_status("draft"),
        needs_link=count_status("needs_link"),
        approved=count_status("approved"),
        posted=count_status("posted"),
        total_clicks=total_clicks,
        top_posts=[
            AnalyticsTopPost(post_id=int(row.post_id), keyword=str(row.keyword), clicks=int(row.clicks))
            for row in rows
        ],
    )
