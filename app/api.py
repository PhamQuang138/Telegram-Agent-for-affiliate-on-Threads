from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db, init_db
from app.services.threads_repository import get_post_by_slug, get_post_link_by_slug, log_click

app = FastAPI(title="POD Bot Tracking API")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/go/{slug}")
def redirect_tracking(slug: str, request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    post_link = get_post_link_by_slug(db, slug)
    post = post_link.post if post_link else get_post_by_slug(db, slug)
    affiliate_url = post_link.affiliate_url if post_link else (post.affiliate_url if post else None)

    if not post or not affiliate_url or post.status == "deleted":
        raise HTTPException(status_code=404, detail="Tracking link not found")

    forwarded_for = request.headers.get("x-forwarded-for", "")
    ip = forwarded_for.split(",")[0].strip() if forwarded_for else (request.client.host if request.client else "")

    log_click(
        db,
        post_id=post.id,
        slug=slug,
        referrer=request.headers.get("referer"),
        user_agent=request.headers.get("user-agent"),
        ip=ip,
    )

    return RedirectResponse(affiliate_url, status_code=302)
