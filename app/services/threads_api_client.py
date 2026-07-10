from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import httpx

from app.config import get_settings


class ThreadsApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, code: str = "api_error", optional: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.optional = optional


class ThreadsPermissionError(ThreadsApiError):
    pass


class ThreadsTokenExpiredError(ThreadsApiError):
    pass


class ThreadsRateLimitError(ThreadsApiError):
    pass


OPTIONAL_EMPTY_CODES = {"permission_denied", "token_expired", "rate_limited", "temporary_error"}


def get_user_threads(account: dict, limit: int = 25, since: datetime | None = None) -> list[dict]:
    params: dict[str, Any] = {
        "fields": "id,text,media_type,permalink,timestamp,username",
        "limit": max(1, min(limit, 100)),
    }
    if since:
        params["since"] = int(since.timestamp())
    return _paged_get(account, f"/{_user_id(account)}/threads", params=params, limit=limit, optional=True)


def get_post_insights(account: dict, threads_media_id: str) -> dict:
    metrics = "views,likes,replies,reposts,quotes,shares"
    data = _get(account, f"/{threads_media_id}/insights", params={"metric": metrics}, optional=True)
    return _flatten_insights(data)


def get_profile_insights(account: dict) -> dict:
    metrics = "views,likes,replies,reposts,quotes"
    data = _get(account, f"/{_user_id(account)}/threads_insights", params={"metric": metrics}, optional=True)
    return _flatten_insights(data)


def get_post_replies(account: dict, threads_media_id: str, limit: int = 100) -> list[dict]:
    params = {
        "fields": "id,text,username,timestamp,permalink",
        "limit": max(1, min(limit, 100)),
    }
    return _paged_get(account, f"/{threads_media_id}/replies", params=params, limit=limit, optional=True)


def search_threads_keywords(account: dict, keyword: str, limit: int = 50) -> list[dict]:
    params = {
        "q": keyword,
        "fields": "id,text,timestamp,username",
        "search_type": "TOP",
        "search_mode": "KEYWORD",
        "limit": max(1, min(limit, 100)),
    }
    return _paged_get(account, "/keyword_search", params=params, limit=limit, optional=True)


def search_keyword(account: dict, keyword: str, limit: int = 50) -> list[dict]:
    errors: list[str] = []
    for search_type in ("TOP", "RECENT"):
        params = {
            "q": keyword,
            "search_type": search_type,
            "search_mode": "KEYWORD",
            "fields": "id,media_product_type,media_type,permalink,username,text,timestamp,shortcode,is_quote_post,has_replies",
            "limit": max(1, min(limit, 100)),
        }
        try:
            return _paged_get(account, "/keyword_search", params=params, limit=limit, optional=False)
        except ThreadsApiError as exc:
            errors.append(f"{search_type}: {exc}")
            if exc.code not in {"temporary_error", "rate_limited"}:
                raise
    raise ThreadsApiError("; ".join(errors), code="temporary_error")


def publish_reply(account: dict, reply_to_id: str, text: str) -> dict:
    from app.services.threads_service import create_reply

    return create_reply(reply_to_id, text, account=account)


def get_mentions(account: dict, limit: int = 50) -> list[dict]:
    params = {
        "fields": "id,text,username,timestamp,permalink",
        "limit": max(1, min(limit, 100)),
    }
    return _paged_get(account, f"/{_user_id(account)}/mentions", params=params, limit=limit, optional=True)


def delete_thread_post(account: dict, threads_media_id: str) -> bool:
    try:
        data = _delete(account, f"/{threads_media_id}", optional=True)
    except ThreadsApiError:
        return False
    if isinstance(data, dict):
        return bool(data.get("success", True))
    return True


def _base_url() -> str:
    return get_settings().threads_api_base_url.rstrip("/")


def _token(account: dict) -> str:
    value = str(account.get("access_token") or "").strip()
    if not value:
        raise ThreadsApiError("Threads account is missing access_token.", code="missing_token")
    return value


def _user_id(account: dict) -> str:
    value = str(account.get("user_id") or "").strip()
    if not value:
        raise ThreadsApiError("Threads account is missing user_id.", code="missing_user_id")
    return value


def _paged_get(account: dict, path: str, *, params: dict[str, Any], limit: int, optional: bool) -> list[dict]:
    items: list[dict] = []
    next_url: str | None = None
    while len(items) < limit:
        try:
            data = _request(account, "GET", path, params=params, url=next_url, optional=optional)
        except ThreadsApiError as exc:
            if optional and exc.code in OPTIONAL_EMPTY_CODES:
                return []
            raise
        batch = data.get("data") if isinstance(data, dict) else data
        if isinstance(batch, list):
            items.extend([item for item in batch if isinstance(item, dict)])
        elif isinstance(data, dict):
            items.append(data)
        paging = data.get("paging", {}) if isinstance(data, dict) else {}
        cursors = paging.get("cursors", {}) if isinstance(paging, dict) else {}
        after = cursors.get("after") if isinstance(cursors, dict) else None
        next_url = paging.get("next") if isinstance(paging, dict) else None
        if next_url:
            params = {}
        elif after:
            params = {**params, "after": after}
        else:
            break
    return items[:limit]


def _get(account: dict, path: str, *, params: dict[str, Any], optional: bool) -> dict:
    try:
        data = _request(account, "GET", path, params=params, optional=optional)
    except ThreadsApiError as exc:
        if optional and exc.code in OPTIONAL_EMPTY_CODES:
            return {}
        raise
    return data if isinstance(data, dict) else {}


def _delete(account: dict, path: str, *, optional: bool) -> dict:
    return _request(account, "DELETE", path, params={}, optional=optional)


def _request(
    account: dict,
    method: str,
    path: str,
    *,
    params: dict[str, Any],
    optional: bool,
    url: str | None = None,
) -> dict:
    token = _token(account)
    request_params = {**params, "access_token": token}
    target = url or f"{_base_url()}{path}"
    with httpx.Client(timeout=12) as client:
        for attempt in range(3):
            try:
                response = client.request(method, target, params=request_params if method == "GET" else None, data=request_params if method != "GET" else None)
                if response.status_code >= 400:
                    _raise_for_response(response, optional=optional)
                data = response.json()
                return data if isinstance(data, dict) else {"data": data}
            except httpx.HTTPError as exc:
                if attempt >= 2:
                    raise ThreadsApiError(f"Cannot call Threads API: {exc}", code="temporary_error", optional=optional) from exc
                time.sleep(1 + attempt)
    return {}


def _raise_for_response(response: httpx.Response, *, optional: bool) -> None:
    status = response.status_code
    raw = response.text[:500]
    lowered = raw.lower()
    if status >= 500:
        hint = "temporary upstream error"
        if "permission" in lowered or "access" in lowered:
            hint = "Meta returned a server error that mentions permission/access; check app review and token scopes"
        raise ThreadsApiError(f"Threads API {hint} HTTP {status}: {raw}", status_code=status, code="temporary_error", optional=optional)
    if status in {401, 403} or "permission" in lowered:
        exc_cls = ThreadsPermissionError
        code = "permission_denied"
        if "expired" in lowered or "token" in lowered and status == 401:
            exc_cls = ThreadsTokenExpiredError
            code = "token_expired"
        raise exc_cls(f"Threads API permission/token error HTTP {status}.", status_code=status, code=code, optional=optional)
    if status == 429:
        raise ThreadsRateLimitError("Threads API rate limit.", status_code=status, code="rate_limited", optional=optional)
    raise ThreadsApiError(f"Threads API error HTTP {status}: {raw}", status_code=status, code="api_error", optional=optional)


def _flatten_insights(data: dict) -> dict:
    result: dict[str, int] = {}
    rows = data.get("data") if isinstance(data, dict) else []
    if not isinstance(rows, list):
        return result
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("metric") or "").strip()
        values = row.get("values")
        value: Any = row.get("value", 0)
        if isinstance(values, list) and values:
            latest = values[-1]
            if isinstance(latest, dict):
                value = latest.get("value", value)
        try:
            result[name] = int(value or 0)
        except (TypeError, ValueError):
            result[name] = 0
    return result
