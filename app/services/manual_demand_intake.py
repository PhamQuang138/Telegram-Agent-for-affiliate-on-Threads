from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import DemandAction, DemandOpportunity
from app.services.demand_comment_generator import generate_demand_comment
from app.services.demand_product_matcher import match_products_for_demand
from app.services.platform_url_parser import parse_platform_url
from app.services.purchase_intent import classify_purchase_intent


def create_manual_demand(
    text: str,
    url: str | None = None,
    platform: str = "threads",
    author_username: str | None = None,
    source_account: str | None = None,
    force: bool = False,
    intake_source: str = "telegram_manual",
) -> dict:
    clean_text = _normalize_text(text)
    if not clean_text:
        if url:
            return {"created": False, "opportunity_id": 0, "platform": platform, "intent": "", "purchase_intent_score": 0, "normalized_query": "", "matched_products_count": 0, "response_mode": "unsupported", "can_api_reply": False, "reason": "URL-only intake cannot scrape content; send text too."}
        return {"created": False, "opportunity_id": 0, "platform": platform, "intent": "", "purchase_intent_score": 0, "normalized_query": "", "matched_products_count": 0, "response_mode": "unsupported", "can_api_reply": False, "reason": "empty text"}

    parsed = parse_platform_url(url or "") if url else {"platform": platform, "normalized_url": "", "external_content_id": None, "username": None, "valid": False, "reason": "no url"}
    platform = parsed["platform"] if parsed.get("valid") else platform
    author = author_username or parsed.get("username")
    content_hash = _hash(f"{platform}:{clean_text}")

    with SessionLocal() as db:
        duplicate = _find_duplicate(db, platform, parsed.get("external_content_id"), parsed.get("normalized_url"), content_hash)
        if duplicate:
            return _preview(duplicate, created=False, reason="duplicate")

    intent = classify_purchase_intent(clean_text)
    if not force and (not intent["eligible"] or intent["purchase_intent_score"] < get_settings().demand_min_intent_score):
        return {"created": False, "opportunity_id": 0, "platform": platform, "intent": intent["intent"], "purchase_intent_score": intent["purchase_intent_score"], "normalized_query": intent["normalized_query"], "matched_products_count": 0, "response_mode": "manual_copy", "can_api_reply": False, "reason": intent["reason"]}

    products = match_products_for_demand(intent["category"], intent["normalized_query"], intent["constraints"], limit=get_settings().demand_max_links_per_comment)
    if not products:
        return {"created": False, "opportunity_id": 0, "platform": platform, "intent": intent["intent"], "purchase_intent_score": intent["purchase_intent_score"], "normalized_query": intent["normalized_query"], "matched_products_count": 0, "response_mode": "manual_copy", "can_api_reply": False, "reason": "no product match"}

    comment = generate_demand_comment({"text": clean_text, "url": parsed.get("normalized_url")}, intent, products)
    if not comment["comment"]:
        return {"created": False, "opportunity_id": 0, "platform": platform, "intent": intent["intent"], "purchase_intent_score": intent["purchase_intent_score"], "normalized_query": intent["normalized_query"], "matched_products_count": len(products), "response_mode": "manual_copy", "can_api_reply": False, "reason": "comment generation failed"}

    can_api_reply = bool(parsed.get("external_content_id") and platform == "threads")
    response_mode = "api_reply" if can_api_reply else "manual_copy"
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        opp = DemandOpportunity(
            platform=platform,
            content_type="post",
            external_content_id=parsed.get("external_content_id"),
            source_url=parsed.get("normalized_url") or None,
            author_username=author,
            source_text_excerpt=clean_text[:500],
            content_hash=content_hash,
            matched_query=intent["category"],
            intent=intent["intent"],
            purchase_intent_score=float(intent["purchase_intent_score"]),
            category=intent["category"],
            normalized_query=intent["normalized_query"],
            constraints_json=json.dumps(intent["constraints"], ensure_ascii=False),
            matched_products_json=json.dumps(products, ensure_ascii=False),
            suggested_response=comment["comment"],
            response_mode=response_mode,
            status="new",
            intake_source=intake_source,
            scan_account_name=source_account,
            created_at=now,
            expires_at=now + timedelta(hours=get_settings().demand_opportunity_ttl_hours),
        )
        db.add(opp)
        db.flush()
        db.add(DemandAction(opportunity_id=opp.id, action="created", account_name=source_account, result="ok", details="manual intake"))
        db.commit()
        db.refresh(opp)
        return _preview(opp, created=True, reason="created")


def import_demands_csv(csv_path: str) -> dict:
    path = Path(csv_path).expanduser()
    result = {"rows": 0, "created": 0, "duplicates": 0, "low_intent": 0, "no_product_match": 0, "errors": []}
    if not path.exists():
        result["errors"].append("file not found")
        return result
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for index, row in enumerate(reader):
                if index >= 100:
                    break
                text = (row.get("text") or "").strip()
                if not text:
                    continue
                result["rows"] += 1
                created = create_manual_demand(
                    text=text,
                    url=(row.get("url") or "").strip() or None,
                    platform=(row.get("platform") or "threads").strip() or "threads",
                    author_username=(row.get("author_username") or "").strip() or None,
                    intake_source="csv_import",
                )
                if created["created"]:
                    result["created"] += 1
                elif created["reason"] == "duplicate":
                    result["duplicates"] += 1
                elif created["matched_products_count"] == 0 and created["purchase_intent_score"] >= get_settings().demand_min_intent_score:
                    result["no_product_match"] += 1
                else:
                    result["low_intent"] += 1
    except Exception as exc:
        result["errors"].append(str(exc))
    return result


def _find_duplicate(db, platform: str, external_id: str | None, url: str | None, content_hash: str) -> DemandOpportunity | None:
    if external_id:
        row = db.scalar(select(DemandOpportunity).where(DemandOpportunity.platform == platform, DemandOpportunity.external_content_id == external_id))
        if row:
            return row
    if url:
        row = db.scalar(select(DemandOpportunity).where(DemandOpportunity.source_url == url))
        if row:
            return row
    return db.scalar(select(DemandOpportunity).where(DemandOpportunity.content_hash == content_hash))


def _preview(opp: DemandOpportunity, *, created: bool, reason: str) -> dict:
    return {
        "created": created,
        "opportunity_id": int(opp.id),
        "platform": opp.platform,
        "intent": opp.intent,
        "purchase_intent_score": float(opp.purchase_intent_score or 0),
        "normalized_query": opp.normalized_query,
        "matched_products_count": len(json.loads(opp.matched_products_json or "[]")),
        "response_mode": opp.response_mode,
        "can_api_reply": opp.response_mode == "api_reply",
        "reason": reason,
    }


def _normalize_text(text: str) -> str:
    return " ".join((text or "").split())


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
