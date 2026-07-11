from __future__ import annotations

import html
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import get_settings
from app.models import AffiliateProduct
from app.services.affiliate_link_type_classifier import (
    link_type_id_from_code,
    link_type_name,
    short_code_for_type,
)
from app.services.product_category_classifier import category_label


def compact_date(import_date: str) -> str:
    return import_date.replace("-", "")


def expand_date(compact: str) -> str:
    if len(compact) != 8 or not compact.isdigit():
        raise ValueError("invalid compact date")
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"


def display_date(import_date: str) -> str:
    return datetime.strptime(import_date, "%Y-%m-%d").strftime("%d/%m/%Y")


def short_display_date(import_date: str) -> str:
    return datetime.strptime(import_date, "%Y-%m-%d").strftime("%d/%m")


def link_type_keyboard(import_date: str, types: list[dict]) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                f"{item['link_type_name']} ({item['count']})",
                callback_data=f"dl:t:{compact_date(import_date)}:{short_code_for_type(item['link_type_id'])}",
            )
        ]
        for item in types
    ]
    buttons.append([InlineKeyboardButton("<- Chọn ngày khác", callback_data="dl:back:dates")])
    return InlineKeyboardMarkup(buttons)


def category_keyboard(import_date: str, link_type_id: str, categories: list[dict]) -> InlineKeyboardMarkup:
    code = short_code_for_type(link_type_id)
    buttons = [
        [
            InlineKeyboardButton(
                f"{item['label']} ({item['count']})",
                callback_data=f"dl:c:{compact_date(import_date)}:{code}:{item['category_id']}",
            )
        ]
        for item in categories
    ]
    buttons.append([InlineKeyboardButton("<- Chọn loại link", callback_data=f"dl:d:{compact_date(import_date)}")])
    return InlineKeyboardMarkup(buttons)


def pagination_keyboard(import_date: str, link_type_id: str, category_id: str, page: int, has_next: bool) -> InlineKeyboardMarkup | None:
    buttons = []
    code = short_code_for_type(link_type_id)
    if page > 1:
        buttons.append(InlineKeyboardButton("Trang trước", callback_data=f"dl:p:{compact_date(import_date)}:{code}:{category_id}:{page - 1}"))
    if has_next and get_settings().daily_enable_pagination:
        buttons.append(InlineKeyboardButton("Trang tiếp", callback_data=f"dl:p:{compact_date(import_date)}:{code}:{category_id}:{page + 1}"))
    if not buttons:
        return None
    return InlineKeyboardMarkup([buttons])


def parse_type_callback(data: str) -> tuple[str, str]:
    _, _, compact, code = data.split(":", 3)
    link_type_id = link_type_id_from_code(code)
    if not link_type_id:
        raise ValueError("invalid link type")
    return expand_date(compact), link_type_id


def parse_category_callback(data: str) -> tuple[str, str, str]:
    _, _, compact, code, category_id = data.split(":", 4)
    link_type_id = link_type_id_from_code(code)
    if not link_type_id:
        raise ValueError("invalid link type")
    return expand_date(compact), link_type_id, category_id


def parse_page_callback(data: str) -> tuple[str, str, str, int]:
    _, _, compact, code, category_id, page = data.split(":", 5)
    link_type_id = link_type_id_from_code(code)
    if not link_type_id:
        raise ValueError("invalid link type")
    return expand_date(compact), link_type_id, category_id, max(1, int(page))


def build_product_messages(
    import_date: str,
    link_type_id: str,
    category_id: str,
    products: list[AffiliateProduct],
) -> list[str]:
    settings = get_settings()
    per_message = max(1, min(5, settings.daily_links_per_message))
    chunks = [products[index : index + per_message] for index in range(0, len(products), per_message)]
    messages: list[str] = []
    for chunk_index, chunk in enumerate(chunks, start=1):
        offset = (chunk_index - 1) * per_message
        lines = [
            f"{category_label(category_id)}",
            f"Loại: {link_type_name(link_type_id)}",
            f"Cập nhật ngày {display_date(import_date)}",
            "",
        ]
        for index, product in enumerate(chunk, start=1 + offset):
            lines.append(f"{index}. {html.escape(product.product_name)}")
            if product.price:
                lines.append(f"Giá: {html.escape(product.price)}")
            lines.append(f"Link: {html.escape(product.affiliate_url)}")
            lines.append("")
        lines.append(settings.telegram_daily_link_disclosure)
        messages.append("\n".join(lines).strip())
    return messages
