from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ClickLog, ThreadsPost, ThreadsPostLink, TrendSnapshot

logger = logging.getLogger(__name__)

SOURCE_WEIGHTS = {
    "google_trends": 0.25,
    "threads": 0.15,
    "shopee_catalog": 0.25,
    "click_history": 0.25,
    "season": 0.10,
    "manual_seed": 0.10,
}

DEFAULT_KEYWORDS = ["đồ tiện ích", "đồ học tập", "decor phòng", "đồ văn phòng", "outfit basic"]
STOPWORDS = {
    "cho",
    "cua",
    "của",
    "gia",
    "giá",
    "hang",
    "hàng",
    "loai",
    "loại",
    "mau",
    "mẫu",
    "nam",
    "nữ",
    "san",
    "sản",
    "pham",
    "phẩm",
    "shopee",
    "the",
    "thể",
    "thoi",
    "thời",
    "trang",
    "voi",
    "với",
    "cao",
    "cấp",
    "cap",
    "hot",
    "chất",
    "chat",
    "liệu",
    "lieu",
    "xịn",
    "xin",
    "vải",
    "vai",
    "mềm",
    "mem",
    "chiều",
    "chieu",
    "dãn",
    "dan",
    "giãn",
    "gian",
    "tròn",
    "tron",
}


@dataclass
class TrendSignal:
    keyword: str
    source: str
    score: float
    reason: str
    matched_products_count: int = 0


class GoogleTrendsProvider:
    source = "google_trends"

    def collect(self, region: str = "VN") -> list[TrendSignal]:
        logger.info("Google Trends provider skipped: no official API configured")
        return []


class ThreadsKeywordSearchProvider:
    source = "threads"

    def collect(self, region: str = "VN") -> list[TrendSignal]:
        settings = get_settings()
        if not settings.threads_access_token or not settings.threads_user_id:
            logger.info("Threads keyword provider skipped: missing token/user permission")
            return []
        logger.info("Threads keyword provider skipped: official keyword search integration not configured")
        return []


class ShopeeCatalogProvider:
    source = "shopee_catalog"

    def __init__(self, db: Session):
        self.db = db

    def collect(self, region: str = "VN") -> list[TrendSignal]:
        links = list(self.db.scalars(select(ThreadsPostLink).order_by(ThreadsPostLink.id.desc()).limit(1500)))
        counter: Counter[str] = Counter()
        product_counts: Counter[str] = Counter()

        for link in links:
            keywords = _extract_keywords(link.product_name)
            for keyword in keywords:
                counter[keyword] += 1
                product_counts[keyword] += 1

        if not counter:
            return []

        max_count = max(counter.values()) or 1
        signals = []
        for keyword, count in counter.most_common(80):
            score = min(100, 35 + (count / max_count) * 65)
            signals.append(
                TrendSignal(
                    keyword=keyword,
                    source=self.source,
                    score=score,
                    reason=f"{count} sản phẩm trong catalog có liên quan",
                    matched_products_count=product_counts[keyword],
                )
            )
        return signals


class ClickHistoryProvider:
    source = "click_history"

    def __init__(self, db: Session):
        self.db = db

    def collect(self, region: str = "VN") -> list[TrendSignal]:
        rows = self.db.execute(
            select(ThreadsPost.keyword, ThreadsPost.persona, ThreadsPost.angle, func.count(ClickLog.id).label("clicks"))
            .join(ClickLog, ClickLog.post_id == ThreadsPost.id)
            .group_by(ThreadsPost.keyword, ThreadsPost.persona, ThreadsPost.angle)
            .order_by(func.count(ClickLog.id).desc())
            .limit(50)
        ).all()
        if not rows:
            return []

        max_clicks = max(int(row.clicks) for row in rows) or 1
        signals = []
        for row in rows:
            keyword = str(row.keyword or "").strip()
            if not keyword:
                continue
            signals.append(
                TrendSignal(
                    keyword=keyword[:120],
                    source=self.source,
                    score=min(100, 40 + (int(row.clicks) / max_clicks) * 60),
                    reason=f"{int(row.clicks)} click từ lịch sử post",
                )
            )
        return signals


class SeasonProvider:
    source = "season"

    def collect(self, region: str = "VN") -> list[TrendSignal]:
        month = datetime.now(timezone.utc).astimezone().month
        if month in (1, 2):
            keywords = ["tết", "decor tết", "quà tết", "lì xì"]
        elif month in (3, 4):
            keywords = ["đồ đi học", "đồ văn phòng", "chống nắng"]
        elif month in (5, 6, 7, 8):
            keywords = ["nắng nóng", "quạt mini", "áo chống nắng", "du lịch"]
        elif month == 9:
            keywords = ["back to school", "balo", "đèn học", "văn phòng phẩm"]
        else:
            keywords = ["noel", "quà tặng", "decor phòng", "áo khoác"]
        return [
            TrendSignal(keyword=keyword, source=self.source, score=78 - index * 4, reason="phù hợp mùa hiện tại")
            for index, keyword in enumerate(keywords)
        ]


class ManualSeedProvider:
    source = "manual_seed"

    def collect(self, region: str = "VN") -> list[TrendSignal]:
        seed_path = Path("data") / "seed_keywords.txt"
        if not seed_path.exists():
            return []
        keywords = [line.strip() for line in seed_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [
            TrendSignal(keyword=keyword, source=self.source, score=55, reason="manual seed keyword")
            for keyword in keywords[:100]
        ]


def get_trending_keywords(
    db: Session,
    limit: int = 20,
    region: str = "VN",
    sources: list[str] | None = None,
    cache_ttl_hours: int = 6,
) -> list[dict]:
    selected_sources = set(sources or SOURCE_WEIGHTS.keys())
    cached = _read_cache(db, region, limit, cache_ttl_hours)
    if cached and not sources:
        return cached[:limit]

    providers = [
        GoogleTrendsProvider(),
        ThreadsKeywordSearchProvider(),
        ShopeeCatalogProvider(db),
        ClickHistoryProvider(db),
        SeasonProvider(),
        ManualSeedProvider(),
    ]

    signals: list[TrendSignal] = []
    for provider in providers:
        if provider.source not in selected_sources:
            continue
        try:
            signals.extend(provider.collect(region=region))
        except Exception as exc:
            logger.warning("Trend provider %s failed: %s", provider.source, exc)

    if not signals:
        signals = [
            TrendSignal(keyword=keyword, source="season", score=50, reason="default fallback keyword")
            for keyword in DEFAULT_KEYWORDS
        ]

    merged = _merge_signals(signals)
    _write_cache(db, merged, region)
    return merged[: max(1, limit)]


def _read_cache(db: Session, region: str, limit: int, ttl_hours: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
    rows = list(
        db.scalars(
            select(TrendSnapshot)
            .where(TrendSnapshot.region == region, TrendSnapshot.created_at >= cutoff)
            .order_by(TrendSnapshot.trend_score.desc(), TrendSnapshot.id.desc())
            .limit(max(20, limit))
        )
    )
    if not rows:
        return []
    return [
        item
        for item in (_snapshot_to_dict(row) for row in rows)
        if not _is_generic_keyword(item["keyword"]) and not _is_weak_keyword(item["keyword"])
    ]


def _write_cache(db: Session, items: list[dict], region: str) -> None:
    for item in items[:50]:
        db.add(
            TrendSnapshot(
                keyword=item["keyword"],
                trend_score=float(item["trend_score"]),
                sources_json=json.dumps(item["sources"], ensure_ascii=False),
                reason=item["reason"],
                region=region,
            )
        )
    db.commit()


def _snapshot_to_dict(snapshot: TrendSnapshot) -> dict:
    try:
        sources = json.loads(snapshot.sources_json)
    except json.JSONDecodeError:
        sources = []
    return {
        "keyword": snapshot.keyword,
        "trend_score": round(float(snapshot.trend_score), 1),
        "sources": sources,
        "reason": snapshot.reason,
        "matched_products_count": 0,
        "suggested_personas": _suggested_personas(snapshot.keyword),
        "suggested_angles": _suggested_angles(snapshot.keyword),
    }


def _merge_signals(signals: Iterable[TrendSignal]) -> list[dict]:
    by_keyword: dict[str, dict] = defaultdict(lambda: {"scores": {}, "reasons": [], "matched": 0})
    for signal in signals:
        keyword = _clean_keyword(signal.keyword)
        if not keyword or _is_generic_keyword(keyword) or _is_weak_keyword(keyword):
            continue
        bucket = by_keyword[keyword]
        bucket["scores"][signal.source] = max(float(signal.score), bucket["scores"].get(signal.source, 0))
        bucket["reasons"].append(signal.reason)
        bucket["matched"] = max(int(bucket["matched"]), int(signal.matched_products_count))

    items = []
    for keyword, bucket in by_keyword.items():
        active_weights = {
            source: SOURCE_WEIGHTS.get(source, 0.10)
            for source in bucket["scores"]
            if bucket["scores"][source] > 0
        }
        total_weight = sum(active_weights.values()) or 1
        score = sum(bucket["scores"][source] * weight for source, weight in active_weights.items()) / total_weight
        items.append(
            {
                "keyword": keyword,
                "trend_score": round(min(100, score), 1),
                "sources": sorted(bucket["scores"].keys()),
                "reason": "; ".join(dict.fromkeys(bucket["reasons"]))[:240],
                "matched_products_count": int(bucket["matched"]),
                "suggested_personas": _suggested_personas(keyword),
                "suggested_angles": _suggested_angles(keyword),
            }
        )
    return sorted(items, key=lambda item: item["trend_score"], reverse=True)


def _extract_keywords(product_name: str) -> list[str]:
    text = _clean_keyword(product_name)
    tokens = [token for token in text.split() if len(token) >= 3 and token not in STOPWORDS]
    phrases = []
    for size in (2, 3):
        for index in range(0, max(0, len(tokens) - size + 1)):
            phrase = " ".join(tokens[index : index + size])
            if not any(part in STOPWORDS for part in phrase.split()):
                phrases.append(phrase)
    return phrases[:5]


def _clean_keyword(value: str) -> str:
    value = re.sub(r"https?://\S+", " ", value or "")
    value = re.sub(r"[^\w\sàáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ-]", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()[:120]


def _is_generic_keyword(keyword: str) -> bool:
    lower = keyword.lower()
    generic_bits = ["list", "shopee", "dang xem", "đang xem", "san pham shopee", "sản phẩm shopee"]
    return sum(1 for bit in generic_bits if bit in lower) >= 2


def _is_weak_keyword(keyword: str) -> bool:
    tokens = keyword.lower().split()
    if not tokens:
        return True
    weak = {
        "cao",
        "cấp",
        "cap",
        "hot",
        "chất",
        "chat",
        "liệu",
        "lieu",
        "xịn",
        "xin",
        "đẹp",
        "dep",
        "vải",
        "vai",
        "mềm",
        "mem",
        "chiều",
        "chieu",
        "dãn",
        "dan",
        "giãn",
        "gian",
        "tròn",
        "tron",
    }
    return tokens[0] in weak or any(token in weak for token in tokens) or all(token in weak for token in tokens)


def _suggested_personas(keyword: str) -> list[str]:
    lower = keyword.lower()
    if any(word in lower for word in ["balo", "đèn học", "đồ học", "văn phòng phẩm"]):
        return ["sinh viên", "người mới đi làm"]
    if any(word in lower for word in ["áo", "outfit", "giày"]):
        return ["người thích mặc gọn", "dân đi chơi cuối tuần"]
    if any(word in lower for word in ["bàn", "laptop", "văn phòng", "decor"]):
        return ["dân văn phòng", "người làm việc ở nhà"]
    return ["người thích đồ tiện ích"]


def _suggested_angles(keyword: str) -> list[str]:
    lower = keyword.lower()
    if any(word in lower for word in ["nắng", "quạt", "áo khoác"]):
        return ["thời tiết làm mình khó chịu", "món nhỏ cứu mood trong ngày"]
    if any(word in lower for word in ["decor", "bàn", "văn phòng"]):
        return ["góc sống bừa nhưng muốn gọn", "setup nhỏ mà đỡ bực"]
    return ["vấn đề nhỏ hằng ngày", "mua ít nhưng dùng được nhiều dịp"]
