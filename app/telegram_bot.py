import random
import time
import json
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.orm import Session
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from agents.threads_shopee_agent import (
    check_model_availability,
    generate_threads_engagement_draft,
    generate_threads_shopee_draft,
    model_status_snapshot,
)
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import ThreadsPost
from app.schemas import ThreadsDraftRequest
from app.services.shopee_csv_importer import import_shopee_csv, scan_shopee_csv
from app.services.threads_repository import (
    add_affiliate_link,
    analytics_summary,
    create_group_post,
    create_post,
    find_catalog_links,
    get_post,
    get_post_links,
    hashtags_from_json,
    list_catalog_links,
    list_posts_by_status,
    list_recent_posts,
    update_draft_content,
    update_status,
)
from app.services.threads_service import (
    ThreadsPostingError,
    create_post as publish_threads_post,
    create_reply as publish_threads_reply,
)

PENDING_UPDATES: dict[int, tuple[str, int]] = {}
PENDING_ENGAGEMENT_POSTS: dict[int, dict[str, str]] = {}
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


def _post_meta(post: ThreadsPost) -> dict[str, str]:
    if not post.cta.startswith("__meta__"):
        return {}
    try:
        value = json.loads(post.cta.removeprefix("__meta__"))
    except json.JSONDecodeError:
        return {}
    return {str(key): str(val) for key, val in value.items()}


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


def _import_limit() -> int | None:
    limit = get_settings().import_generate_limit
    return limit if limit > 0 else None


def _queue_status_text(db: Session) -> str:
    summary = analytics_summary(db)
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
        f"Gan day:\n{recent_text}"
    )


async def _reply_status(update: Update) -> None:
    with _db() as db:
        await update.message.reply_text(_queue_status_text(db))


def _model_row_text(row: dict) -> str:
    provider = str(row.get("provider", "unknown"))
    status = str(row.get("status", "unknown"))
    cooldown = int(row.get("cooldown_seconds", 0) or 0)
    detail = str(row.get("detail", "") or "")
    latency = row.get("latency_ms")

    suffix = ""
    if cooldown:
        suffix = f" (~{cooldown}s)"
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
        """/threads_shopee <keyword hoặc Shopee affiliate link>
/updatelink <csv_path> [group_size] - quét CSV, chưa import
/confirmupdate - nhập các link vừa quét vào queue
/cancelupdate - hủy lần quét CSV hiện tại
/status
/queue
/autodrafts [limit] [keyword]
/engagepost <topic>
/view <post_id>
/regenerate <post_id>
/refreshdrafts [limit]
/modelstatus
/checkmodels [limit]
/approve <post_id>
/post <post_id>
/replylinks <post_id>
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
            draft = generate_threads_shopee_draft(
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

    PENDING_ENGAGEMENT_POSTS[user_id] = {"topic": topic, "persona": "daily"}
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

    pending = PENDING_ENGAGEMENT_POSTS.get(user_id)
    if not pending:
        await query.message.reply_text("Khong con bai cau view nao dang cho chon persona. Hay gui lai /engagepost <topic>.")
        return

    persona = (query.data or "").split(":", 1)[1]
    if persona not in ENGAGEMENT_PERSONA_LABELS:
        persona = "daily"
    pending["persona"] = persona

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

    pending = PENDING_ENGAGEMENT_POSTS.get(user_id)
    if not pending:
        await query.message.reply_text("Khong con bai cau view nao dang cho chon dang bai. Hay gui lai /engagepost <topic>.")
        return

    mode = (query.data or "").split(":", 1)[1]
    if mode not in ENGAGEMENT_MODE_LABELS:
        mode = "viral"
    pending["mode"] = mode

    persona_name = _engagement_persona_name(pending.get("persona", "daily"))
    mode_name = _engagement_mode_name(mode)
    await query.edit_message_text(
        f"Persona: {persona_name}\nDang bai: {mode_name}\nBai nay co gan 2-3 link random o comment khong?",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Co gan link", callback_data="engage_links:yes"),
                    InlineKeyboardButton("Khong gan link", callback_data="engage_links:no"),
                ]
            ]
        ),
    )


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
            )
        else:
            post = create_post(
                db,
                keyword=topic,
                product_name="engagement-only",
                affiliate_url=None,
                draft=draft,
                status="engagement",
            )
        await query.message.reply_text(_preview(post))
        if selected_links:
            await query.message.reply_text(f"Da gan {len(selected_links)} link random de comment khi /post.")
        elif include_links:
            await query.message.reply_text("Chua co link trong catalog nen bai nay se dang khong kem comment link.")
        else:
            await query.message.reply_text("Bai cau view nay se dang khong kem comment link.")
        await query.message.reply_text(_queue_status_text(db))


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

        draft = generate_threads_shopee_draft(
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
            )
        else:
            post = create_post(
                db,
                keyword=keyword,
                product_name=product_name,
                affiliate_url=affiliate_url,
                draft=draft,
                status="draft" if affiliate_url else "needs_link",
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
        post = get_post(db, post_id)
        if not post or post.status == "deleted":
            await update.message.reply_text(f"Không tìm thấy post #{post_id}.")
            return

        if post.status == "engagement":
            meta = _post_meta(post)
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
        else:
            links = get_post_links(db, post.id)
            first_link = links[0] if links else None
            draft = generate_threads_shopee_draft(
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
        updated = update_draft_content(db, post_id, draft)
        await update.message.reply_text(_preview(updated))
        await _reply_status(update)


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
            draft = generate_threads_shopee_draft(
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
        await update.message.reply_text("Dùng: /post <post_id>")
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

        await update.message.reply_text("Đang đăng bài lên Threads...")

        try:
            result = publish_threads_post(_thread_text(post))
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
                    publish_threads_reply(threads_post_id, reply_text)
                    reply_count += 1
                    time.sleep(2)
            except ThreadsPostingError as exc:
                failed_replies.append(str(exc))

            if failed_replies:
                reply_message = f"\nBài chính đã đăng. Đã gắn {reply_count} bình luận link. Lỗi đầu tiên: {failed_replies[0]}"
            else:
                reply_message = f"\nĐã gắn {reply_count} bình luận link."

        post.threads_post_id = threads_post_id
        post.status = "posted"
        db.commit()
        db.refresh(post)

        await update.message.reply_text(f"Đã đăng Threads cho post #{post.id}.\nThreads ID: {post.threads_post_id or 'unknown'}{reply_message}")
        await _reply_status(update)


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

        await update.message.reply_text("Đang gắn link vào bình luận...")
        reply_count = 0
        try:
            for reply_text in _reply_link_texts(post):
                publish_threads_reply(post.threads_post_id, reply_text)
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
Top 5 bài nhiều click nhất:
{top}"""
    )


def build_application() -> Application:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not found")

    init_db()
    run_startup_import_once()
    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("importcsv", importcsv))
    app.add_handler(CommandHandler("updatelink", updatelink))
    app.add_handler(CommandHandler("confirmupdate", confirmupdate))
    app.add_handler(CommandHandler("cancelupdate", cancelupdate))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("modelstatus", modelstatus))
    app.add_handler(CommandHandler("checkmodels", checkmodels))
    app.add_handler(CommandHandler("autodrafts", autodrafts))
    app.add_handler(CommandHandler("engagepost", engagepost))
    app.add_handler(CallbackQueryHandler(choose_engagement_persona, pattern=r"^engage_persona:(daily|controversial|advisor)$"))
    app.add_handler(CallbackQueryHandler(choose_engagement_mode, pattern=r"^engage_mode:(viral|advice|ask|quote|observation)$"))
    app.add_handler(CallbackQueryHandler(choose_engagement_links, pattern=r"^engage_links:(yes|no)$"))
    app.add_handler(CommandHandler("threads_shopee", threads_shopee))
    app.add_handler(CommandHandler("addlink", addlink))
    app.add_handler(CommandHandler("queue", queue))
    app.add_handler(CommandHandler("view", view))
    app.add_handler(CommandHandler("regenerate", regenerate))
    app.add_handler(CommandHandler("refreshdrafts", refreshdrafts))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("post", post_to_threads))
    app.add_handler(CommandHandler("replylinks", replylinks))
    app.add_handler(CommandHandler("delete", delete))
    app.add_handler(CommandHandler("analytics", analytics))
    return app
