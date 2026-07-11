from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse


def parse_platform_url(url: str) -> dict:
    raw = (url or "").strip()
    if not raw:
        return {"platform": "unknown", "normalized_url": "", "external_content_id": None, "username": None, "valid": False, "reason": "empty url"}
    parsed = urlparse(raw if "://" in raw else "https://" + raw)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in {"threads.com", "threads.net"}:
        return {"platform": "unknown", "normalized_url": raw, "external_content_id": None, "username": None, "valid": False, "reason": "unsupported platform"}
    path = re.sub(r"/+", "/", parsed.path).rstrip("/")
    username = None
    external_id = None
    parts = [part for part in path.split("/") if part]
    if parts and parts[0].startswith("@"):
        username = parts[0].lstrip("@")
    if "post" in parts:
        index = parts.index("post")
        if index + 1 < len(parts):
            external_id = parts[index + 1]
    elif len(parts) >= 2 and parts[0].startswith("@"):
        external_id = parts[-1]
    normalized = urlunparse(("https", "www.threads.com", path or "/", "", "", ""))
    return {
        "platform": "threads",
        "normalized_url": normalized,
        "external_content_id": external_id,
        "username": username,
        "valid": True,
        "reason": "ok" if external_id else "valid url but content id not certain",
    }
