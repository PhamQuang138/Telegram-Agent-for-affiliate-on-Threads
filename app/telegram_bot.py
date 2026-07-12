import time
import json
import tempfile
import re
import shlex
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import Forbidden
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from agents.threads_shopee_agent import (
    check_model_availability,
    generate_threads_engagement_draft,
    generate_threads_shopee_content,
    model_status_snapshot,
)
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import AppSetting, DemandOpportunity, ThreadsPost, ThreadsPostMetric, ThreadsReply
from app.models import AdminAffiliateLink, AffiliateProduct, DailyLinkEntry
from app.schemas import ThreadsDraftRequest
from app.services.shopee_csv_importer import import_shopee_csv, scan_shopee_csv
from app.services.content_engine import generate_content_ideas
from app.services.threads_repository import (
    add_affiliate_link,
    analytics_context,
    analytics_summary,
    catalog_link_counts,
    create_group_post,
    create_post,
    find_catalog_links,
    get_post,
    get_post_links,
    hashtags_from_json,
    list_catalog_links,
    list_posts_by_status,
    list_recent_posts,
    performance_summary,
    update_draft_content,
    update_post_metadata,
    update_status,
)
from app.services.threads_service import (
    ThreadsPostingError,
    create_post as publish_threads_post,
    create_reply as publish_threads_reply,
)
from app.services.threads_account_service import (
    ThreadsAccountError,
    get_threads_account,
    load_threads_accounts,
    select_account_for_post,
)
from app.services.learning_engine import (
    SETTING_AUTO_LEARNING,
    build_account_learning_profile,
    learning_status,
    maybe_run_auto_learning,
    set_app_setting,
    update_account_learning_profile,
    update_learned_weights,
)
from app.services.reply_suggestion_service import build_reply_suggestion
from app.services.threads_api_client import delete_thread_post, get_mentions
from app.services.threads_insights_service import (
    sync_account_insights,
    sync_all_accounts_insights,
    sync_post_insights,
    thread_stats,
)
from app.services.threads_reply_service import sync_account_replies, sync_post_replies
from app.services.threads_sync_service import sync_account_posts, sync_all_accounts_posts
from app.services.trend_service import collect_threads_keyword_snapshot, get_trending_keywords
from app.services.topic_memory import is_topic_recently_used, record_topic_usage
from app.services.feature_flags import feature_snapshot, is_feature_enabled
from app.services.daily_link_catalog import (
    add_daily_product,
    daily_stats as daily_catalog_stats,
    display_date,
    import_daily_csv,
    parse_import_date,
    recent_dates,
    recategorize_product,
    set_daily_product_active,
    short_display_date,
)
from app.services.daily_link_cleanup import cleanup_expired_daily_links
from app.services.daily_link_repository import (
    get_categories_for_date_and_type,
    get_link_types_for_date,
    get_products_for_date_type_category,
    update_product_link_type,
)
from app.services.affiliate_link_type_classifier import (
    link_type_name,
    load_link_types,
    valid_link_type_ids,
)
from app.services.product_category_classifier import category_label, load_categories, valid_category_ids
from app.services.telegram_daily_link_ui import (
    build_product_messages,
    category_keyboard,
    compact_date,
    expand_date,
    link_type_keyboard,
    pagination_keyboard,
    parse_category_callback,
    parse_page_callback,
    parse_type_callback,
)
from app.services.telegram_cta_generator import generate_telegram_cta
from app.services.admin_curated_links import (
    active_batch_for_admin as admin_active_batch_for_admin,
    active_type_counts as admin_active_type_counts,
    build_private_link_messages,
    categories_for_type as admin_categories_for_type,
    cleanup_expired_admin_links,
    close_batch as admin_close_batch,
    complete_private_request,
    create_private_request,
    get_links_for_delivery,
    get_pending_request,
    import_admin_links_csv,
    ingest_admin_message,
    is_admin as is_link_admin,
    is_configured_group,
    link_stats as admin_link_stats,
    set_batch_guide_message,
    start_batch as admin_start_batch,
    user_request_allowed,
)
from app.services.manual_demand_intake import create_manual_demand, import_demands_csv
from app.services.demand_opportunity_service import (
    approve_batch as approve_buy_batch_service,
    approve_opportunity,
    copy_opportunity,
    edit_opportunity_comment,
    get_opportunity as get_buy_opportunity,
    list_opportunities,
    opstats as opportunity_stats,
    reply_batch as reply_buy_batch_service,
    reply_opportunity,
    skip_opportunity,
)
from app.services.threads_demand_scanner import (
    build_scan_keywords,
    scan_threads_demand,
)

LINK_TYPE_CODES = {"sh": "shopee_commission", "xt": "xtra_commission", "pc": "product_commission", "ex": "exclusive_offer"}
LINK_TYPE_ID_TO_CODE = {value: key for key, value in LINK_TYPE_CODES.items()}

PENDING_UPDATES: dict[int, tuple[str, int]] = {}
PENDING_ENGAGEMENT_POSTS: dict[int, dict[str, str]] = {}
DAILY_SEND_COOLDOWNS: dict[tuple[int, str, str, int], float] = {}
STARTUP_IMPORT_DONE = False
ENGAGEMENT_PERSONA_LABELS = {
    "daily": "Doi thuong",
    "controversial": "Gay tranh cai nhe",
    "advisor": "Prompt advice",
}
ENGAGEMENT_MODE_LABELS = {
    "viral": "Cau view",
    "advice": "Cho loi khuyen",
    "ask": "Xin loi khuyen",
    "quote": "Quote/thought",
    "observation": "Observation",
}
ENGAGEMENT_MODE_STYLES = {
    "viral": "viral ragebait-lite",
    "advice": "practical advice, useful Threads thought",
    "ask": "ask for advice, relatable dilemma, soft question",
    "quote": "quote-like thought, sharp one-liner, reflective",
    "observation": "social observation, everyday insight, mildly debatable",
}

FROZEN_COMMANDS = {
    "scanthreads",
    "buyops",
    "buyop",
    "approvebuy",
    "approvebuybatch",
    "editbuy",
    "skipbuy",
    "replybuy",
    "replybuybatch",
    "adddemand",
    "adddemandtext",
    "importdemands",
    "copybuy",
    "approveandcopy",
    "syncposts",
    "syncinsights",
    "syncreplies",
    "threadstats",
    "accountperformance",
    "threadtrends",
    "mentions",
    "replysuggestions",
    "ideas",
    "ideadrafts",
    "trenddrafts",
    "trends",
    "performance",
    "opstats",
}


def _db() -> Session:
    return SessionLocal()


def _user_id(update: Update) -> int | None:
    return update.effective_user.id if update.effective_user else None


def _engagement_persona_name(persona: str) -> str:
    return ENGAGEMENT_PERSONA_LABELS.get(persona, ENGAGEMENT_PERSONA_LABELS["daily"])


def _engagement_mode_name(mode: str) -> str:
    return ENGAGEMENT_MODE_LABELS.get(mode, ENGAGEMENT_MODE_LABELS["viral"])


def _engagement_mode_style(mode: str) -> str:
    return ENGAGEMENT_MODE_STYLES.get(mode, ENGAGEMENT_MODE_STYLES["viral"])


def _is_shopee_link(text: str) -> bool:
    try:
        host = urlparse(text.strip()).hostname or ""
    except ValueError:
        return False
    return host == "s.shopee.vn" or host.endswith(".shopee.vn") or host == "shopee.vn"


def _hashtags(post: ThreadsPost) -> str:
    return " ".join(f"#{tag.lstrip('#')}" for tag in hashtags_from_json(post.hashtags))


def _metadata_cta(**items: str) -> str:
    return "__meta__" + json.dumps(items, ensure_ascii=False)


def _pending_engagement_key(user_id: int) -> str:
    return f"pending_engagement:{user_id}"


def _pending_importlinks_key(user_id: int) -> str:
    return f"pending_importlinks:{user_id}"


def _save_pending_importlinks(user_id: int, ttl_seconds: int = 900, forced_link_type_id: str = "") -> None:
    with _db() as db:
        key = _pending_importlinks_key(user_id)
        setting = db.get(AppSetting, key)
        payload = {"expires_at": str(int(time.time()) + ttl_seconds), "forced_link_type_id": forced_link_type_id}
        value = json.dumps(payload, ensure_ascii=False)
        now = datetime.now().isoformat()
        if setting:
            setting.value = value
            setting.updated_at = now
        else:
            db.add(AppSetting(key=key, value=value, updated_at=now))
        db.commit()


def _load_pending_importlinks(user_id: int) -> dict[str, str] | None:
    with _db() as db:
        setting = db.get(AppSetting, _pending_importlinks_key(user_id))
        if not setting:
            return None
        try:
            payload = json.loads(setting.value)
            expires_at = int(payload.get("expires_at", 0))
        except (TypeError, ValueError, json.JSONDecodeError):
            db.delete(setting)
            db.commit()
            return None
        if expires_at < int(time.time()):
            db.delete(setting)
            db.commit()
            return None
        return {str(key): str(value) for key, value in payload.items()}


def _clear_pending_importlinks(user_id: int) -> None:
    with _db() as db:
        setting = db.get(AppSetting, _pending_importlinks_key(user_id))
        if setting:
            db.delete(setting)
            db.commit()


def _parse_importlinks_forced_type(args: list[str] | tuple[str, ...]) -> str:
    if not args:
        return ""
    raw = " ".join(args).strip().lower().replace("-", "_")
    aliases = {
        "docquyen": "exclusive_offer",
        "doc_quyen": "exclusive_offer",
        "exclusive": "exclusive_offer",
        "exclusive_offer": "exclusive_offer",
        "uu_dai_doc_quyen": "exclusive_offer",
        "xtra": "xtra_commission",
        "shopee": "shopee_commission",
        "product": "product_commission",
        "san_pham": "product_commission",
    }
    if raw in aliases:
        return aliases[raw]
    if raw in valid_link_type_ids():
        return raw
    return ""


def _save_pending_engagement(user_id: int, payload: dict[str, str]) -> None:
    with _db() as db:
        key = _pending_engagement_key(user_id)
        setting = db.get(AppSetting, key)
        value = json.dumps(payload, ensure_ascii=False)
        now = datetime.now().isoformat()
        if setting:
            setting.value = value
            setting.updated_at = now
        else:
            db.add(AppSetting(key=key, value=value, updated_at=now))
        db.commit()


def _load_pending_engagement(user_id: int) -> dict[str, str] | None:
    with _db() as db:
        setting = db.get(AppSetting, _pending_engagement_key(user_id))
        if not setting:
            return None
        try:
            value = json.loads(setting.value)
        except json.JSONDecodeError:
            return None
        return {str(key): str(val) for key, val in value.items()}


def _clear_pending_engagement(user_id: int) -> None:
    with _db() as db:
        setting = db.get(AppSetting, _pending_engagement_key(user_id))
        if setting:
            db.delete(setting)
            db.commit()


def _post_meta(post: ThreadsPost) -> dict[str, str]:
    if not post.cta.startswith("__meta__"):
        return {}
    try:
        value = json.loads(post.cta.removeprefix("__meta__"))
    except json.JSONDecodeError:
        return {}
    return {str(key): str(val) for key, val in value.items()}


def _post_account_payload(post: ThreadsPost) -> dict:
    return {
        "keyword": post.keyword,
        "product_name": post.product_name,
        "persona": post.persona,
        "persona_id": post.persona_id,
        "angle": post.angle,
        "content_type": post.content_type,
        "content_goal": post.content_goal,
        "content": post.content,
    }


def _public_cta(post: ThreadsPost) -> str:
    return "" if post.cta.startswith("__meta__") else post.cta


def _preview(post: ThreadsPost) -> str:
    link_target = get_settings().comment_link_target.lower().strip()
    tracking = post.affiliate_url if link_target == "affiliate" else post.tracking_url
    tracking = tracking or "chưa có link Shopee"
    with _db() as db:
        links = get_post_links(db, post.id)

    if links:
        tracking = "\n".join(
            f"{index}. {link.product_name}: {link.affiliate_url if link_target == 'affiliate' else link.tracking_url}"
            for index, link in enumerate(links, start=1)
        )

    return f"""Threads Shopee Draft #{post.id}

Nội dung:
{post.content}

Hashtags:
{_hashtags(post)}

Link sẽ dùng:
{tracking}

Status:
{post.status}

Lệnh:
- /approve {post.id}
- /post {post.id}
- /view {post.id}
- /regenerate {post.id}
- /delete {post.id}"""


def _thread_text(post: ThreadsPost) -> str:
    parts = [post.content.strip(), _public_cta(post).strip(), _hashtags(post).strip()]
    if get_settings().include_tracking_link_in_threads and post.tracking_url:
        parts.insert(2, f"Link tham khảo: {post.tracking_url}")
    return "\n\n".join(part for part in parts if part)


def _manual_support_cta() -> str:
    settings = get_settings()
    telegram_url = (settings.threads_telegram_group_url or settings.telegram_group_invite_url).strip()
    if not telegram_url:
        raise ValueError("Chua cau hinh THREADS_TELEGRAM_GROUP_URL hoac TELEGRAM_GROUP_INVITE_URL.")
    return (
        "Nếu thấy hay mọi người có thể ủng hộ mình qua nhóm này nha, mỗi ngày channel đều cập nhật các link sản phẩm giá tốt "
        "theo từng danh mục để mọi người dễ tìm. Chỉ cần bấm chọn mục mình quan tâm, bot sẽ gửi riêng danh sách link tương ứng cho bạn.\n"
        f"{telegram_url}"
    )


def _parse_manual_threadpost_args(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple[str, str]:
    args = list(context.args or [])
    account_name = ""
    account_names = {str(account.get("name") or "").lower() for account in load_threads_accounts() if account.get("name")}
    if args and args[0].lower() in account_names:
        account_name = args.pop(0)
    content = " ".join(args).strip()
    if not content and update.message and update.message.reply_to_message:
        content = (update.message.reply_to_message.text or update.message.reply_to_message.caption or "").strip()
    return account_name, content


def _short_product_name(name: str, limit: int = 120) -> str:
    clean = " ".join(name.split())
    return clean if len(clean) <= limit else clean[: limit - 3].rstrip() + "..."


def _reply_link_texts(post: ThreadsPost) -> list[str]:
    link_target = get_settings().comment_link_target.lower().strip()
    with _db() as db:
        links = get_post_links(db, post.id)

    if links:
        return [_build_bundled_reply(post, links, link_target)]

    url = post.affiliate_url if link_target == "affiliate" else post.tracking_url
    return [f"{_reply_links_intro(post)}\n\n{url}"]


def _build_bundled_reply(post: ThreadsPost, links: list, link_target: str, limit: int = 500) -> str:
    intro = _reply_links_intro(post)

    for count in (4, 3):
        for name_limit in (46, 28, 0):
            lines = [intro]
            for index, link in enumerate(links[:count], start=1):
                url = link.affiliate_url if link_target == "affiliate" else link.tracking_url
                if len(url) > 90 and link.tracking_url:
                    url = link.tracking_url
                if name_limit > 0:
                    lines.append(f"{index}. {_short_product_name(link.product_name, name_limit)}\n{url}")
                else:
                    lines.append(f"{index}. {url}")

            text = "\n\n".join(lines)
            if len(text) <= limit:
                return text

    compact_lines = ["Đây là vài link mình gom lại cho mọi người dễ mở:"]
    for index, link in enumerate(links[:3], start=1):
        url = link.tracking_url or link.affiliate_url
        compact_lines.append(f"{index}. {url}")
    return "\n".join(compact_lines)[:limit]


def _reply_links_intro(post: ThreadsPost) -> str:
    if post.status == "engagement":
        return "Đây là vài link liên quan mình gom lại cho mọi người dễ mở hơn."

    product_text = f"{post.keyword} {post.product_name}".lower()
    if any(word in product_text for word in ["áo", "quần", "khoác", "hoodie", "jean", "tóc", "kính"]):
        return "Đây là vài món đồ mình đã nhắc, gom lại cho mọi người dễ xem hơn."
    if any(word in product_text for word in ["bàn", "laptop", "chuột", "kệ", "dây sạc", "mực in"]):
        return "Đây là vài món cho góc làm việc mình đã nhắc, ai đang cần thì xem cho tiện."
    if any(word in product_text for word in ["gym", "thể thao", "bóng đá", "yoga", "tạ", "pickleball"]):
        return "Đây là vài món tập luyện/thể thao mình đã nhắc, gom lại cho mọi người tiện mở."

    return "Đây là vài món mình đã nhắc, gom lại cho mọi người dễ xem hơn."


def _has_any_tracking_link(db: Session, post: ThreadsPost) -> bool:
    return bool(post.tracking_url or get_post_links(db, post.id))


def _parse_csv_update_args(args: list[str]) -> tuple[str, int]:
    text = " ".join(args).strip().strip('"')
    group_size = 5

    if not text:
        return "", group_size

    parts = text.rsplit(" ", 1)
    if len(parts) == 2 and parts[1].isdigit():
        text = parts[0].strip().strip('"')
        group_size = int(parts[1])

    return text, max(1, min(6, group_size))


def _chunk_links(items: list, size: int = 5) -> list[list]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _product_summary_from_links(links: list) -> str:
    return "\n".join(
        f"{index}. {link.product_name}"
        for index, link in enumerate(links, start=1)
    )


def _post_link_payloads(links: list) -> list[dict[str, str]]:
    return [
        {
            "product_name": link.product_name,
            "affiliate_url": link.affiliate_url,
            "product_url": link.product_url or "",
            "price": link.price or "",
            "shop_name": link.shop_name or "",
        }
        for link in links
    ]


def _draft_request_from_links(keyword: str, product_name: str, matched_links: list) -> ThreadsDraftRequest:
    first_link = matched_links[0] if matched_links else None
    return ThreadsDraftRequest(
        keyword=keyword,
        product_name=product_name,
        affiliate_url=first_link.affiliate_url if len(matched_links) == 1 else "",
        product_url=first_link.product_url if len(matched_links) == 1 else "",
        price=first_link.price if len(matched_links) == 1 else "",
        shop_name=first_link.shop_name if len(matched_links) == 1 else "",
        style="viral product-native",
    )


def _create_content_draft_from_keyword(db: Session, keyword: str, idea_context: dict | None = None) -> ThreadsPost:
    matched_links = find_catalog_links(db, keyword, limit=5)
    product_name = keyword
    if matched_links:
        product_name = "\n".join(
            f"{index}. {link.product_name}"
            for index, link in enumerate(matched_links, start=1)
        )

    draft, metadata = generate_threads_shopee_content(
        db,
        _draft_request_from_links(keyword, product_name, matched_links),
        idea_context=idea_context,
    )
    if len(matched_links) >= 2:
        post = create_group_post(
            db,
            keyword=keyword,
            product_name=product_name,
            draft=draft,
            links=_post_link_payloads(matched_links),
            status="draft",
            metadata=metadata,
        )
        record_topic_usage(keyword, [link.id for link in matched_links], post.id)
        return post
    if len(matched_links) == 1:
        post = create_post(
            db,
            keyword=keyword,
            product_name=product_name,
            affiliate_url=matched_links[0].affiliate_url,
            draft=draft,
            status="draft",
            metadata=metadata,
        )
        record_topic_usage(keyword, [matched_links[0].id], post.id)
        return post
    post = create_post(
        db,
        keyword=keyword,
        product_name=product_name,
        affiliate_url=None,
        draft=draft,
        status="needs_link",
        metadata=metadata,
    )
    record_topic_usage(keyword, [], post.id)
    return post


def _import_limit() -> int | None:
    limit = get_settings().import_generate_limit
    return limit if limit > 0 else None


def _queue_status_text(db: Session) -> str:
    summary = analytics_summary(db)
    link_counts = catalog_link_counts(db)
    engagement = len(list_posts_by_status(db, "engagement"))
    recent = list_recent_posts(db, limit=5)
    recent_text = "\n".join(
        f"#{post.id} | {post.status} | {post.keyword}"
        for post in recent
    ) or "Queue trong."

    return (
        "Trang thai queue:\n"
        f"- draft: {summary.draft}\n"
        f"- needs_link: {summary.needs_link}\n"
        f"- approved: {summary.approved}\n"
        f"- posted: {summary.posted}\n"
        f"- engagement: {engagement}\n\n"
        "Links trong DB:\n"
        f"- total: {link_counts['total_links']}\n"
        f"- unique: {link_counts['unique_links']}\n"
        f"- posts co links: {link_counts['posts_with_links']}\n\n"
        f"Gan day:\n{recent_text}"
    )


async def _reply_status(update: Update) -> None:
    with _db() as db:
        await update.message.reply_text(_queue_status_text(db))


def _model_row_text(row: dict) -> str:
    provider = str(row.get("provider", "unknown"))
    status = str(row.get("status", "unknown"))
    cooldown = int(row.get("cooldown_seconds", 0) or 0)
    ready_at = int(row.get("ready_at", 0) or 0)
    detail = str(row.get("detail", "") or "")
    latency = row.get("latency_ms")

    suffix = ""
    if cooldown:
        ready_text = datetime.fromtimestamp(ready_at).strftime("%Y-%m-%d %H:%M:%S") if ready_at else "unknown"
        suffix = f" (~{cooldown}s, ready_at {ready_text})"
        if detail:
            suffix += f" - {detail}"
    elif latency is not None:
        suffix = f" ({latency}ms)"
    elif detail:
        suffix = f" - {detail}"

    return f"- {provider}: {status}{suffix}"


async def modelstatus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    rows = model_status_snapshot()
    provider_text = "\n".join(_model_row_text(row) for row in rows) or "Chua co provider nao."
    await update.message.reply_text(
        "Model status hien tai:\n"
        f"Order: {settings.ai_provider_order}\n"
        f"{provider_text}\n\n"
        "Dung /checkmodels [limit] neu muon test that tung model. Lenh nay se ton request quota."
    )


async def checkmodels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    limit = None
    if context.args:
        try:
            limit = max(1, min(20, int(context.args[0])))
        except ValueError:
            await update.message.reply_text("Dung: /checkmodels [limit]")
            return

    await update.message.reply_text("Dang check model bang prompt rat nhe... Lenh nay co ton quota.")
    rows = check_model_availability(limit=limit)
    provider_text = "\n".join(_model_row_text(row) for row in rows) or "Chua co provider nao."
    await update.message.reply_text(f"Ket qua check model:\n{provider_text}")


def run_startup_import_once() -> None:
    global STARTUP_IMPORT_DONE

    if STARTUP_IMPORT_DONE:
        return

    STARTUP_IMPORT_DONE = True
    settings = get_settings()
    csv_path = settings.startup_import_csv_path.strip()
    if not csv_path:
        return

    path = Path(csv_path).expanduser()
    if not path.exists():
        print(f"Startup import skipped: CSV file not found: {path}")
        return

    limit = settings.startup_generate_limit if settings.startup_generate_limit > 0 else 2
    try:
        with _db() as db:
            result = import_shopee_csv(db, path, limit=limit)
        print(
            "Startup import done. "
            f"Created: {result.created}, skipped: {result.skipped}, limit: {limit}"
        )
    except Exception as exc:
        print(f"Startup import failed: {exc}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        """AI Opportunity Engine MVP:
/adddemand <url> <nội dung bài>
/adddemandtext <nội dung bài>
/importdemands <csv_path>
/buyops [limit]
/buyop <id>
/approvebuy <id>
/approvebuybatch <id1,id2,id3>
/editbuy <id> <comment mới>
/skipbuy <id>
/copybuy <id>
/approveandcopy <id>
/replybuy <id> [account_name]
/replybuybatch <id1,id2,id3> [account_name]
/opstats
/features

Workflow cũ vẫn dùng được:
/threads_shopee <keyword hoặc Shopee affiliate link>
/updatelink <csv_path> [group_size] - quét CSV, chưa import
/confirmupdate - nhập các link vừa quét vào queue
/cancelupdate - hủy lần quét CSV hiện tại
/status
/queue
/autodrafts [limit] [keyword]
/contentdraft <keyword>
/trends
/trenddrafts [limit hoặc keyword]
/ideadrafts [limit] [keyword]
/performance
/learn
/autolearn on|off
/ideas [keyword]
/accounts
/syncposts [account_name]
/syncinsights [account_name]
/syncreplies [account_name]
/threadstats <post_id>
/accountperformance <account_name>
/threadtrends <keyword>
/mentions [account_name]
/replysuggestions <post_id>
/scanthreads [keyword] [account_name]
/engagepost <topic>
/view <post_id>
/regenerate <post_id>
/refreshdrafts [limit]
/modelstatus
/checkmodels [limit]
/approve <post_id>
/post <post_id> [account_name]
/threadpost [account_name] <noi dung>
/replylinks <post_id>
/delete_thread <post_id> confirm
/delete <post_id>
/analytics"""
    )


async def importcsv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    path, group_size = _parse_csv_update_args(context.args)

    if not path:
        await update.message.reply_text("Dùng: /importcsv <csv_path> [group_size 1-6]")
        return

    await update.message.reply_text(f"Đang import CSV Shopee vào queue... group_size={group_size}")

    try:
        with _db() as db:
            result = import_shopee_csv(db, path, limit=_import_limit(), group_size=group_size)
    except Exception as exc:
        await update.message.reply_text(f"Import CSV lỗi: {exc}")
        return

    message = f"Import xong.\nBài mới: {result.created}\nLink trùng/không hợp lệ đã bỏ qua: {result.skipped}"
    if result.errors:
        message += "\nLỗi:\n" + "\n".join(f"- {error}" for error in result.errors[:5])
    await update.message.reply_text(message)
    await _reply_status(update)


async def updatelink(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = _user_id(update)
    if user_id is None:
        return

    path, group_size = _parse_csv_update_args(context.args)

    if not path:
        await update.message.reply_text("Dùng: /updatelink <csv_path> [group_size 1-6]")
        return

    try:
        with _db() as db:
            scan = scan_shopee_csv(db, path)
    except Exception as exc:
        await update.message.reply_text(f"Quét CSV lỗi: {exc}")
        return

    PENDING_UPDATES[user_id] = (path, group_size)
    preview_rows = scan.product_rows[:10]
    preview = "\n".join(
        f"{index}. {row['product_name']} | {row.get('price') or 'chưa có giá'} | {row['affiliate_url']}"
        for index, row in enumerate(preview_rows, start=1)
    )
    if not preview:
        preview = "Không tìm thấy link sản phẩm mới."

    estimated_posts = (len(scan.product_rows) + group_size - 1) // group_size + len(scan.campaign_rows)
    capped_posts = min(estimated_posts, _import_limit()) if _import_limit() else estimated_posts
    await update.message.reply_text(
        f"Quét CSV xong.\n"
        f"Link mới: {scan.new_links}\n"
        f"Link trùng/không hợp lệ đã bỏ qua: {scan.skipped}\n"
        f"Số link mỗi bài: {group_size}\n"
        f"Dự kiến tạo bài mới: {capped_posts}/{estimated_posts}\n\n"
        f"Xem trước {len(preview_rows)} link đầu:\n{preview}\n\n"
        f"Gửi /confirmupdate để import, hoặc /cancelupdate để hủy."
    )


async def confirmupdate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = _user_id(update)
    if user_id is None:
        return

    pending = PENDING_UPDATES.get(user_id)
    if not pending:
        await update.message.reply_text("Chưa có CSV nào đang chờ duyệt. Hãy chạy /updatelink <csv_path> trước.")
        return

    path, group_size = pending
    await update.message.reply_text(f"Đang import các link đã duyệt... group_size={group_size}")

    try:
        with _db() as db:
            result = import_shopee_csv(db, path, limit=_import_limit(), group_size=group_size)
    except Exception as exc:
        await update.message.reply_text(f"Import lỗi: {exc}")
        return

    PENDING_UPDATES.pop(user_id, None)
    message = f"Import xong.\nBài mới: {result.created}\nLink trùng/không hợp lệ đã bỏ qua: {result.skipped}"
    if result.errors:
        message += "\nLỗi:\n" + "\n".join(f"- {error}" for error in result.errors[:5])
    await update.message.reply_text(message)
    await _reply_status(update)


async def cancelupdate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = _user_id(update)
    if user_id is None:
        return
    PENDING_UPDATES.pop(user_id, None)
    await update.message.reply_text("Đã hủy lần quét CSV hiện tại.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply_status(update)


async def autodrafts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    limit = _import_limit() or 2
    keyword = " ".join(context.args).strip()

    if context.args and context.args[0].isdigit():
        limit = max(1, min(10, int(context.args[0])))
        keyword = " ".join(context.args[1:]).strip()

    needed_links = limit * 5
    await update.message.reply_text(
        f"Dang duyet catalog de tao toi da {limit} draft..."
        + (f"\nKeyword: {keyword}" if keyword else "")
    )

    created_posts: list[int] = []

    with _db() as db:
        catalog_links = (
            find_catalog_links(db, keyword, limit=needed_links)
            if keyword
            else list_catalog_links(db, limit=needed_links)
        )

        groups = [
            group
            for group in _chunk_links(catalog_links, 5)
            if len(group) >= 3
        ][:limit]

        if not groups:
            await update.message.reply_text(
                "Chua du link phu hop de tao bai tu dong. Can it nhat 3 link trong catalog."
            )
            return

        for group in groups:
            post_keyword = keyword or f"list {len(group)} mon Shopee dang xem"
            product_name = _product_summary_from_links(group)
            draft, metadata = generate_threads_shopee_content(
                db,
                ThreadsDraftRequest(
                    keyword=post_keyword,
                    product_name=product_name,
                    style="viral product-native",
                ),
            )
            post = create_group_post(
                db,
                keyword=post_keyword,
                product_name=product_name,
                draft=draft,
                links=_post_link_payloads(group),
                status="draft",
                metadata=metadata,
            )
            created_posts.append(post.id)

    await update.message.reply_text(
        "Tao draft tu catalog xong.\n"
        f"Bai moi: {', '.join(f'#{post_id}' for post_id in created_posts)}\n"
        "Dung /queue hoac /view <post_id> de xem."
    )
    await _reply_status(update)


async def engagepost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = _user_id(update)
    if user_id is None:
        return

    topic = " ".join(context.args).strip()
    if not topic:
        await update.message.reply_text("Dung: /engagepost <topic>\nVi du: /engagepost ban lam viec bua")
        return

    _save_pending_engagement(user_id, {"topic": topic, "persona": "daily"})
    await update.message.reply_text(
        "Chon persona cho bai cau view nay:",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Doi thuong", callback_data="engage_persona:daily"),
                    InlineKeyboardButton("Gay tranh cai nhe", callback_data="engage_persona:controversial"),
                ],
                [
                    InlineKeyboardButton("Prompt advice", callback_data="engage_persona:advisor"),
                ]
            ]
        ),
    )


async def choose_engagement_persona(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await query.answer()
    user_id = query.from_user.id if query.from_user else None
    if user_id is None:
        return

    pending = _load_pending_engagement(user_id)
    if not pending:
        await query.message.reply_text("Khong con bai cau view nao dang cho chon persona. Hay gui lai /engagepost <topic>.")
        return

    persona = (query.data or "").split(":", 1)[1]
    if persona not in ENGAGEMENT_PERSONA_LABELS:
        persona = "daily"
    pending["persona"] = persona
    _save_pending_engagement(user_id, pending)

    persona_name = _engagement_persona_name(persona)
    await query.edit_message_text(
        f"Persona: {persona_name}\nChon dang bai:",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Cau view", callback_data="engage_mode:viral"),
                    InlineKeyboardButton("Cho loi khuyen", callback_data="engage_mode:advice"),
                ],
                [
                    InlineKeyboardButton("Xin loi khuyen", callback_data="engage_mode:ask"),
                    InlineKeyboardButton("Quote/thought", callback_data="engage_mode:quote"),
                ],
                [
                    InlineKeyboardButton("Observation", callback_data="engage_mode:observation"),
                ],
            ]
        ),
    )


async def choose_engagement_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await query.answer()
    user_id = query.from_user.id if query.from_user else None
    if user_id is None:
        return

    pending = _load_pending_engagement(user_id)
    if not pending:
        await query.message.reply_text("Khong con bai cau view nao dang cho chon dang bai. Hay gui lai /engagepost <topic>.")
        return

    mode = (query.data or "").split(":", 1)[1]
    if mode not in ENGAGEMENT_MODE_LABELS:
        mode = "viral"
    pending["mode"] = mode

    persona_name = _engagement_persona_name(pending.get("persona", "daily"))
    mode_name = _engagement_mode_name(mode)
    await query.edit_message_text(f"Dang tao bai {mode_name.lower()} persona {persona_name.lower()}...")
    _clear_pending_engagement(user_id)

    with _db() as db:
        draft = generate_threads_engagement_draft(
            db,
            pending.get("topic", ""),
            style=_engagement_mode_style(mode),
            persona=pending.get("persona", "daily"),
        )
        draft.cta = _metadata_cta(persona=pending.get("persona", "daily"), mode=mode, style=_engagement_mode_style(mode))
        post = create_post(
            db,
            keyword=pending.get("topic", ""),
            product_name="engagement-only",
            affiliate_url=None,
            draft=draft,
            status="engagement",
            metadata={
                "content_type": "engagement",
                "content_goal": "engagement",
                "persona": persona_name,
                "hook_type": mode,
            },
        )
        await query.message.reply_text(_preview(post))
        await query.message.reply_text("Bai Threads nay se khong kem link Shopee. Khi /post, bot se reply CTA dan ve Telegram group neu da cau hinh.")
        await query.message.reply_text(_queue_status_text(db))


async def choose_engagement_links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    await query.answer()
    user_id = query.from_user.id if query.from_user else None
    if user_id is None:
        return

    pending = PENDING_ENGAGEMENT_POSTS.pop(user_id, None)
    if not pending:
        await query.message.reply_text("Khong con bai cau view nao dang cho chon link. Hay gui lai /engagepost <topic>.")
        return

    topic = pending.get("topic", "")
    persona = pending.get("persona", "daily")
    mode = pending.get("mode", "viral")
    style = _engagement_mode_style(mode)
    include_links = query.data == "engage_links:yes"
    persona_name = _engagement_persona_name(persona).lower()
    mode_name = _engagement_mode_name(mode).lower()
    await query.edit_message_text(
        f"Dang tao bai {mode_name} persona {persona_name}..."
        + (" Co gan link random o comment." if include_links else " Khong gan link.")
    )

    with _db() as db:
        draft = generate_threads_engagement_draft(
            db,
            topic,
            style=style,
            persona=persona,
        )
        draft.cta = _metadata_cta(persona=persona, mode=mode, style=style)
        selected_links = []
        if include_links:
            catalog_links = list_catalog_links(db, limit=50)
            link_count = min(len(catalog_links), random.randint(2, 3))
            selected_links = random.sample(catalog_links, link_count) if link_count else []

        if selected_links:
            post = create_group_post(
                db,
                keyword=topic,
                product_name="random engagement links",
                draft=draft,
                links=_post_link_payloads(selected_links),
                status="engagement",
                metadata={"content_type": "engagement", "content_goal": "engagement", "persona": persona_name, "hook_type": mode},
            )
        else:
            post = create_post(
                db,
                keyword=topic,
                product_name="engagement-only",
                affiliate_url=None,
                draft=draft,
                status="engagement",
                metadata={"content_type": "engagement", "content_goal": "engagement", "persona": persona_name, "hook_type": mode},
            )
        await query.message.reply_text(_preview(post))
        if selected_links:
            await query.message.reply_text(f"Da gan {len(selected_links)} link random de comment khi /post.")
        elif include_links:
            await query.message.reply_text("Chua co link trong catalog nen bai nay se dang khong kem comment link.")
        else:
            await query.message.reply_text("Bai cau view nay se dang khong kem comment link.")
        await query.message.reply_text(_queue_status_text(db))


async def contentdraft(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyword = " ".join(context.args).strip()
    if not keyword:
        await update.message.reply_text("Dung: /contentdraft <keyword>")
        return

    await update.message.reply_text("Dang tao draft bang AI Affiliate Content Engine...")
    with _db() as db:
        post = _create_content_draft_from_keyword(db, keyword)
        await update.message.reply_text(_preview(post))
        await _reply_status(update)


async def trends(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with _db() as db:
        items = get_trending_keywords(db, limit=10)
    if not items:
        await update.message.reply_text("Chua co trend nao.")
        return
    lines = [
        f"{index}. {item['keyword']} - {item['trend_score']}/100\n   {item['reason']}"
        for index, item in enumerate(items, start=1)
    ]
    await update.message.reply_text("Top trend keywords:\n" + "\n".join(lines))


async def ideas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyword = " ".join(context.args).strip()
    with _db() as db:
        if not keyword:
            trend_items = get_trending_keywords(db, limit=1)
            keyword = trend_items[0]["keyword"] if trend_items else "đồ tiện ích"
        links = find_catalog_links(db, keyword, limit=5)
        products = _post_link_payloads(links)
        ideas_list = generate_content_ideas(keyword, products, analytics_context(db), count=3)

    blocks = []
    for index, item in enumerate(ideas_list, start=1):
        product_names = [
            _short_product_name(product.get("product_name", ""), 70)
            for product in item.get("selected_products", [])
            if product.get("product_name")
        ]
        blocks.append(
            f"{index}. Need: {item['need']}\n"
            f"Persona: {item['persona']}\n"
            f"Angle: {item['angle']}\n"
            f"Hook: {item['hook']}\n"
            f"Idea: {item['idea']}\n"
            f"San pham hop: {', '.join(product_names) if product_names else 'chua co link phu hop'}"
        )

    await update.message.reply_text(f"Ideas cho keyword: {keyword}\n\n" + "\n\n".join(blocks))


async def ideadrafts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    limit = 2
    keyword = ""
    if args:
        if args[0].isdigit():
            limit = max(1, min(3, int(args[0])))
            keyword = " ".join(args[1:]).strip()
        else:
            keyword = " ".join(args).strip()
            limit = 1

    await update.message.reply_text(
        "Dang tao draft tu idea seed..."
        + (f"\nKeyword: {keyword}" if keyword else f"\nSo luong: {limit}")
    )
    created_posts: list[int] = []
    with _db() as db:
        if not keyword:
            trend_items = get_trending_keywords(db, limit=limit * 3)
            keywords = [item["keyword"] for item in trend_items[:limit]] or ["đồ tiện ích"]
        else:
            keywords = [keyword]

        for current_keyword in keywords[:limit]:
            links = find_catalog_links(db, current_keyword, limit=5)
            ideas_list = generate_content_ideas(
                current_keyword,
                _post_link_payloads(links),
                analytics_context(db),
                count=1,
            )
            idea = ideas_list[0] if ideas_list else {}
            post = _create_content_draft_from_keyword(db, current_keyword, idea_context=idea)
            created_posts.append(post.id)

    await update.message.reply_text(
        "Tao idea drafts xong.\n"
        f"Bai moi: {', '.join(f'#{post_id}' for post_id in created_posts) if created_posts else 'khong co'}"
    )
    await _reply_status(update)


async def trenddrafts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    limit = 2
    forced_keyword = ""
    if args:
        if args[0].isdigit():
            limit = max(1, min(5, int(args[0])))
            forced_keyword = " ".join(args[1:]).strip()
        else:
            forced_keyword = " ".join(args).strip()
            limit = 1

    await update.message.reply_text(
        "Dang tao draft tu trend..."
        + (f"\nKeyword ep: {forced_keyword}" if forced_keyword else f"\nSo luong: {limit}")
    )
    created_posts: list[int] = []
    with _db() as db:
        if forced_keyword:
            keywords = [forced_keyword]
        else:
            keywords = []
            for item in get_trending_keywords(db, limit=limit * 4):
                if not is_topic_recently_used(item["keyword"]):
                    keywords.append(item["keyword"])
                if len(keywords) >= limit:
                    break
        for keyword in keywords[:limit]:
            links = find_catalog_links(db, keyword, limit=5)
            ideas_list = generate_content_ideas(keyword, _post_link_payloads(links), analytics_context(db), count=1)
            post = _create_content_draft_from_keyword(db, keyword, idea_context=ideas_list[0] if ideas_list else None)
            created_posts.append(post.id)

    await update.message.reply_text(
        "Tao trend drafts xong.\n"
        f"Bai moi: {', '.join(f'#{post_id}' for post_id in created_posts) if created_posts else 'khong co'}"
    )
    await _reply_status(update)


async def performance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with _db() as db:
        summary = performance_summary(db)
    learn_status = learning_status()

    def rows_text(title: str, rows: list[dict]) -> str:
        if not rows:
            return f"{title}: chua co du lieu"
        return title + ":\n" + "\n".join(
            f"- {row['name']}: {row['clicks']} clicks / {row['posts']} posts"
            for row in rows
        )

    best = (
        summary["personas"][0]["name"]
        if summary.get("personas")
        else "persona dang co click tot"
    )
    weak = (
        summary["bottom_hook_types"][0]["name"]
        if summary.get("bottom_hook_types")
        else "hook lap lai"
    )

    await update.message.reply_text(
        "\n\n".join(
            [
                "Threads synced metrics:\n"
                f"- synchronized posts: {summary['threads']['synchronized_posts']}\n"
                f"- total views: {summary['threads']['total_views']}\n"
                f"- total replies: {summary['threads']['total_replies']}\n"
                f"- affiliate clicks: {summary['threads']['total_affiliate_clicks']}\n"
                f"- avg CTR: {_pct(summary['threads']['average_affiliate_ctr'])}\n"
                f"- avg engagement: {_pct(summary['threads']['average_engagement_rate'])}\n"
                f"- stored replies: {summary['threads']['stored_replies']}",
                rows_text("Persona", summary["personas"]),
                rows_text("Angle", summary["angles"]),
                rows_text("Hook type", summary["hook_types"]),
                rows_text("Keyword", summary["keywords"]),
                rows_text("Product", summary["products"]),
                rows_text("Diversity key", summary["diversity_keys"]),
                rows_text("Bottom persona", summary["bottom_personas"]),
                rows_text("Bottom angle", summary["bottom_angles"]),
                rows_text("Bottom hook", summary["bottom_hook_types"]),
                "Learning status:\n"
                f"- auto learning: {'on' if learn_status['auto_learning_enabled'] else 'off'}\n"
                f"- last run: {learn_status['last_learning_run_at'] or 'chua co'}\n"
                f"- sample size: {learn_status['sample_size']}\n"
                f"- weights updated: {learn_status['updated_at'] or 'chua co'}",
                f"Goi y: nen viet them theo vibe '{best}', va giam bot dang hook '{weak}' neu no co nhieu bai nhung it click.",
            ]
        )
    )


async def learn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_feature_enabled("learning_engine"):
        await update.message.reply_text("Learning engine đang đóng băng trong MVP.")
        return
    result = update_learned_weights(min_posts=10, lookback_days=30)
    top_persona = result.get("top_personas", [{}])[0].get("name", "chua co") if result.get("top_personas") else "chua co"
    weak_persona = result.get("weak_personas", [{}])[0].get("name", "chua co") if result.get("weak_personas") else "chua co"
    top_angle = result.get("top_angles", [{}])[0].get("name", "chua co") if result.get("top_angles") else "chua co"
    weak_angle = result.get("weak_angles", [{}])[0].get("name", "chua co") if result.get("weak_angles") else "chua co"
    top_hook = result.get("top_hook_types", [{}])[0].get("name", "chua co") if result.get("top_hook_types") else "chua co"
    recommendations = "\n".join(f"- {item}" for item in result.get("recommendations", [])) or "- chua co"
    await update.message.reply_text(
        "Learning update:\n"
        f"- enough_data: {result.get('enough_data')}\n"
        f"- sample size: {result.get('total_posts')}\n"
        f"- top persona: {top_persona}\n"
        f"- weak persona: {weak_persona}\n"
        f"- top angle: {top_angle}\n"
        f"- weak angle: {weak_angle}\n"
        f"- top hook_type: {top_hook}\n"
        f"Goi y:\n{recommendations}"
    )


async def autolearn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_feature_enabled("learning_engine"):
        await update.message.reply_text("Auto learning đang đóng băng trong MVP.")
        return
    if not context.args or context.args[0].lower() not in {"on", "off"}:
        await update.message.reply_text("Dung: /autolearn on hoặc /autolearn off")
        return
    value = context.args[0].lower()
    set_app_setting(SETTING_AUTO_LEARNING, value)
    await update.message.reply_text(f"Auto learning: {value}")


async def accounts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    accounts_list = load_threads_accounts()
    if not accounts_list:
        await update.message.reply_text("Chưa cấu hình Threads account nào.")
        return

    with _db() as db:
        rows = []
        for account in accounts_list:
            last_post = db.scalar(
                select(ThreadsPost)
                .where(ThreadsPost.posted_account_name == account["name"])
                .order_by(ThreadsPost.id.desc())
                .limit(1)
            )
            last_post_at = last_post.created_at.isoformat() if last_post and last_post.created_at else "chưa có"
            rows.append(
                "\n".join(
                    [
                        f"- {account['name']} ({account.get('display_name') or account['name']})",
                        f"  persona: {account.get('persona') or 'chưa đặt'}",
                        f"  topics: {', '.join(account.get('topics') or []) or 'chưa đặt'}",
                        f"  config: {'enabled' if account.get('enabled') else 'missing token/user_id'}",
                        f"  last_post_at: {last_post_at}",
                    ]
                )
            )

    await update.message.reply_text("Threads accounts:\n" + "\n\n".join(rows))


async def threads_shopee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Dùng: /threads_shopee <keyword hoặc Shopee affiliate link>")
        return

    await update.message.reply_text("Đang tạo draft Threads Shopee...")
    affiliate_url = text if _is_shopee_link(text) else None
    keyword = "sản phẩm Shopee" if affiliate_url else text
    product_name = "sản phẩm Shopee" if affiliate_url else keyword

    with _db() as db:
        matched_links = [] if affiliate_url else find_catalog_links(db, keyword, limit=5)
        matched_link = matched_links[0] if matched_links else None
        if matched_links:
            product_name = "\n".join(
                f"{index}. {link.product_name}"
                for index, link in enumerate(matched_links, start=1)
            )
            affiliate_url = matched_link.affiliate_url if len(matched_links) == 1 else None
            await update.message.reply_text(
                f"Da tim thay {len(matched_links)} link phu hop trong DB:\n"
                + "\n".join(
                    f"{index}. {_short_product_name(link.product_name, 80)}"
                    for index, link in enumerate(matched_links, start=1)
                )
            )

        draft, metadata = generate_threads_shopee_content(
            db,
            ThreadsDraftRequest(
                keyword=keyword,
                product_name=product_name,
                affiliate_url=affiliate_url or "",
                product_url=matched_link.product_url if len(matched_links) == 1 else "",
                price=matched_link.price if len(matched_links) == 1 else "",
                shop_name=matched_link.shop_name if len(matched_links) == 1 else "",
                style="viral product-native",
            ),
        )
        if len(matched_links) >= 2:
            post = create_group_post(
                db,
                keyword=keyword,
                product_name=product_name,
                draft=draft,
                links=[
                    {
                        "product_name": link.product_name,
                        "affiliate_url": link.affiliate_url,
                        "product_url": link.product_url or "",
                        "price": link.price or "",
                        "shop_name": link.shop_name or "",
                    }
                    for link in matched_links
                ],
                status="draft",
                metadata=metadata,
            )
        else:
            post = create_post(
                db,
                keyword=keyword,
                product_name=product_name,
                affiliate_url=affiliate_url,
                draft=draft,
                status="draft" if affiliate_url else "needs_link",
                metadata=metadata,
            )
        await update.message.reply_text(_preview(post))

        if not affiliate_url:
            await update.message.reply_text(f"Draft #{post.id} đang thiếu link. Gửi: /addlink {post.id} <link>")
        await _reply_status(update)


async def addlink(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Dùng: /addlink <post_id> <shopee_affiliate_link>")
        return

    try:
        post_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("post_id phải là số.")
        return

    link = context.args[1].strip()
    if not _is_shopee_link(link):
        await update.message.reply_text("Link Shopee chưa hợp lệ.")
        return

    with _db() as db:
        post = add_affiliate_link(db, post_id, link)
        if not post:
            await update.message.reply_text(f"Không tìm thấy post #{post_id}.")
            return
        await update.message.reply_text(_preview(post))
        await _reply_status(update)


async def queue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with _db() as db:
        posts = list_recent_posts(db)
        if not posts:
            await update.message.reply_text("Queue đang trống.")
            return
        await update.message.reply_text(
            "\n".join(
                f"#{post.id} | {post.status} | {post.keyword} | score {int(post.quality_score)}"
                for post in posts
            )
        )


async def view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Dùng: /view <post_id>")
        return
    with _db() as db:
        post = get_post(db, int(context.args[0]))
        if not post or post.status == "deleted":
            await update.message.reply_text("Không tìm thấy post.")
            return
        await update.message.reply_text(_preview(post))


async def regenerate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Dùng: /regenerate <post_id>")
        return

    try:
        post_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("post_id phải là số.")
        return

    await update.message.reply_text("Đang tạo lại nội dung bằng AI...")

    with _db() as db:
        try:
            post = get_post(db, post_id)
            if not post or post.status == "deleted":
                await update.message.reply_text(f"Không tìm thấy post #{post_id}.")
                return

            meta = _post_meta(post)
            is_engagement_post = post.status == "engagement" or bool(meta.get("mode") or meta.get("persona"))

            if is_engagement_post:
                mode = meta.get("mode", "viral")
                draft = generate_threads_engagement_draft(
                    db,
                    post.keyword,
                    style=meta.get("style", _engagement_mode_style(mode)),
                    persona=meta.get("persona", "daily"),
                )
                draft.cta = _metadata_cta(
                    persona=meta.get("persona", "daily"),
                    mode=mode,
                    style=meta.get("style", _engagement_mode_style(mode)),
                )
                if post.status == "posted":
                    updated = create_post(
                        db,
                        keyword=post.keyword,
                        product_name=post.product_name,
                        affiliate_url=None,
                        draft=draft,
                        status="engagement",
                        metadata={
                            "content_type": "engagement",
                            "content_goal": "engagement",
                            "persona": _engagement_persona_name(meta.get("persona", "daily")).lower(),
                            "hook_type": mode,
                        },
                    )
                    await update.message.reply_text(f"Post #{post_id} đã posted, mình tạo draft mới #{updated.id}.")
                else:
                    updated = update_draft_content(db, post_id, draft)
                    updated = update_post_metadata(
                        db,
                        post_id,
                        {
                            "content_type": "engagement",
                            "content_goal": "engagement",
                            "persona": _engagement_persona_name(meta.get("persona", "daily")).lower(),
                            "hook_type": mode,
                        },
                    ) or updated
            else:
                links = get_post_links(db, post.id)
                first_link = links[0] if links else None
                draft, metadata = generate_threads_shopee_content(
                    db,
                    ThreadsDraftRequest(
                        keyword=post.keyword,
                        product_name=post.product_name,
                        affiliate_url=post.affiliate_url or (first_link.affiliate_url if first_link else ""),
                        product_url=first_link.product_url if first_link else "",
                        price=first_link.price if first_link else "",
                        shop_name=first_link.shop_name if first_link else "",
                        style="viral product-native",
                    ),
                )
                if post.status == "posted":
                    if links:
                        updated = create_group_post(
                            db,
                            keyword=post.keyword,
                            product_name=post.product_name,
                            draft=draft,
                            links=_post_link_payloads(links),
                            status="draft",
                            metadata=metadata,
                        )
                    else:
                        updated = create_post(
                            db,
                            keyword=post.keyword,
                            product_name=post.product_name,
                            affiliate_url=post.affiliate_url,
                            draft=draft,
                            status="draft" if post.affiliate_url else "needs_link",
                            metadata=metadata,
                        )
                    await update.message.reply_text(f"Post #{post_id} đã posted, mình tạo draft mới #{updated.id}.")
                else:
                    updated = update_draft_content(db, post_id, draft)
                    updated = update_post_metadata(db, post_id, metadata) or updated

            if not updated:
                await update.message.reply_text(f"Không update được post #{post_id}.")
                return
            await update.message.reply_text(_preview(updated))
            await _reply_status(update)
        except Exception as exc:
            await update.message.reply_text(f"Regenerate bị lỗi: {exc}")
            raise


async def refreshdrafts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    limit = _import_limit() or 2
    if context.args:
        try:
            limit = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Dung: /refreshdrafts [limit]")
            return

    limit = max(1, min(10, limit))
    await update.message.reply_text(f"Dang regenerate va giu lai {limit} draft phu hop...")

    refreshed_ids: list[int] = []
    deleted_ids: list[int] = []

    with _db() as db:
        drafts = list_posts_by_status(db, "draft")
        if not drafts:
            await update.message.reply_text("Khong co draft nao de refresh.")
            return

        keep = drafts[:limit]
        remove = drafts[limit:]

        for post in keep:
            links = get_post_links(db, post.id)
            first_link = links[0] if links else None
            draft, metadata = generate_threads_shopee_content(
                db,
                ThreadsDraftRequest(
                    keyword=post.keyword,
                    product_name=post.product_name,
                    affiliate_url=post.affiliate_url or (first_link.affiliate_url if first_link else ""),
                    product_url=first_link.product_url if first_link else "",
                    price=first_link.price if first_link else "",
                    shop_name=first_link.shop_name if first_link else "",
                    style="viral product-native",
                ),
            )
            update_draft_content(db, post.id, draft)
            update_post_metadata(db, post.id, metadata)
            refreshed_ids.append(post.id)

        for post in remove:
            update_status(db, post.id, "deleted")
            deleted_ids.append(post.id)

    await update.message.reply_text(
        "Refresh xong.\n"
        f"Draft giu lai va da tao noi dung moi: {', '.join(f'#{post_id}' for post_id in refreshed_ids)}\n"
        f"Draft du da xoa khoi queue: {', '.join(f'#{post_id}' for post_id in deleted_ids) if deleted_ids else 'khong co'}\n"
        "Dung /queue de xem lai."
    )
    await _reply_status(update)


async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Dùng: /approve <post_id>")
        return
    post_id = int(context.args[0])
    with _db() as db:
        post = get_post(db, post_id)
        if not post or post.status == "deleted":
            await update.message.reply_text(f"Không tìm thấy post #{post_id}.")
            return
        if post.status != "engagement" and not _has_any_tracking_link(db, post):
            await update.message.reply_text(f"Post #{post_id} chưa có link Shopee. Dùng /addlink {post_id} <link> trước.")
            return
        update_status(db, post_id, "approved")
        await update.message.reply_text(f"Đã approve post #{post_id}. Dùng /post {post_id} để đăng Threads.")
        await _reply_status(update)


async def post_to_threads(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Dùng: /post <post_id> [account_name]")
        return

    try:
        post_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("post_id phải là số.")
        return

    with _db() as db:
        post = get_post(db, post_id)

        if not post or post.status == "deleted":
            await update.message.reply_text(f"Không tìm thấy post #{post_id}.")
            return

        if post.status == "posted":
            await update.message.reply_text(f"Post #{post_id} đã được đăng rồi. Threads ID: {post.threads_post_id or 'unknown'}")
            return

        if post.status != "engagement" and not _has_any_tracking_link(db, post):
            await update.message.reply_text(f"Post #{post_id} chưa có link Shopee. Dùng /addlink {post_id} <link> trước.")
            return

        account_name = context.args[1].strip() if len(context.args) >= 2 else ""
        try:
            if account_name:
                account = get_threads_account(account_name)
            else:
                account = select_account_for_post(_post_account_payload(post), load_threads_accounts())
        except ThreadsAccountError as exc:
            await update.message.reply_text(f"Chưa chọn được Threads account: {exc}")
            return

        await update.message.reply_text(f"Đang đăng bài lên Threads bằng account: {account['name']}...")

        try:
            result = publish_threads_post(_thread_text(post), account=account)
        except ThreadsPostingError as exc:
            await update.message.reply_text(f"Chưa đăng được Threads: {exc}")
            return

        threads_post_id = str(result.get("id") or result.get("post_id") or "")
        reply_message = ""

        if get_settings().post_tracking_link_as_reply and _has_any_tracking_link(db, post) and threads_post_id:
            reply_count = 0
            failed_replies: list[str] = []
            try:
                for reply_text in _reply_link_texts(post):
                    publish_threads_reply(threads_post_id, reply_text, account=account)
                    reply_count += 1
                    time.sleep(2)
            except ThreadsPostingError as exc:
                failed_replies.append(str(exc))

            if failed_replies:
                reply_message = f"\nBài chính đã đăng. Đã gắn {reply_count} bình luận link. Lỗi đầu tiên: {failed_replies[0]}"
            else:
                reply_message = f"\nĐã gắn {reply_count} bình luận link."

        post.threads_post_id = threads_post_id
        post.posted_account_name = account["name"]
        post.posted_account_user_id = account["user_id"]
        post.status = "posted"
        db.commit()
        db.refresh(post)
        if _should_post_telegram_cta(post):
            ok, cta_message = await _publish_telegram_cta_reply(db, post, account)
            reply_message += f"\nTelegram CTA: {cta_message if ok else 'failed - ' + cta_message}"
        learning_result = maybe_run_auto_learning()
        learning_message = ""
        if learning_result:
            learning_message = f"\nAuto learning checked. enough_data={learning_result.get('enough_data')}, sample={learning_result.get('total_posts')}"

        await update.message.reply_text(
            f"Đã đăng Threads cho post #{post.id} bằng {account['name']}.\n"
            f"Threads ID: {post.threads_post_id or 'unknown'}{reply_message}{learning_message}"
        )
        await _reply_status(update)


async def threadpost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _admin_required(update):
        await update.message.reply_text("Lenh nay chi danh cho admin.")
        return
    account_name, content = _parse_manual_threadpost_args(update, context)
    if not content:
        await update.message.reply_text(
            "Dung: /threadpost [account_name] <noi dung>\n"
            "Hoac reply vao tin nhan chua noi dung roi go /threadpost [account_name]."
        )
        return
    if len(content) > 500:
        await update.message.reply_text(f"Noi dung dai {len(content)} ky tu. Threads text post nen <= 500 ky tu.")
        return
    try:
        cta = _manual_support_cta()
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return
    if len(cta) > 500:
        await update.message.reply_text(f"CTA Telegram dang dai {len(cta)} ky tu, can rut gon duoi 500 ky tu.")
        return

    try:
        if account_name:
            account = get_threads_account(account_name)
        else:
            account = select_account_for_post(
                {
                    "keyword": "manual support post",
                    "product_name": "",
                    "content_type": "engagement",
                    "content_goal": "engagement",
                    "content": content,
                },
                load_threads_accounts(),
            )
    except ThreadsAccountError as exc:
        await update.message.reply_text(f"Chua chon duoc Threads account: {exc}")
        return

    await update.message.reply_text(f"Dang dang Threads thu cong bang account: {account['name']}...")
    try:
        result = publish_threads_post(content, account=account)
    except ThreadsPostingError as exc:
        await update.message.reply_text(f"Chua dang duoc Threads: {exc}")
        return

    threads_post_id = str(result.get("id") or result.get("post_id") or "")
    reply_id = ""
    reply_error = ""
    if threads_post_id:
        try:
            reply_result = publish_threads_reply(threads_post_id, cta, account=account)
            reply_id = str(reply_result.get("id") or reply_result.get("post_id") or "")
        except ThreadsPostingError as exc:
            reply_error = str(exc)

    with _db() as db:
        post = ThreadsPost(
            keyword="manual support post",
            product_name="",
            content=content,
            cta="",
            hashtags="[]",
            status="posted",
            quality_score=100,
            content_type="engagement",
            content_goal="engagement",
            target_platform="threads",
            threads_post_id=threads_post_id,
            posted_account_name=account["name"],
            posted_account_user_id=account["user_id"],
            telegram_cta_text=cta,
            telegram_cta_mode="reply",
            telegram_cta_reply_id=reply_id or None,
            telegram_cta_posted_at=datetime.now() if reply_id else None,
            telegram_cta_status="posted" if reply_id else ("failed" if reply_error else "skipped"),
        )
        db.add(post)
        db.commit()
        db.refresh(post)
        post_id = post.id

    if reply_error:
        await update.message.reply_text(
            f"Da dang bai Threads #{post_id} bang {account['name']}.\n"
            f"Threads ID: {threads_post_id or 'unknown'}\n"
            f"Nhung comment Telegram CTA bi loi: {reply_error}"
        )
    else:
        await update.message.reply_text(
            f"Da dang bai Threads #{post_id} bang {account['name']}.\n"
            f"Threads ID: {threads_post_id or 'unknown'}\n"
            f"CTA comment: {'posted' if reply_id else 'skipped'}"
        )


async def replylinks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Dùng: /replylinks <post_id>")
        return

    try:
        post_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("post_id phải là số.")
        return

    with _db() as db:
        post = get_post(db, post_id)
        if not post or post.status == "deleted":
            await update.message.reply_text(f"Không tìm thấy post #{post_id}.")
            return
        if not post.threads_post_id:
            await update.message.reply_text(f"Post #{post_id} chưa có Threads ID. Hãy /post {post_id} trước.")
            return

        try:
            account = get_threads_account(post.posted_account_name) if post.posted_account_name else None
        except ThreadsAccountError as exc:
            await update.message.reply_text(f"Không lấy được account đã post bài này: {exc}")
            return

        await update.message.reply_text("Đang gắn link vào bình luận...")
        reply_count = 0
        try:
            for reply_text in _reply_link_texts(post):
                publish_threads_reply(post.threads_post_id, reply_text, account=account)
                reply_count += 1
                time.sleep(2)
        except ThreadsPostingError as exc:
            await update.message.reply_text(f"Đã gắn {reply_count} link, sau đó bị lỗi: {exc}")
            return

        await update.message.reply_text(f"Đã gắn {reply_count} link vào bình luận cho post #{post.id}.")


async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Dùng: /delete <post_id>")
        return
    post_id = int(context.args[0])
    with _db() as db:
        post = update_status(db, post_id, "deleted")
        if not post:
            await update.message.reply_text(f"Không tìm thấy post #{post_id}.")
            return
        await update.message.reply_text(f"Đã xóa post #{post_id} khỏi queue.")
        await _reply_status(update)


async def analytics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with _db() as db:
        summary = analytics_summary(db)
        link_counts = catalog_link_counts(db)
    top = (
        "\n".join(f"{idx}. #{post.post_id} - {post.clicks} clicks - {post.keyword}" for idx, post in enumerate(summary.top_posts, 1))
        if summary.top_posts
        else "chưa có click"
    )
    await update.message.reply_text(
        f"""Tổng bài: {summary.total_posts}
Draft: {summary.draft}
Needs link: {summary.needs_link}
Approved: {summary.approved}
Posted: {summary.posted}
Tổng click: {summary.total_clicks}
Links DB: {link_counts['unique_links']} unique / {link_counts['total_links']} total
Top 5 bài nhiều click nhất:
{top}"""
    )


async def syncposts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_feature_enabled("threads_background_sync"):
        await update.message.reply_text("Threads sync đang đóng băng trong MVP. Dùng workflow manual demand intake.")
        return
    account_name = context.args[0].strip() if context.args else ""
    await update.message.reply_text("Đang đồng bộ bài Threads...")
    try:
        result = sync_account_posts(account_name, limit=50) if account_name else sync_all_accounts_posts(limit_per_account=50)
    except ThreadsAccountError as exc:
        await update.message.reply_text(f"Không sync được: {exc}")
        return
    await update.message.reply_text(_sync_result_text("Sync posts", result))


async def syncinsights(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_feature_enabled("threads_background_sync"):
        await update.message.reply_text("Threads insights sync đang đóng băng trong MVP.")
        return
    account_name = context.args[0].strip() if context.args else ""
    await update.message.reply_text("Đang đồng bộ insights Threads...")
    try:
        result = sync_account_insights(account_name, limit=50) if account_name else sync_all_accounts_insights(limit_per_account=50)
    except ThreadsAccountError as exc:
        await update.message.reply_text(f"Không sync được: {exc}")
        return
    await update.message.reply_text(_sync_result_text("Sync insights", result))


async def syncreplies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_feature_enabled("threads_background_sync"):
        await update.message.reply_text("Threads replies sync đang đóng băng trong MVP.")
        return
    account_name = context.args[0].strip() if context.args else ""
    await update.message.reply_text("Đang đồng bộ replies Threads...")
    results = []
    try:
        accounts_list = [get_threads_account(account_name)] if account_name else [acc for acc in load_threads_accounts() if acc.get("enabled")]
        for account in accounts_list:
            results.append(sync_account_replies(account["name"], limit_posts=30))
    except ThreadsAccountError as exc:
        await update.message.reply_text(f"Không sync được: {exc}")
        return
    await update.message.reply_text(_sync_result_text("Sync replies", {"accounts": results}))


async def threadstats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Dùng: /threadstats <post_id>")
        return
    try:
        post_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("post_id phải là số.")
        return
    stats = thread_stats(post_id)
    if not stats:
        await update.message.reply_text(f"Chưa có stats cho post #{post_id}. Có thể chạy /syncinsights trước.")
        return
    await update.message.reply_text(
        "\n".join(
            [
                f"Thread stats #{post_id}:",
                f"- Threads ID: {stats.get('threads_media_id') or 'chưa có'}",
                f"- account: {stats.get('account_name') or 'chưa rõ'}",
                f"- views: {stats.get('views', 0)}",
                f"- likes: {stats.get('likes', 0)}",
                f"- replies: {stats.get('replies', 0)}",
                f"- reposts: {stats.get('reposts', 0)}",
                f"- quotes: {stats.get('quotes', 0)}",
                f"- clicks: {stats.get('click_count', 0)}",
                f"- affiliate CTR: {_pct(stats.get('affiliate_ctr'))}",
                f"- engagement rate: {_pct(stats.get('engagement_rate'))}",
                f"- purchase intent: {_score(stats.get('purchase_intent_score'))}",
                f"- performance: {_score(stats.get('performance_score'))}",
            ]
        )
    )


async def accountperformance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_feature_enabled("learning_engine"):
        await update.message.reply_text("Account learning đang đóng băng trong MVP.")
        return
    account_name = context.args[0].strip() if context.args else ""
    if not account_name:
        await update.message.reply_text("Dùng: /accountperformance <account_name>")
        return
    profile = update_account_learning_profile(account_name, min_posts=get_settings().threads_learning_min_posts, lookback_days=get_settings().threads_insights_lookback_days)
    await update.message.reply_text(
        "\n".join(
            [
                f"Account performance: {account_name}",
                f"- enough_data: {profile.get('enough_data')}",
                f"- sample size: {profile.get('sample_size')}",
                f"- best reach: {_leader(profile.get('best_for_reach'))}",
                f"- best engagement: {_leader(profile.get('best_for_engagement'))}",
                f"- best affiliate: {_leader(profile.get('best_for_affiliate'))}",
                f"- persona weights: {_weights_line((profile.get('weights') or {}).get('personas', {}))}",
                f"- angle weights: {_weights_line((profile.get('weights') or {}).get('angles', {}))}",
                f"- hook weights: {_weights_line((profile.get('weights') or {}).get('hook_types', {}))}",
            ]
        )
    )


async def threadtrends(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_feature_enabled("threads_trend_provider"):
        await update.message.reply_text("Threads trend provider đang đóng băng. Dùng /adddemand để nhập opportunity thủ công.")
        return
    keyword = " ".join(context.args).strip()
    if not keyword:
        await update.message.reply_text("Dùng: /threadtrends <keyword>")
        return
    account = next((acc for acc in load_threads_accounts() if acc.get("enabled")), None)
    if not account:
        await update.message.reply_text("Chưa có Threads account hợp lệ để gọi keyword search.")
        return
    with _db() as db:
        snapshot = collect_threads_keyword_snapshot(account, keyword, limit=50, db=db)
    if not snapshot:
        await update.message.reply_text("Chưa lấy được Threads keyword signal. Có thể token chưa có quyền hoặc THREADS_KEYWORD_SEARCH_ENABLED=false.")
        return
    await update.message.reply_text(
        "\n".join(
            [
                f"Threads trend: {snapshot['keyword']}",
                f"- score: {snapshot['score']}/100",
                f"- results: {snapshot['result_count']} ({snapshot['recent_result_count']} gần đây)",
                f"- related: {', '.join(snapshot['related_topics']) or 'chưa rõ'}",
                f"- intents: {', '.join(snapshot['common_intents']) or 'chưa rõ'}",
                f"- tone: {snapshot['tone_summary']}",
                f"- reason: {snapshot['reason']}",
            ]
        )
    )


async def mentions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    account_name = context.args[0].strip() if context.args else ""
    try:
        account = get_threads_account(account_name or None)
        rows = get_mentions(account, limit=10)
    except ThreadsAccountError as exc:
        await update.message.reply_text(f"Không lấy được account: {exc}")
        return
    if not rows:
        await update.message.reply_text("Chưa có mentions hoặc token chưa có quyền threads_manage_mentions.")
        return
    await update.message.reply_text(
        "Mentions gần đây:\n"
        + "\n".join(f"- {str(row.get('username') or 'unknown')}: {str(row.get('text') or '')[:120]}" for row in rows[:10])
    )


async def replysuggestions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Dùng: /replysuggestions <post_id>")
        return
    try:
        post_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("post_id phải là số.")
        return
    with _db() as db:
        post = get_post(db, post_id)
        if not post:
            await update.message.reply_text(f"Không tìm thấy post #{post_id}.")
            return
        replies = list(
            db.scalars(
                select(ThreadsReply)
                .where(ThreadsReply.post_id == post_id, ThreadsReply.is_spam == 0)
                .order_by(ThreadsReply.synced_at.desc())
                .limit(20)
            )
        )
        post_payload = {"has_links": _has_any_tracking_link(db, post)}
        suggestions = []
        for reply in replies:
            suggestion = build_reply_suggestion(
                {
                    "intent": reply.intent,
                    "asks_for_link": bool(reply.asks_for_link),
                    "asks_for_price": bool(reply.asks_for_price),
                    "product_interest": bool(reply.product_interest),
                    "is_spam": bool(reply.is_spam),
                },
                post_payload,
            )
            if suggestion["should_reply"]:
                suggestions.append((reply, suggestion))
    if not suggestions:
        await update.message.reply_text("Chưa có reply nào cần gợi ý phản hồi. Chạy /syncreplies trước nếu cần.")
        return
    await update.message.reply_text(
        "Reply suggestions (chỉ gợi ý, không tự gửi):\n\n"
        + "\n\n".join(
            f"- @{reply.reply_username or 'unknown'}: {reply.reply_text[:120]}\n"
            f"  Gợi ý: {suggestion['reply_text']}\n"
            f"  Lý do: {suggestion['reason']}"
            for reply, suggestion in suggestions[:8]
        )
    )


async def delete_thread(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2 or context.args[1].lower() != "confirm":
        await update.message.reply_text("Dùng: /delete_thread <post_id> confirm")
        return
    try:
        post_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("post_id phải là số.")
        return
    with _db() as db:
        post = get_post(db, post_id)
        if not post or not post.threads_post_id:
            await update.message.reply_text(f"Post #{post_id} chưa có Threads ID.")
            return
        try:
            account = get_threads_account(post.posted_account_name) if post.posted_account_name else get_threads_account(None)
        except ThreadsAccountError as exc:
            await update.message.reply_text(f"Không lấy được account: {exc}")
            return
        ok = delete_thread_post(account, post.threads_post_id)
        if ok:
            post.status = "deleted"
            db.commit()
        await update.message.reply_text("Đã gửi lệnh xóa Threads." if ok else "Chưa xóa được, có thể token thiếu quyền threads_delete.")


async def features(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    snapshot = feature_snapshot()
    await update.message.reply_text(
        "Feature flags:\n"
        "Enabled:\n"
        + "\n".join(f"- {item}" for item in snapshot["enabled"])
        + "\n\nFrozen:\n"
        + "\n".join(f"- {item}" for item in snapshot["frozen"])
    )


async def adddemand(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_feature_enabled("manual_demand_intake"):
        await update.message.reply_text("Manual demand intake đang tắt.")
        return
    if not context.args:
        await update.message.reply_text("Dùng: /adddemand <url> <nội dung bài>")
        return
    url = context.args[0].strip()
    text = " ".join(context.args[1:]).strip()
    if not text:
        await update.message.reply_text("Bot không scrape URL. Hãy gửi thêm nội dung bài sau URL.")
        return
    result = create_manual_demand(text=text, url=url)
    await update.message.reply_text(_manual_demand_result_text(result))


async def adddemandtext(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_feature_enabled("manual_demand_intake"):
        await update.message.reply_text("Manual demand intake đang tắt.")
        return
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Dùng: /adddemandtext <nội dung bài>")
        return
    result = create_manual_demand(text=text)
    await update.message.reply_text(_manual_demand_result_text(result))


async def importdemands(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Dùng: /importdemands <csv_path>")
        return
    result = import_demands_csv(" ".join(context.args).strip())
    await update.message.reply_text(
        "Import demands xong.\n"
        f"- rows: {result['rows']}\n"
        f"- created: {result['created']}\n"
        f"- duplicates: {result['duplicates']}\n"
        f"- low_intent: {result['low_intent']}\n"
        f"- no_product_match: {result['no_product_match']}\n"
        f"- errors: {'; '.join(result['errors'][:3]) if result['errors'] else 'không có'}"
    )


async def scanthreads(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_feature_enabled("threads_auto_scanner"):
        await update.message.reply_text("Threads auto scanner đang đóng băng. Dùng /adddemand <url> <text> hoặc /adddemandtext <text>.")
        return
    if not get_settings().threads_demand_scanner_enabled:
        await update.message.reply_text("Demand scanner đang tắt. Bật THREADS_DEMAND_SCANNER_ENABLED=true trong .env trước.")
        return
    args = context.args
    account_name = ""
    manual_keyword = " ".join(args).strip()
    if args and len(args) >= 2:
        maybe_account = args[-1].strip()
        account_names = {account["name"] for account in load_threads_accounts()}
        if maybe_account in account_names:
            account_name = maybe_account
            manual_keyword = " ".join(args[:-1]).strip()
    if not account_name:
        account = next((acc for acc in load_threads_accounts() if acc.get("enabled")), None)
        if not account:
            await update.message.reply_text("Chưa có Threads account hợp lệ.")
            return
        account_name = account["name"]
    keywords = build_scan_keywords(manual_keyword or None, limit=8 if manual_keyword else 10)
    await update.message.reply_text(f"Đang quét {len(keywords)} keyword bằng account {account_name}...")
    result = scan_threads_demand(
        account_name,
        keywords,
        limit_per_keyword=20,
        max_opportunities=get_settings().threads_demand_max_results_per_scan,
    )
    await update.message.reply_text(
        "Scan Threads xong.\n"
        f"- keywords: {len(result['keywords_scanned'])}\n"
        f"- posts fetched: {result['posts_fetched']}\n"
        f"- opportunities: {result['opportunities_created']}\n"
        f"- duplicates skipped: {result['duplicates_skipped']}\n"
        f"- low intent skipped: {result['low_intent_skipped']}\n"
        f"- errors: {'; '.join(result['errors'][:3]) if result['errors'] else 'không có'}"
    )


async def buyops(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    limit = 5
    if context.args and context.args[0].isdigit():
        limit = max(1, min(10, int(context.args[0])))
    rows = list_opportunities(limit=limit)
    if not rows:
        await update.message.reply_text("Chưa có buy opportunity mới. Chạy /scanthreads trước.")
        return
    await update.message.reply_text("\n\n".join(_buyop_text(row, full=False) for row in rows))


async def buyop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Dùng: /buyop <id>")
        return
    opp = get_buy_opportunity(int(context.args[0]))
    if not opp:
        await update.message.reply_text("Không tìm thấy opportunity.")
        return
    await update.message.reply_text(_buyop_text(opp, full=True))


async def approvebuy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Dùng: /approvebuy <id>")
        return
    ok, message = approve_opportunity(int(context.args[0]))
    await update.message.reply_text(f"{'Đã approve' if ok else 'Chưa approve được'}: {message}")


async def approvebuybatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Dùng: /approvebuybatch <id1,id2,id3>")
        return
    ids = _parse_ids(context.args[0])
    if not ids:
        await update.message.reply_text("Không đọc được ID.")
        return
    result = approve_buy_batch_service(ids)
    await update.message.reply_text(
        f"Approved: {', '.join(map(str, result['approved'])) or 'không có'}\n"
        f"Giới hạn batch: {result['max_batch']}"
    )


async def skipbuy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Dùng: /skipbuy <id>")
        return
    ok, message = skip_opportunity(int(context.args[0]))
    await update.message.reply_text(f"{'Đã skip' if ok else 'Chưa skip được'}: {message}")


async def editbuy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.message.reply_text("Dùng: /editbuy <id> <comment mới>")
        return
    comment = " ".join(context.args[1:]).strip()
    ok, message = edit_opportunity_comment(int(context.args[0]), comment)
    await update.message.reply_text(f"{'Đã sửa comment' if ok else 'Chưa sửa được'}: {message}")


async def replybuy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Dùng: /replybuy <id> [account_name]")
        return
    account_name = context.args[1].strip() if len(context.args) >= 2 else None
    await update.message.reply_text("Đang reply opportunity đã approve...")
    ok, message, comment = reply_opportunity(int(context.args[0]), account_name)
    if comment:
        await update.message.reply_text(f"{'Đã reply' if ok else 'Chưa reply được'}: {message}\n\nManual copy:\n{comment}")
    else:
        await update.message.reply_text(f"{'Đã reply' if ok else 'Chưa reply được'}: {message}")


async def replybuybatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Dùng: /replybuybatch <id1,id2,id3> [account_name]")
        return
    ids = _parse_ids(context.args[0])
    if not ids:
        await update.message.reply_text("Không đọc được ID.")
        return
    account_name = context.args[1].strip() if len(context.args) >= 2 else None
    await update.message.reply_text("Đang reply batch đã approve...")
    result = reply_buy_batch_service(ids, account_name)
    await update.message.reply_text(
        "\n".join(
            f"- #{row['id']}: {'ok' if row['ok'] else 'fail'} - {row['message']}"
            for row in result["results"]
        )
    )


async def copybuy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Dùng: /copybuy <id>")
        return
    ok, message, comment = copy_opportunity(int(context.args[0]), approve=False)
    await update.message.reply_text(f"{message}\n\n{comment}" if ok else f"Chưa copy được: {message}")


async def approveandcopy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Dùng: /approveandcopy <id>")
        return
    ok, message, comment = copy_opportunity(int(context.args[0]), approve=True)
    await update.message.reply_text(f"{message}\n\n{comment}" if ok else f"Chưa copy được: {message}")


async def opstats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    stats = opportunity_stats()
    await update.message.reply_text(
        "Opportunity stats:\n"
        f"- total: {stats['total']}\n"
        f"- approved: {stats['statuses'].get('approved', 0)}\n"
        f"- replied: {stats['statuses'].get('replied', 0)}\n"
        f"- manual copied: {stats['statuses'].get('manual_copied', 0)}\n"
        f"- skipped: {stats['statuses'].get('skipped', 0)}\n"
        f"- expired: {stats['statuses'].get('expired', 0)}\n"
        f"- clicks: {stats['clicks']}\n"
        "Top intents:\n"
        + ("\n".join(f"- {row['name']}: {row['count']}" for row in stats["top_intents"]) or "- chưa có")
        + "\nTop categories:\n"
        + ("\n".join(f"- {row['name']}: {row['count']}" for row in stats["top_categories"]) or "- chưa có")
    )


def _manual_demand_result_text(result: dict) -> str:
    if result["created"]:
        return (
            f"Đã tạo opportunity #{result['opportunity_id']}.\n"
            f"- intent: {result['intent']} ({result['purchase_intent_score']:.0f})\n"
            f"- nhu cầu: {result['normalized_query']}\n"
            f"- products: {result['matched_products_count']}\n"
            f"- mode: {result['response_mode']}\n"
            f"Dùng /buyop {result['opportunity_id']} để xem, /approvebuy {result['opportunity_id']} để duyệt."
        )
    return (
        "Chưa tạo opportunity.\n"
        f"- reason: {result['reason']}\n"
        f"- intent: {result.get('intent')}\n"
        f"- score: {result.get('purchase_intent_score')}"
    )


def _sync_result_text(title: str, result: dict) -> str:
    if "accounts" in result:
        rows = [title + ":"]
        for item in result.get("accounts", []):
            rows.append(
                "- "
                + item.get("account_name", "unknown")
                + ": "
                + ", ".join(f"{key}={value}" for key, value in item.items() if key not in {"account_name", "errors"} and not isinstance(value, list))
            )
            if item.get("errors"):
                rows.append("  errors: " + "; ".join(item["errors"][:3]))
        return "\n".join(rows)
    rows = [title + ":", "- " + ", ".join(f"{key}={value}" for key, value in result.items() if key != "errors" and not isinstance(value, list))]
    if result.get("errors"):
        rows.append("errors: " + "; ".join(result["errors"][:3]))
    return "\n".join(rows)


def _pct(value: object) -> str:
    if value is None:
        return "chưa có"
    return f"{float(value) * 100:.2f}%"


def _score(value: object) -> str:
    if value is None:
        return "chưa có"
    return f"{float(value):.1f}/100"


def _leader(value: object) -> str:
    if not isinstance(value, dict) or not value:
        return "chưa có"
    return f"{value.get('name')} ({value.get('score')})"


def _weights_line(weights: dict) -> str:
    if not weights:
        return "chưa có"
    return ", ".join(f"{key}: {value}" for key, value in list(sorted(weights.items(), key=lambda item: item[1], reverse=True))[:5])


def _buyop_text(opp: DemandOpportunity, full: bool = False) -> str:
    try:
        products = json.loads(opp.matched_products_json or "[]")
    except json.JSONDecodeError:
        products = []
    product_lines = "\n".join(
        f"  {idx}. {str(item.get('name') or '')[:70]} ({item.get('match_score')})"
        for idx, item in enumerate(products[:4], start=1)
    ) or "  chưa có"
    base = [
        f"Buy opportunity #{opp.id} | {opp.status}",
        f"- user: @{opp.author_username or 'unknown'}",
        f"- intent: {opp.intent} | score: {opp.purchase_intent_score:.0f}",
        f"- nhu cầu: {opp.normalized_query[:180]}",
        f"- source: {opp.platform} / {opp.intake_source}",
        f"- url: {opp.source_url or 'text-only'}",
        f"- response_mode: {opp.response_mode}",
        "- products:",
        product_lines,
    ]
    if full:
        base.extend(
            [
                f"- gốc: {opp.source_text_excerpt[:500]}",
                "Comment đề xuất:",
                opp.suggested_response,
                "Lệnh:",
                f"- /approvebuy {opp.id}",
                f"- /replybuy {opp.id}",
                f"- /copybuy {opp.id}",
                f"- /skipbuy {opp.id}",
                f"- /editbuy {opp.id} <comment mới>",
            ]
        )
    else:
        base.extend(["Comment:", opp.suggested_response[:500]])
    return "\n".join(base)


def _parse_ids(raw: str) -> list[int]:
    ids = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return ids


def _telegram_group_target() -> int | str | None:
    settings = get_settings()
    raw = settings.telegram_community_group_id.strip()
    if not raw:
        raw = settings.telegram_group_invite_url.strip()
    if raw.startswith("https://t.me/"):
        slug = raw.rstrip("/").rsplit("/", 1)[-1]
        if slug and not slug.startswith("+"):
            return f"@{slug.lstrip('@')}"
        return raw
    if raw.startswith("@"):
        return raw
    if raw.lstrip("-").isdigit():
        return int(raw)
    return raw or None


def _link_type_code(link_type_id: str) -> str:
    return LINK_TYPE_ID_TO_CODE.get(link_type_id, link_type_id[:2])


def _link_type_from_code(code: str) -> str | None:
    return LINK_TYPE_CODES.get(code)


def _admin_required(update: Update) -> bool:
    user = update.effective_user
    return is_link_admin(user.id if user else None)


def _group_required(update: Update) -> bool:
    chat = update.effective_chat
    return is_configured_group(chat.id if chat else None)


def _admin_group_required(update: Update) -> bool:
    return _admin_required(update) and _group_required(update)


def _batch_storage_group_id(update: Update) -> str | int | None:
    chat = update.effective_chat
    if chat and is_configured_group(chat.id):
        return chat.id
    configured = get_settings().telegram_community_group_id.strip()
    return configured or None


def _admin_group_denied_text(update: Update) -> str:
    user = update.effective_user
    chat = update.effective_chat
    settings = get_settings()
    return (
        "Lenh nay chi danh cho admin trong group cau hinh.\n"
        f"user_id hien tai: {user.id if user else 'unknown'}\n"
        f"chat_id hien tai: {chat.id if chat else 'unknown'}\n"
        f"admin_ids cau hinh: {settings.telegram_admin_user_ids or 'chua dat'}\n"
        f"group_id cau hinh: {settings.telegram_community_group_id or 'chua dat'}"
    )


def _link_type_buttons(prefix: str, user_id: int | None = None) -> InlineKeyboardMarkup:
    rows = []
    for item in load_link_types():
        code = _link_type_code(item["id"])
        callback = f"{prefix}:{code}" if user_id is None else f"{prefix}:{user_id}:{code}"
        rows.append([InlineKeyboardButton(item["name"], callback_data=callback)])
    return InlineKeyboardMarkup(rows)


def _category_buttons(prefix: str, link_type_id: str, categories: list[dict], user_id: int | None = None) -> InlineKeyboardMarkup:
    code = _link_type_code(link_type_id)
    rows = []
    for item in categories:
        callback = f"{prefix}:{code}:{item['category_id']}" if user_id is None else f"{prefix}:{user_id}:{code}:{item['category_id']}"
        rows.append([InlineKeyboardButton(f"{item['label']} ({item['count']})", callback_data=callback)])
    return InlineKeyboardMarkup(rows)


def _guide_text() -> str:
    return (
        "Link ưu đãi mới đã được cập nhật.\n\n"
        "Cách nhận link riêng:\n"
        "- /docquyen - xem các link ưu đãi độc quyền\n"
        "- /links - chọn loại link và danh mục bạn muốn\n\n"
        "Bot sẽ gửi danh sách riêng qua tin nhắn cá nhân, tối đa 15 link cho mỗi danh mục.\n"
        "Lần đầu sử dụng, hãy mở bot và bấm Start để bot có thể gửi tin nhắn cho bạn."
    )


def _guide_keyboard() -> InlineKeyboardMarkup:
    bot_username = get_settings().telegram_bot_username.strip().lstrip("@")
    open_url = f"https://t.me/{bot_username}" if bot_username else "https://t.me/"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Nhận link độc quyền", callback_data="ac:docq")],
            [InlineKeyboardButton("Chọn danh mục", callback_data="ac:links")],
            [InlineKeyboardButton("Mở bot", url=open_url)],
        ]
    )


def _active_link_type_buttons(types: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for item in types:
        code = _link_type_code(item["link_type_id"])
        rows.append([InlineKeyboardButton(f"{item['label']} ({item['count']})", callback_data=f"ac:pt:{code}")])
    return InlineKeyboardMarkup(rows)


def _publish_category_buttons(link_type_id: str, categories: list[dict]) -> InlineKeyboardMarkup:
    code = _link_type_code(link_type_id)
    rows = []
    for item in categories:
        rows.append([InlineKeyboardButton(f"{item['label']} ({item['count']})", callback_data=f"ac:pc:{code}:{item['category_id']}")])
    return InlineKeyboardMarkup(rows)


def _channel_link_keyboard(link_type_id: str, category_id: str) -> InlineKeyboardMarkup:
    code = _link_type_code(link_type_id)
    bot_username = get_settings().telegram_bot_username.strip().lstrip("@")
    rows = [[InlineKeyboardButton("Lấy riêng danh mục này", callback_data=f"ac:get:{code}:{category_id}")]]
    if link_type_id != "exclusive_offer":
        rows.append([InlineKeyboardButton("Lấy link độc quyền", callback_data=f"ac:get:{_link_type_code('exclusive_offer')}:all")])
    if bot_username:
        rows.append([InlineKeyboardButton("Mở bot", url=f"https://t.me/{bot_username}")])
    return InlineKeyboardMarkup(rows)


def _channel_link_post_text(link_type_id: str, category_id: str, links: list[AdminAffiliateLink]) -> str:
    header = [
        f"{link_type_name(link_type_id)} - {category_label(category_id)}",
        "",
        f"Gom nhanh {len(links[:15])} link đang có trong kho:",
        "",
    ]
    footer = [
        "Bấm nút bên dưới nếu muốn bot gửi riêng theo danh mục này.",
        get_settings().telegram_daily_link_disclosure,
    ]
    lines = header[:]
    truncated = False
    for index, link in enumerate(links[:15], start=1):
        candidate = [f"{index}. {link.display_name}", link.affiliate_url, ""]
        if len("\n".join(lines + candidate + footer)) > 3800:
            truncated = True
            break
        lines.extend(candidate)
    if truncated:
        lines.append("Còn thêm link trong kho, bấm nút nhận riêng để lấy đủ danh sách.")
        lines.append("")
    lines.extend(footer)
    return "\n".join(lines).strip()


def _csv_import_summary(result) -> str:
    lines = [
        "Import CSV vào kho channel xong.",
        f"Tổng dòng: {result.total_rows}",
        f"Link mới: {result.added}",
        f"Trùng trong file: {result.duplicates}",
        f"Bỏ qua: {result.ignored}",
        "",
        "Theo loại:",
    ]
    if result.type_counts:
        lines.extend(f"- {link_type_name(key)}: {count}" for key, count in sorted(result.type_counts.items()))
    else:
        lines.append("- chưa có")
    lines.append("Theo danh mục:")
    if result.category_counts:
        lines.extend(f"- {category_label(key)}: {count}" for key, count in sorted(result.category_counts.items()))
    else:
        lines.append("- chưa có")
    if result.errors:
        lines.append("Lỗi mẫu:")
        lines.extend(f"- {item}" for item in result.errors[:5])
    lines.append("")
    lines.append("Dùng /publishlinks để chọn danh mục đăng lên channel.")
    return "\n".join(lines)


def _parse_importdaily_args(text: str) -> tuple[str, str | None, str | None]:
    raw = text.partition(" ")[2].strip()
    if not raw:
        return "", None, None
    if raw[0] in {'"', "'"}:
        quote = raw[0]
        end = raw.find(quote, 1)
        if end > 0:
            parts = [raw[1:end].strip(), *raw[end + 1 :].strip().split()]
        else:
            parts = raw.split()
    else:
        try:
            parts = shlex.split(raw)
        except ValueError:
            parts = raw.split()
    if not parts:
        return "", None, None
    path = parts[0].strip().strip('"').strip("'")
    raw_date = None
    link_type_id = None
    for token in parts[1:]:
        if token in valid_link_type_ids():
            link_type_id = token
        elif not raw_date:
            raw_date = token
    return path, raw_date, link_type_id


def _parse_importdaily_upload_caption(text: str | None) -> tuple[str | None, str | None]:
    raw = (text or "").partition(" ")[2].strip()
    if not raw:
        return None, None
    try:
        parts = shlex.split(raw)
    except ValueError:
        parts = raw.split()
    raw_date = None
    link_type_id = None
    for token in parts:
        if token in valid_link_type_ids():
            link_type_id = token
        elif not raw_date:
            raw_date = token
    return raw_date, link_type_id


def _looks_like_local_path(path: str) -> bool:
    return bool(path) and (
        re.match(r"^[A-Za-z]:[\\/]", path) is not None
        or path.startswith("\\\\")
        or path.startswith("/")
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.args and context.args[0].startswith("links_") and update.effective_user:
        token = context.args[0].removeprefix("links_")
        with _db() as db:
            request = get_pending_request(db, token, update.effective_user.id)
            if not request:
                await update.message.reply_text("Yeu cau link da het han hoac khong hop le. Hay quay lai group va bam lai /links.")
                return
            links = get_links_for_delivery(db, request.link_type_id, request.category_id, limit=25, hard_cap=25)
            for text in build_private_link_messages(request.link_type_id, request.category_id, links):
                await context.bot.send_message(chat_id=update.effective_user.id, text=text, disable_web_page_preview=True)
            complete_private_request(db, request)
        return
    await update.message.reply_text(
        """POD Bot - Telegram link catalog + Threads engagement

Kho link:
/links
/docquyen

Admin link intake:
/linkbatch
/endlinkbatch
/cancellinkbatch
/currentlinkbatch
/importlinks
/endimportlinks
/publishlinks
/linkstats

Threads:
/engagepost <topic>
/queue
/view <post_id>
/regenerate <post_id>
/approve <post_id>
/post <post_id> [account_name]
/threadpost [account_name] <noi dung>
/retrytelegramcta <post_id>

System:
/accounts
/chatid
/features"""
    )


async def frozen_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Chuc nang nay hien dang duoc dong bang. Bot dang tap trung vao kho link Telegram va bai Threads dan ve group."
    )


async def chatid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    await update.message.reply_text(f"chat_id: {chat.id if chat else 'unknown'}")


async def linkbatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _admin_required(update):
        await update.message.reply_text(_admin_group_denied_text(update))
        return
    if not _batch_storage_group_id(update):
        await update.message.reply_text("Chua cau hinh TELEGRAM_COMMUNITY_GROUP_ID de dang guide sau batch.")
        return
    with _db() as db:
        cleanup_expired_admin_links(db)
    await update.message.reply_text(
        "Chon loai link cho dot nhap:",
        reply_markup=_link_type_buttons("ac:bt"),
    )


async def choose_batch_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if not is_link_admin(query.from_user.id if query.from_user else None):
        await query.message.reply_text("Ban khong co quyen tao batch.")
        return
    code = (query.data or "").rsplit(":", 1)[-1]
    link_type_id = _link_type_from_code(code)
    if not link_type_id:
        await query.message.reply_text("Loai link khong hop le.")
        return
    categories = [{"category_id": item["id"], "label": item.get("label", item["id"]), "count": 0} for item in load_categories()]
    await query.edit_message_text(
        f"Chon danh muc cho {link_type_name(link_type_id)}:",
        reply_markup=_category_buttons("ac:bc", link_type_id, categories),
    )


async def choose_batch_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if not is_link_admin(query.from_user.id if query.from_user else None):
        await query.message.reply_text("Ban khong co quyen tao batch.")
        return
    parts = (query.data or "").split(":")
    if len(parts) != 4:
        return
    link_type_id = _link_type_from_code(parts[2])
    category_id = parts[3]
    if not link_type_id or category_id not in valid_category_ids():
        await query.message.reply_text("Loai link hoac danh muc khong hop le.")
        return
    target_group = get_settings().telegram_community_group_id.strip() if not is_configured_group(query.message.chat_id) else query.message.chat_id
    if not target_group:
        await query.message.reply_text("Chua cau hinh TELEGRAM_COMMUNITY_GROUP_ID.")
        return
    with _db() as db:
        batch = admin_start_batch(db, query.from_user.id, target_group, link_type_id, category_id)
    await query.edit_message_text(
        "Da bat dau dot nhap:\n"
        f"Loai: {link_type_name(batch.link_type_id)}\n"
        f"Danh muc: {category_label(batch.category_id)}\n\n"
        "Admin hay gui link, moi dong mot link hoac:\n"
        "Ten san pham | https://..."
    )


async def ingest_admin_link_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat:
        return
    if not is_link_admin(user.id):
        return
    text = message.text or ""
    if not text or text.startswith("/"):
        return
    target_group = chat.id if is_configured_group(chat.id) else get_settings().telegram_community_group_id.strip()
    if not target_group:
        return
    with _db() as db:
        result = ingest_admin_message(db, user.id, target_group, text)
    if not result.batch or result.added <= 0:
        return
    await message.reply_text(
        f"Da them {result.added} link vao:\n"
        f"{link_type_name(result.batch.link_type_id)} -> {category_label(result.batch.category_id)}"
        + (f"\nTrung trong batch: {result.duplicates}" if result.duplicates else "")
    )


async def endlinkbatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _admin_required(update):
        await update.message.reply_text(_admin_group_denied_text(update))
        return
    target_group = _batch_storage_group_id(update)
    if not target_group:
        await update.message.reply_text("Chua cau hinh TELEGRAM_COMMUNITY_GROUP_ID.")
        return
    with _db() as db:
        batch = admin_close_batch(db, update.effective_user.id, target_group, "completed")
        cleanup_expired_admin_links(db)
    if not batch:
        await update.message.reply_text("Khong co batch active.")
        return
    await update.message.reply_text(f"Da ket thuc batch #{batch.id}. Link da luu: {batch.link_count}")
    if batch.link_count and get_settings().send_guide_after_batch:
        sent = await context.bot.send_message(
            chat_id=target_group,
            text=_guide_text(),
            reply_markup=_guide_keyboard(),
            disable_web_page_preview=True,
        )
        with _db() as db:
            set_batch_guide_message(db, batch.id, sent.message_id)


async def importlinks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _admin_required(update):
        await update.message.reply_text(_admin_group_denied_text(update))
        return
    if not _batch_storage_group_id(update):
        await update.message.reply_text("Chua cau hinh TELEGRAM_COMMUNITY_GROUP_ID de luu kho link channel.")
        return
    forced_link_type_id = _parse_importlinks_forced_type(context.args)
    if context.args and not forced_link_type_id:
        await update.message.reply_text("Loai link import khong hop le. Vi du: /importlinks exclusive_offer hoac /importlinks docquyen")
        return
    _save_pending_importlinks(update.effective_user.id, forced_link_type_id=forced_link_type_id)
    forced_line = f"Che do ep loai link: {link_type_name(forced_link_type_id)}\n" if forced_link_type_id else ""
    await update.message.reply_text(
        "Da bat che do nhan CSV trong 15 phut.\n"
        "Gui mot hoac nhieu file CSV vao chat nay, co caption /importlinks hoac khong deu duoc.\n\n"
        f"{forced_line}"
        "Bot se tu phan loai theo cot CSV va ten san pham:\n"
        "- loai link: Shopee/Xtra/San pham/Doc quyen\n"
        "- danh muc: thoi trang, gia dung, dien tu, the thao...\n\n"
        "Neu file la link doc quyen nhung cot CSV giong link thuong, dung /importlinks docquyen de ep loai.\n"
        "Sau khi import xong, dung /publishlinks de chon danh muc dang len channel.\n"
        "Dung /endimportlinks neu muon tat che do nhan CSV som."
    )


async def endimportlinks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _admin_required(update):
        await update.message.reply_text(_admin_group_denied_text(update))
        return
    _clear_pending_importlinks(update.effective_user.id)
    await update.message.reply_text("Da tat che do nhan CSV /importlinks.")


async def importlinks_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    user = update.effective_user
    if not message or not user or not message.document:
        return
    document = message.document
    file_name = document.file_name or "links.csv"
    caption = (message.caption or "").strip()
    explicit_import = caption.startswith("/importlinks")
    forced_link_type_id = ""
    if explicit_import:
        try:
            caption_args = shlex.split(caption.partition(" ")[2].strip())
        except ValueError:
            caption_args = caption.partition(" ")[2].strip().split()
        forced_link_type_id = _parse_importlinks_forced_type(caption_args)
        if caption_args and not forced_link_type_id:
            await message.reply_text("Loai link import khong hop le. Vi du caption: /importlinks exclusive_offer")
            return
        _save_pending_importlinks(user.id, forced_link_type_id=forced_link_type_id)
    else:
        pending = _load_pending_importlinks(user.id)
        if not pending:
            return
        forced_link_type_id = pending.get("forced_link_type_id", "")
    if not _admin_required(update):
        await message.reply_text("Lenh nay chi danh cho admin.")
        return
    target_group = _batch_storage_group_id(update)
    if not target_group:
        await message.reply_text("Chua cau hinh TELEGRAM_COMMUNITY_GROUP_ID.")
        return
    if not file_name.lower().endswith(".csv"):
        await message.reply_text("File import phai la CSV.")
        return
    if document.file_size and document.file_size > 8 * 1024 * 1024:
        await message.reply_text(
            "File CSV hoi lon, bot van se import nhung tren Vercel co the cham. "
            "Neu bi timeout, hay chia file thanh cac lo nho hon."
        )
    await message.reply_text("Da nhan CSV. Dang phan loai va import vao kho channel...")
    temp_path = ""
    try:
        tg_file = await document.get_file()
        with tempfile.NamedTemporaryFile(delete=False, suffix="-" + file_name) as tmp:
            temp_path = tmp.name
        await tg_file.download_to_drive(custom_path=temp_path)
        with _db() as db:
            result = import_admin_links_csv(db, temp_path, user.id, target_group, forced_link_type_id=forced_link_type_id)
            cleanup_expired_admin_links(db)
        await message.reply_text(_csv_import_summary(result))
    except Exception as exc:
        await message.reply_text(f"Import links loi: {exc}")
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                pass


async def publishlinks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _admin_required(update):
        await update.message.reply_text(_admin_group_denied_text(update))
        return
    if not _batch_storage_group_id(update):
        await update.message.reply_text("Chua cau hinh TELEGRAM_COMMUNITY_GROUP_ID.")
        return
    with _db() as db:
        types = admin_active_type_counts(db)
    if not types:
        await update.message.reply_text("Kho link channel hien chua co link active. Hay /importlinks truoc.")
        return
    await update.message.reply_text("Chon loai link muon dang len channel:", reply_markup=_active_link_type_buttons(types))


async def publish_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if not is_link_admin(query.from_user.id if query.from_user else None):
        await query.message.reply_text("Ban khong co quyen publish link.")
        return
    code = (query.data or "").rsplit(":", 1)[-1]
    link_type_id = _link_type_from_code(code)
    if not link_type_id:
        await query.message.reply_text("Loai link khong hop le.")
        return
    with _db() as db:
        cats = admin_categories_for_type(db, link_type_id)
    if not cats:
        await query.edit_message_text("Loai link nay chua co danh muc active.")
        return
    await query.edit_message_text(
        f"Chon danh muc dang cho {link_type_name(link_type_id)}:",
        reply_markup=_publish_category_buttons(link_type_id, cats),
    )


async def publish_category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if not is_link_admin(query.from_user.id if query.from_user else None):
        await query.message.reply_text("Ban khong co quyen publish link.")
        return
    parts = (query.data or "").split(":")
    if len(parts) != 4:
        return
    link_type_id = _link_type_from_code(parts[2])
    category_id = parts[3]
    if not link_type_id or category_id not in valid_category_ids():
        await query.message.reply_text("Loai link hoac danh muc khong hop le.")
        return
    target_group = _batch_storage_group_id(update)
    if not target_group:
        await query.message.reply_text("Chua cau hinh TELEGRAM_COMMUNITY_GROUP_ID.")
        return
    with _db() as db:
        links = get_links_for_delivery(db, link_type_id, category_id, limit=15, hard_cap=15)
    if not links:
        await query.message.reply_text("Danh muc nay hien chua co link active.")
        return
    sent = await context.bot.send_message(
        chat_id=target_group,
        text=_channel_link_post_text(link_type_id, category_id, links),
        reply_markup=_channel_link_keyboard(link_type_id, category_id),
        disable_web_page_preview=True,
    )
    await query.edit_message_text(f"Da dang len channel message_id={sent.message_id}.")


async def channel_get_links_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    parts = (query.data or "").split(":")
    if len(parts) != 4 or not query.from_user:
        await query.answer()
        return
    link_type_id = _link_type_from_code(parts[2])
    category_id = parts[3]
    allow_all_categories = link_type_id == "exclusive_offer" and category_id == "all"
    if not link_type_id or (not allow_all_categories and category_id not in valid_category_ids()):
        await query.answer("Loai link hoac danh muc khong hop le.", show_alert=True)
        return
    await _deliver_private_links(update, context, query.from_user.id, query.message.chat_id, link_type_id, category_id, public_ack=False)


async def cancellinkbatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _admin_required(update):
        await update.message.reply_text(_admin_group_denied_text(update))
        return
    target_group = _batch_storage_group_id(update)
    if not target_group:
        await update.message.reply_text("Chua cau hinh TELEGRAM_COMMUNITY_GROUP_ID.")
        return
    with _db() as db:
        batch = admin_close_batch(db, update.effective_user.id, target_group, "cancelled")
    await update.message.reply_text("Da huy batch." if batch else "Khong co batch active.")


async def currentlinkbatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _admin_required(update):
        await update.message.reply_text(_admin_group_denied_text(update))
        return
    target_group = _batch_storage_group_id(update)
    if not target_group:
        await update.message.reply_text("Chua cau hinh TELEGRAM_COMMUNITY_GROUP_ID.")
        return
    with _db() as db:
        batch = admin_active_batch_for_admin(db, update.effective_user.id, target_group)
    if not batch:
        await update.message.reply_text("Khong co batch active.")
        return
    await update.message.reply_text(
        f"Batch #{batch.id}\nLoai: {link_type_name(batch.link_type_id)}\nDanh muc: {category_label(batch.category_id)}\nLink: {batch.link_count}"
    )


async def docquyen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _show_request_categories(update, "exclusive_offer")


async def admin_links_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_text(
        "Ban muon xem loai link nao?",
        reply_markup=_link_type_buttons("ac:rt", user.id if user else None),
    )


async def request_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    parts = (query.data or "").split(":")
    if len(parts) != 4:
        return
    requester_id = int(parts[2])
    if not query.from_user or query.from_user.id != requester_id:
        await query.message.reply_text("Menu nay khong phai cua ban.")
        return
    link_type_id = _link_type_from_code(parts[3])
    if not link_type_id:
        await query.message.reply_text("Loai link khong hop le.")
        return
    await _show_request_categories(update, link_type_id, edit=True)


async def _show_request_categories(update: Update, link_type_id: str, edit: bool = False) -> None:
    user = update.effective_user
    if not user:
        return
    with _db() as db:
        cats = admin_categories_for_type(db, link_type_id)
    if not cats:
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text("Hien chua co link active cho muc nay.")
        return
    text = f"Chon danh muc {link_type_name(link_type_id)}:"
    markup = _category_buttons("ac:rc", link_type_id, cats, user.id)
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=markup)
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=markup)
    else:
        await update.message.reply_text(text, reply_markup=markup)


async def guide_links_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if query.data == "ac:docq":
        await _show_request_categories(update, "exclusive_offer")
    else:
        await query.message.reply_text(
            "Ban muon xem loai link nao?",
            reply_markup=_link_type_buttons("ac:rt", query.from_user.id if query.from_user else None),
        )


async def request_category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    parts = (query.data or "").split(":")
    if len(parts) != 5:
        return
    requester_id = int(parts[2])
    if not query.from_user or query.from_user.id != requester_id:
        await query.message.reply_text("Menu nay khong phai cua ban.")
        return
    link_type_id = _link_type_from_code(parts[3])
    category_id = parts[4]
    if not link_type_id or category_id not in valid_category_ids():
        await query.message.reply_text("Loai link hoac danh muc khong hop le.")
        return
    await _deliver_private_links(update, context, query.from_user.id, query.message.chat_id, link_type_id, category_id)


async def _deliver_private_links(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    group_chat_id: int | str,
    link_type_id: str,
    category_id: str,
    public_ack: bool = True,
) -> None:
    query = update.callback_query
    with _db() as db:
        allowed, reason = user_request_allowed(db, user_id)
        if not allowed:
            if public_ack:
                await query.message.reply_text("Ban dang yeu cau qua nhanh, vui long thu lai sau.")
            else:
                await query.answer("Ban dang yeu cau qua nhanh, vui long thu lai sau.", show_alert=True)
            return
        links = get_links_for_delivery(db, link_type_id, category_id, limit=25, hard_cap=25)
    if not links:
        if public_ack:
            await query.message.reply_text("Danh muc nay hien chua co link active.")
        else:
            await query.answer("Danh muc nay hien chua co link active.", show_alert=True)
        return
    with _db() as db:
        request = create_private_request(db, user_id, group_chat_id, link_type_id, category_id)
    try:
        for text in build_private_link_messages(link_type_id, category_id, links):
            await context.bot.send_message(chat_id=user_id, text=text, disable_web_page_preview=True)
        with _db() as db:
            stored = db.get(type(request), request.id)
            if stored:
                complete_private_request(db, stored)
        if public_ack:
            await query.message.reply_text("Minh da gui danh sach vao tin nhan rieng cua ban.")
        else:
            await query.answer("Minh da gui link vao tin nhan rieng cua ban.", show_alert=True)
    except Forbidden:
        username = get_settings().telegram_bot_username.strip().lstrip("@")
        url = f"https://t.me/{username}?start=links_{request.request_token}" if username else ""
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Mo bot de nhan link", url=url)]]) if url else None
        if public_ack:
            await query.message.reply_text("Ban can mo bot va bam Start de nhan link rieng.", reply_markup=keyboard)
        else:
            await query.answer("Hay mo bot va bam Start de nhan link rieng.", show_alert=True)


async def cleanlinks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _admin_required(update):
        await update.message.reply_text("Lenh nay chi danh cho admin.")
        return
    with _db() as db:
        result = cleanup_expired_admin_links(db, preview=False)
    await update.message.reply_text(f"Cleanup xong. Link tat: {result['links_deactivated']}, request het han: {result['requests_expired']}")


async def cleanlinkspreview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _admin_required(update):
        await update.message.reply_text("Lenh nay chi danh cho admin.")
        return
    with _db() as db:
        result = cleanup_expired_admin_links(db, preview=True)
    await update.message.reply_text(f"Preview cleanup. Link se tat: {result['links_deactivated']}, request het han: {result['requests_expired']}")


async def linkstats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _admin_required(update):
        await update.message.reply_text("Lenh nay chi danh cho admin.")
        return
    with _db() as db:
        stats = admin_link_stats(db)
    lines = [f"Tong link active: {stats['total']}", "Theo loai:"]
    lines.extend(f"- {name}: {count}" for name, count in stats["by_type"])
    lines.append("Theo danh muc:")
    lines.extend(f"- {name}: {count}" for name, count in stats["by_category"])
    batch = stats["latest_batch"]
    lines.append(f"Batch gan nhat: #{batch.id} {batch.status} ({batch.link_count} link)" if batch else "Batch gan nhat: chua co")
    lines.append(f"Sap het han: {stats['expiring']}")
    await update.message.reply_text("\n".join(lines))


async def viewlink(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _admin_required(update):
        await update.message.reply_text("Lenh nay chi danh cho admin.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Dung: /viewlink <id>")
        return
    with _db() as db:
        link = db.get(AdminAffiliateLink, int(context.args[0]))
    if not link:
        await update.message.reply_text("Khong tim thay link.")
        return
    await update.message.reply_text(
        f"Link #{link.id}\n"
        f"Ten: {link.display_name}\n"
        f"Loai: {link_type_name(link.link_type_id)}\n"
        f"Danh muc: {category_label(link.category_id)}\n"
        f"Active: {bool(link.is_active)}\n"
        f"Batch: #{link.batch_id}\n"
        f"Het han: {link.expires_at}\n"
        f"URL: {link.affiliate_url}",
        disable_web_page_preview=True,
    )


async def deactivatelink(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_admin_link_active(update, context, False)


async def activatelink(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_admin_link_active(update, context, True)


async def _set_admin_link_active(update: Update, context: ContextTypes.DEFAULT_TYPE, active: bool) -> None:
    if not _admin_required(update):
        await update.message.reply_text("Lenh nay chi danh cho admin.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Dung: /activatelink <id>" if active else "Dung: /deactivatelink <id>")
        return
    with _db() as db:
        link = db.get(AdminAffiliateLink, int(context.args[0]))
        if not link:
            await update.message.reply_text("Khong tim thay link.")
            return
        link.is_active = 1 if active else 0
        db.commit()
    await update.message.reply_text("Da bat link." if active else "Da tat link.")


async def importdaily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    csv_path, raw_date, link_type_id = _parse_importdaily_args(update.message.text if update.message else "")
    if not csv_path:
        await update.message.reply_text("Dung: /importdaily <csv_path> [YYYY-MM-DD|today] [link_type]")
        return
    if link_type_id and link_type_id not in valid_link_type_ids():
        await update.message.reply_text("link_type khong hop le. Dung /linktypes de xem danh sach.")
        return
    csv_path = csv_path.strip().strip('"').strip("'")

    if get_settings().vercel and _looks_like_local_path(csv_path):
        await update.message.reply_text(
            "Vercel khong doc duoc duong dan tren may cua ban.\n"
            "Hay upload truc tiep file CSV vao chat Telegram, bot se import ngay roi xoa file tam.\n"
            "Neu chay local polling thi lenh path nay van dung duoc."
        )
        return

    if _looks_like_local_path(csv_path) and not Path(csv_path).expanduser().exists():
        await update.message.reply_text(
            "Khong tim thay file CSV o duong dan nay. Neu bot dang chay tren Vercel, hay upload truc tiep file CSV vao Telegram."
        )
        return

    await update.message.reply_text("Dang import link vao kho theo ngay...")
    try:
        with _db() as db:
            result = import_daily_csv(db, csv_path, raw_date, default_link_type_id=link_type_id)
    except Exception as exc:
        await update.message.reply_text(f"Import daily loi: {exc}")
        return
    cleaned = (result.cleanup or {}).get("entries_deleted", 0)
    await update.message.reply_text(
        f"Ngay nhap: {display_date(result.import_date)}\n"
        f"Tong dong: {result.total_rows}\n"
        f"San pham moi: {result.new_products}\n"
        f"Entry moi: {result.new_entries}\n"
        f"Duplicate trong ngay: {result.duplicate_count}\n"
        f"Link cu da don: {cleaned}"
    )


async def importdaily_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.document:
        return
    document = message.document
    file_name = document.file_name or "daily-links.csv"
    if not file_name.lower().endswith(".csv"):
        return
    raw_date = None
    link_type_id = None
    if message.caption and message.caption.strip().startswith("/importdaily"):
        raw_date, link_type_id = _parse_importdaily_upload_caption(message.caption)
        if link_type_id and link_type_id not in valid_link_type_ids():
            await message.reply_text("link_type khong hop le. Dung /linktypes de xem danh sach.")
            return
    await message.reply_text("Da nhan CSV. Dang tai file va import vao kho link...")
    temp_path = ""
    try:
        tg_file = await document.get_file()
        suffix = "-" + file_name
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            temp_path = tmp.name
        await tg_file.download_to_drive(custom_path=temp_path)
        with _db() as db:
            result = import_daily_csv(db, temp_path, raw_date, default_link_type_id=link_type_id)
        cleaned = (result.cleanup or {}).get("entries_deleted", 0)
        await message.reply_text(
            f"Import CSV upload xong.\n"
            f"Ngay nhap: {display_date(result.import_date)}\n"
            f"Tong dong: {result.total_rows}\n"
            f"San pham moi: {result.new_products}\n"
            f"Entry moi: {result.new_entries}\n"
            f"Duplicate trong ngay: {result.duplicate_count}\n"
            f"Link cu da don: {cleaned}"
        )
    except Exception as exc:
        await message.reply_text(f"Import CSV upload loi: {exc}")
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                pass


async def adddailylink(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.partition(" ")[2].strip()
    if not text:
        await update.message.reply_text("Dung: /adddailylink <url> | <name> | <price>")
        return
    try:
        with _db() as db:
            result = add_daily_product(db, text)
        if get_settings().enable_daily_link_auto_cleanup:
            cleanup_expired_daily_links(get_settings().daily_link_retention_days)
    except Exception as exc:
        await update.message.reply_text(f"Chua them duoc link: {exc}")
        return
    await update.message.reply_text(
        f"Da them link cho ngay {display_date(result.import_date)}. Entry moi: {result.new_entries}, duplicate: {result.duplicate_count}"
    )


async def adddailytext(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await adddailylink(update, context)


async def dailystats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw_date = context.args[0] if context.args else None
    try:
        with _db() as db:
            stats = daily_catalog_stats(db, raw_date)
    except Exception as exc:
        await update.message.reply_text(f"Khong doc duoc stats: {exc}")
        return
    lines = [
        f"Ngay {display_date(stats['import_date'])}",
        f"Tong link: {stats['active_entries']}",
        "",
    ]
    for type_item in stats.get("types", []):
        lines.append(f"{type_item['link_type_name']}: {type_item['count']}")
        for item in type_item.get("categories", []):
            lines.append(f"- {item['label']}: {item['count']}")
        lines.append("")
    lines.append(f"Khong xac dinh danh muc: {stats.get('unknown_categories', 0)}")
    await update.message.reply_text("\n".join(lines))


async def linktypes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = ["Loai link co the dung khi import:"]
    for item in load_link_types():
        lines.append(f"- {item['name']}: {item['id']}")
    await update.message.reply_text("\n".join(lines))


async def cleanuppreview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = cleanup_expired_daily_links(get_settings().daily_link_retention_days, preview=True)
    await update.message.reply_text(_cleanup_result_text(result, "Preview cleanup daily"))


async def cleanupdaily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = cleanup_expired_daily_links(get_settings().daily_link_retention_days)
    await update.message.reply_text(_cleanup_result_text(result, "Cleanup daily xong"))


def _cleanup_result_text(result: dict, title: str) -> str:
    return (
        f"{title}\n"
        f"cutoff_date: {result.get('cutoff_date')}\n"
        f"entries_deleted: {result.get('entries_deleted')}\n"
        f"batches_deleted: {result.get('batches_deleted')}\n"
        f"orphan_products_deleted: {result.get('orphan_products_deleted')}\n"
        f"errors: {', '.join(result.get('errors') or []) or 'khong co'}"
    )


async def links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with _db() as db:
        dates = recent_dates(db, limit=get_settings().daily_link_retention_days)
    if not dates:
        await update.message.reply_text("Hien chua co link uu dai nao trong 4 ngay gan nhat.")
        return
    buttons = [
        [InlineKeyboardButton(short_display_date(item), callback_data=f"dl:d:{compact_date(item)}") for item in dates[index : index + 2]]
        for index in range(0, len(dates), 2)
    ]
    await update.message.reply_text("Chon ngay cap nhat link:", reply_markup=InlineKeyboardMarkup(buttons))


async def daily_date_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ""
    if data.startswith("daily_date:"):
        import_date = data.split(":", 1)[1]
    else:
        try:
            import_date = expand_date(data.split(":", 2)[2])
        except Exception:
            await query.message.reply_text("Ngay khong hop le.")
            return
    try:
        parse_import_date(import_date)
    except ValueError:
        await query.message.reply_text("Ngay khong hop le.")
        return
    with _db() as db:
        types = get_link_types_for_date(db, import_date)
    if not types:
        await query.edit_message_text(f"Ngay {display_date(import_date)} chua co link active.")
        return
    await query.edit_message_text(
        f"Chon loai link ngay {short_display_date(import_date)}:",
        reply_markup=link_type_keyboard(import_date, types),
    )


async def daily_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    try:
        import_date, link_type_id = parse_type_callback(query.data or "")
    except Exception:
        await query.message.reply_text("Loai link khong hop le.")
        return
    with _db() as db:
        cats = get_categories_for_date_and_type(db, import_date, link_type_id)
    if not cats:
        await query.edit_message_text(f"{link_type_name(link_type_id)} ngay {display_date(import_date)} chua co link active.")
        return
    await query.edit_message_text(
        f"Danh muc - {link_type_name(link_type_id)} - {short_display_date(import_date)}:",
        reply_markup=category_keyboard(import_date, link_type_id, cats),
    )


async def daily_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
        with _db() as db:
            dates = recent_dates(db, limit=get_settings().daily_link_retention_days)
        if not dates:
            await query.edit_message_text("Hien chua co link uu dai nao trong 4 ngay gan nhat.")
            return
        buttons = [
            [InlineKeyboardButton(short_display_date(item), callback_data=f"dl:d:{compact_date(item)}") for item in dates[index : index + 2]]
            for index in range(0, len(dates), 2)
        ]
        await query.edit_message_text("Chon ngay cap nhat link:", reply_markup=InlineKeyboardMarkup(buttons))


async def daily_category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    try:
        if (query.data or "").startswith("daily_cat:"):
            parts = (query.data or "").split(":", 2)
            import_date, category_id = parts[1], parts[2]
            link_type_id = "shopee_commission"
            page = 1
        else:
            import_date, link_type_id, category_id = parse_category_callback(query.data or "")
            page = 1
    except Exception:
        await query.message.reply_text("Callback danh muc khong hop le.")
        return
    await _send_daily_products(update, context, import_date, link_type_id, category_id, page)


async def daily_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    try:
        import_date, link_type_id, category_id, page = parse_page_callback(query.data or "")
    except Exception:
        await query.message.reply_text("Trang khong hop le.")
        return
    await _send_daily_products(update, context, import_date, link_type_id, category_id, page)


async def _send_daily_products(update: Update, context: ContextTypes.DEFAULT_TYPE, import_date: str, link_type_id: str, category_id: str, page: int = 1) -> None:
    query = update.callback_query
    if not query:
        return
    try:
        parse_import_date(import_date)
    except ValueError:
        await query.message.reply_text("Ngay khong hop le.")
        return
    if link_type_id not in valid_link_type_ids():
        await query.message.reply_text("Loai link khong hop le.")
        return
    if category_id not in valid_category_ids():
        await query.message.reply_text("Danh muc khong hop le.")
        return

    settings = get_settings()
    source_chat_id = query.message.chat_id if query.message else 0
    configured_group = _telegram_group_target()
    target_chat_id = source_chat_id if isinstance(configured_group, int) and source_chat_id == configured_group else configured_group
    if not target_chat_id:
        await query.message.reply_text("Chua cau hinh TELEGRAM_COMMUNITY_GROUP_ID.")
        return

    key = (query.from_user.id if query.from_user else 0, import_date, link_type_id, category_id, page, target_chat_id)
    now = time.time()
    if now - DAILY_SEND_COOLDOWNS.get(key, 0) < settings.daily_group_send_cooldown_seconds:
        await query.message.reply_text("Danh muc nay vua duoc gui vao group, ban xem tin nhan moi nhat nhe.")
        return
    DAILY_SEND_COOLDOWNS[key] = now

    with _db() as db:
        page_size = max(1, min(10, settings.daily_max_products_per_send))
        result = get_products_for_date_type_category(db, import_date, link_type_id, category_id, page=page, page_size=page_size)
        products = result["products"]
    if not products:
        await query.message.reply_text("Danh muc nay chua co link active.")
        return
    for message in build_product_messages(import_date, link_type_id, category_id, products):
        await context.bot.send_message(
            chat_id=target_chat_id,
            text=message,
            disable_web_page_preview=settings.telegram_daily_disable_link_preview,
        )
    keyboard = pagination_keyboard(import_date, link_type_id, category_id, result["page"], result["has_next"])
    await query.message.reply_text(
        f"Da gui {len(products)} link {category_label(category_id)} / {link_type_name(link_type_id)} vao group.",
        reply_markup=keyboard,
    )


async def recategorize(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Dung: /recategorize <link_id> <category_id>")
        return
    with _db() as db:
        ok = recategorize_product(db, int(context.args[0]), context.args[1])
    await update.message.reply_text("Da doi danh muc." if ok else "Khong tim thay link hoac category khong hop le.")


async def retype(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Dung: /retype <product_id> <link_type_id>")
        return
    with _db() as db:
        ok = update_product_link_type(db, int(context.args[0]), context.args[1])
    await update.message.reply_text("Da doi loai link." if ok else "Khong tim thay link hoac link_type khong hop le.")


async def viewproduct(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Dung: /viewproduct <product_id>")
        return
    with _db() as db:
        product = db.get(AffiliateProduct, int(context.args[0]))
        if not product:
            await update.message.reply_text("Khong tim thay product.")
            return
        latest_date = db.scalar(
            select(DailyLinkEntry.import_date)
            .where(DailyLinkEntry.product_id == product.id)
            .order_by(DailyLinkEntry.import_date.desc())
            .limit(1)
        )
        text = (
            f"Product #{product.id}\n"
            f"Ten: {product.product_name}\n"
            f"Loai: {link_type_name(product.link_type_id)} ({product.link_type_id})\n"
            f"Danh muc: {category_label(product.category_id)} ({product.category_id})\n"
            f"Gia: {product.price or 'khong co'}\n"
            f"Shop: {product.shop_name or 'khong co'}\n"
            f"Ngay gan nhat: {display_date(latest_date) if latest_date else 'chua co'}\n"
            f"Link: {product.affiliate_url}"
        )
    await update.message.reply_text(text, disable_web_page_preview=True)


async def deactivatedaily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Dung: /deactivatedaily <link_id>")
        return
    with _db() as db:
        ok = set_daily_product_active(db, int(context.args[0]), False)
    await update.message.reply_text("Da tat link." if ok else "Khong tim thay link.")


async def activatedaily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Dung: /activatedaily <link_id>")
        return
    with _db() as db:
        ok = set_daily_product_active(db, int(context.args[0]), True)
    await update.message.reply_text("Da bat lai link." if ok else "Khong tim thay link.")


async def senddaily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 3:
        await update.message.reply_text("Dung: /senddaily <date> <link_type_id> <category_id> [group|channel]")
        return
    try:
        import_date = parse_import_date(context.args[0])
    except ValueError:
        await update.message.reply_text("Ngay phai la YYYY-MM-DD hoac DD/MM/YYYY.")
        return
    link_type_id = context.args[1]
    category_id = context.args[2]
    if link_type_id not in valid_link_type_ids() or category_id not in valid_category_ids():
        await update.message.reply_text("link_type hoac category khong hop le. Dung /linktypes de xem link_type.")
        return
    settings = get_settings()
    target_chat_id = _telegram_group_target()
    if not target_chat_id:
        await update.message.reply_text("Chua cau hinh TELEGRAM_COMMUNITY_GROUP_ID.")
        return
    with _db() as db:
        page_size = max(1, min(10, settings.daily_max_products_per_send))
        result = get_products_for_date_type_category(db, import_date, link_type_id, category_id, page=1, page_size=page_size)
        products = result["products"]
    if not products:
        await update.message.reply_text("Khong co product active cho ngay/category nay.")
        return
    for message in build_product_messages(import_date, link_type_id, category_id, products):
        await context.bot.send_message(
            chat_id=target_chat_id,
            text=message,
            disable_web_page_preview=settings.telegram_daily_disable_link_preview,
        )
    await update.message.reply_text(f"Da gui {len(products)} link vao group.")


async def sendtoday(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await links(update, context)


async def checktelegramcta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    try:
        preview = generate_telegram_cta({}, settings.threads_telegram_group_url or settings.telegram_group_invite_url, [])
    except Exception as exc:
        preview = f"CTA chua san sang: {exc}"
    await update.message.reply_text(
        f"Telegram CTA\nmode: {settings.threads_telegram_cta_mode}\n"
        f"group_name: {settings.threads_telegram_group_name}\n"
        f"url_configured: {'yes' if (settings.threads_telegram_group_url or settings.telegram_group_invite_url) else 'no'}\n"
        f"preview:\n{preview}"
    )


def _recent_telegram_ctas(db: Session, limit: int = 5) -> list[str]:
    return [
        str(item)
        for item in db.scalars(
            select(ThreadsPost.telegram_cta_text)
            .where(ThreadsPost.telegram_cta_text.is_not(None), ThreadsPost.telegram_cta_text != "")
            .order_by(ThreadsPost.id.desc())
            .limit(limit)
        )
    ]


def _should_post_telegram_cta(post: ThreadsPost) -> bool:
    settings = get_settings()
    mode = settings.threads_telegram_cta_mode.lower().strip()
    return (
        settings.threads_include_telegram_cta
        and mode in {"reply", "both", "main_post"}
        and (post.content_goal == "engagement" or post.content_type == "engagement" or post.status == "engagement")
    )


async def _publish_telegram_cta_reply(db: Session, post: ThreadsPost, account: dict) -> tuple[bool, str]:
    settings = get_settings()
    mode = settings.threads_telegram_cta_mode.lower().strip()
    if mode in {"none", "main_post"} or not _should_post_telegram_cta(post):
        post.telegram_cta_status = "disabled"
        post.telegram_cta_mode = mode
        db.commit()
        return True, "CTA disabled"
    group_url = settings.threads_telegram_group_url or settings.telegram_group_invite_url
    cta = generate_telegram_cta(_post_account_payload(post), group_url, _recent_telegram_ctas(db))
    post.telegram_cta_text = cta
    post.telegram_cta_mode = mode
    if not post.threads_post_id:
        post.telegram_cta_status = "failed"
        db.commit()
        return False, "missing Threads post id"
    try:
        result = publish_threads_reply(post.threads_post_id, cta, account=account)
        post.telegram_cta_reply_id = str(result.get("id") or result.get("post_id") or "")
        post.telegram_cta_posted_at = datetime.now()
        post.telegram_cta_status = "posted"
        db.commit()
        return True, "CTA reply posted"
    except Exception as exc:
        post.telegram_cta_status = "failed"
        db.commit()
        return False, str(exc)


async def retrytelegramcta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Dung: /retrytelegramcta <post_id>")
        return
    with _db() as db:
        post = get_post(db, int(context.args[0]))
        if not post or not post.threads_post_id:
            await update.message.reply_text("Post chua co Threads ID.")
            return
        try:
            account = get_threads_account(post.posted_account_name) if post.posted_account_name else get_threads_account(None)
        except ThreadsAccountError as exc:
            await update.message.reply_text(f"Khong lay duoc account: {exc}")
            return
        ok, message = await _publish_telegram_cta_reply(db, post, account)
    await update.message.reply_text(("Da retry CTA: " if ok else "Retry CTA loi: ") + message)


def build_application() -> Application:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not found")

    init_db()
    if settings.enable_daily_link_auto_cleanup and not settings.vercel:
        cleanup_expired_daily_links(settings.daily_link_retention_days)
    if settings.enable_daily_link_cleanup and not settings.vercel:
        with _db() as db:
            cleanup_expired_admin_links(db)
    if not settings.vercel and settings.enable_csv_daily_import:
        run_startup_import_once()
    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("links", admin_links_menu))
    app.add_handler(CommandHandler("docquyen", docquyen))
    app.add_handler(CommandHandler("linkbatch", linkbatch))
    app.add_handler(CommandHandler("endlinkbatch", endlinkbatch))
    app.add_handler(CommandHandler("cancellinkbatch", cancellinkbatch))
    app.add_handler(CommandHandler("currentlinkbatch", currentlinkbatch))
    app.add_handler(CommandHandler("importlinks", importlinks))
    app.add_handler(CommandHandler("endimportlinks", endimportlinks))
    app.add_handler(CommandHandler("publishlinks", publishlinks))
    app.add_handler(CommandHandler("linkstats", linkstats))
    app.add_handler(CommandHandler("cleanlinks", cleanlinks))
    app.add_handler(CommandHandler("cleanlinkspreview", cleanlinkspreview))
    app.add_handler(CommandHandler("viewlink", viewlink))
    app.add_handler(CommandHandler("deactivatelink", deactivatelink))
    app.add_handler(CommandHandler("activatelink", activatelink))
    app.add_handler(CommandHandler("accounts", accounts))
    app.add_handler(CommandHandler("chatid", chatid))
    app.add_handler(CommandHandler("features", features))
    app.add_handler(CommandHandler("checktelegramcta", checktelegramcta))
    app.add_handler(CommandHandler("engagepost", engagepost))
    app.add_handler(CallbackQueryHandler(choose_engagement_persona, pattern=r"^engage_persona:(daily|controversial|advisor)$"))
    app.add_handler(CallbackQueryHandler(choose_engagement_mode, pattern=r"^engage_mode:(viral|advice|ask|quote|observation)$"))
    app.add_handler(CallbackQueryHandler(choose_batch_type, pattern=r"^ac:bt:(sh|xt|pc|ex)$"))
    app.add_handler(CallbackQueryHandler(choose_batch_category, pattern=r"^ac:bc:(sh|xt|pc|ex):[a-z_]+$"))
    app.add_handler(CallbackQueryHandler(publish_type_callback, pattern=r"^ac:pt:(sh|xt|pc|ex)$"))
    app.add_handler(CallbackQueryHandler(publish_category_callback, pattern=r"^ac:pc:(sh|xt|pc|ex):[a-z_]+$"))
    app.add_handler(CallbackQueryHandler(channel_get_links_callback, pattern=r"^ac:get:(sh|xt|pc|ex):[a-z_]+$"))
    app.add_handler(CallbackQueryHandler(request_type_callback, pattern=r"^ac:rt:\d+:(sh|xt|pc|ex)$"))
    app.add_handler(CallbackQueryHandler(request_category_callback, pattern=r"^ac:rc:\d+:(sh|xt|pc|ex):[a-z_]+$"))
    app.add_handler(CallbackQueryHandler(guide_links_callback, pattern=r"^ac:(docq|links)$"))
    app.add_handler(CallbackQueryHandler(daily_date_callback, pattern=r"^dl:d:\d{8}$"))
    app.add_handler(CallbackQueryHandler(daily_type_callback, pattern=r"^dl:t:\d{8}:(sh|xt|pc|ex)$"))
    app.add_handler(CallbackQueryHandler(daily_category_callback, pattern=r"^dl:c:\d{8}:(sh|xt|pc|ex):[a-z_]+$"))
    app.add_handler(CallbackQueryHandler(daily_page_callback, pattern=r"^dl:p:\d{8}:(sh|xt|pc|ex):[a-z_]+:\d+$"))
    app.add_handler(CallbackQueryHandler(daily_back_callback, pattern=r"^dl:back:dates$"))
    app.add_handler(CallbackQueryHandler(daily_date_callback, pattern=r"^daily_date:\d{4}-\d{2}-\d{2}$"))
    app.add_handler(CallbackQueryHandler(daily_category_callback, pattern=r"^daily_cat:\d{4}-\d{2}-\d{2}:[a-z_]+$"))
    app.add_handler(CallbackQueryHandler(daily_back_callback, pattern=r"^daily_back$"))
    app.add_handler(CommandHandler("threads_shopee", threads_shopee))
    app.add_handler(CommandHandler("queue", queue))
    app.add_handler(CommandHandler("view", view))
    app.add_handler(CommandHandler("regenerate", regenerate))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("post", post_to_threads))
    app.add_handler(CommandHandler("threadpost", threadpost))
    app.add_handler(CommandHandler("supportpost", threadpost))
    app.add_handler(CommandHandler("retrytelegramcta", retrytelegramcta))
    app.add_handler(CommandHandler("delete", delete))
    app.add_handler(MessageHandler(filters.Document.ALL, importlinks_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ingest_admin_link_message))
    for command in FROZEN_COMMANDS:
        app.add_handler(CommandHandler(command, frozen_command))
    return app
