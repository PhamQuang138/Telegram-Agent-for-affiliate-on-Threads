from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ThreadsPost(Base):
    __tablename__ = "threads_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    keyword: Mapped[str] = mapped_column(String(255), nullable=False)
    product_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    affiliate_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    tracking_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    slug: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    cta: Mapped[str] = mapped_column(Text, nullable=False, default="")
    hashtags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    need: Mapped[str | None] = mapped_column(Text, nullable=True)
    persona: Mapped[str | None] = mapped_column(String(255), nullable=True)
    angle: Mapped[str | None] = mapped_column(Text, nullable=True)
    persona_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    angle_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    hook: Mapped[str | None] = mapped_column(Text, nullable=True)
    hook_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    story_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content_goal: Mapped[str | None] = mapped_column(String(64), nullable=True)
    diversity_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    target_platform: Mapped[str | None] = mapped_column(String(64), nullable=True)
    click_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    impression_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    performance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    threads_post_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    posted_account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    posted_account_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_cta_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    telegram_cta_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    telegram_cta_reply_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_cta_posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    telegram_cta_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    clicks: Mapped[list["ClickLog"]] = relationship(back_populates="post")
    links: Mapped[list["ThreadsPostLink"]] = relationship(back_populates="post", cascade="all, delete-orphan")


class ThreadsPostLink(Base):
    __tablename__ = "threads_post_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("threads_posts.id"), nullable=False, index=True)
    product_name: Mapped[str] = mapped_column(Text, nullable=False)
    affiliate_url: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    product_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[str | None] = mapped_column(String(64), nullable=True)
    shop_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tracking_url: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    post: Mapped[ThreadsPost] = relationship(back_populates="links")


class ClickLog(Base):
    __tablename__ = "click_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("threads_posts.id"), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="threads")
    referrer: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    clicked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    post: Mapped[ThreadsPost] = relationship(back_populates="clicks")


class TrendSnapshot(Base):
    __tablename__ = "trend_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    keyword: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    trend_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    sources_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    region: Mapped[str] = mapped_column(String(16), nullable=False, default="VN", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class TopicMemory(Base):
    __tablename__ = "topic_memory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    keyword: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    product_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    post_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, default="")


class AffiliateProduct(Base):
    __tablename__ = "affiliate_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    affiliate_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    product_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[str | None] = mapped_column(String(64), nullable=True)
    shop_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    link_type_id: Mapped[str] = mapped_column(String(64), nullable=False, default="shopee_commission", index=True)
    category_id: Mapped[str] = mapped_column(String(64), nullable=False, default="other", index=True)
    subcategory: Mapped[str | None] = mapped_column(String(128), nullable=True)
    subcategory_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DailyLinkEntry(Base):
    __tablename__ = "daily_link_entries"
    __table_args__ = (UniqueConstraint("product_id", "import_date", name="uq_daily_link_product_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("affiliate_products.id"), nullable=False, index=True)
    import_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    batch_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("affiliate_import_batches.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AffiliateImportBatch(Base):
    __tablename__ = "affiliate_import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    import_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    imported_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    type_stats_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    category_stats_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ThreadsPostMetric(Base):
    __tablename__ = "threads_post_metrics"
    __table_args__ = (UniqueConstraint("account_name", "threads_media_id", name="uq_threads_metric_account_media"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    threads_media_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    account_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    threads_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    views: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    likes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    replies: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reposts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quotes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    shares: Mapped[int | None] = mapped_column(Integer, nullable=True)
    click_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    affiliate_ctr: Mapped[float | None] = mapped_column(Float, nullable=True)
    engagement_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    purchase_intent_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    performance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ThreadsReply(Base):
    __tablename__ = "threads_replies"
    __table_args__ = (UniqueConstraint("account_name", "reply_media_id", name="uq_threads_reply_account_reply"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    threads_media_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    reply_media_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    account_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    reply_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reply_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reply_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    intent: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    sentiment: Mapped[str] = mapped_column(String(32), nullable=False, default="neutral")
    asks_for_link: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    asks_for_price: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    product_interest: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_spam: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ThreadsKeywordSnapshot(Base):
    __tablename__ = "threads_keyword_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    keyword: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    account_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recent_result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    related_topics_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    common_intents_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    tone_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class AccountLearningProfile(Base):
    __tablename__ = "account_learning_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    profile_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ThreadsDemandOpportunity(Base):
    __tablename__ = "threads_demand_opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_post_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    author_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    author_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    permalink: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_text_excerpt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    matched_keyword: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    intent: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    purchase_intent_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    category: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    normalized_query: Mapped[str] = mapped_column(Text, nullable=False, default="")
    constraints_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    matched_products_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    suggested_comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="new", index=True)
    scan_account_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    reply_account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    threads_reply_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ThreadsDemandAction(Base):
    __tablename__ = "threads_demand_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    account_name: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    result: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    details: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class DemandOpportunity(Base):
    __tablename__ = "demand_opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(64), nullable=False, default="threads", index=True)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False, default="post")
    external_content_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_text_excerpt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    matched_query: Mapped[str | None] = mapped_column(String(255), nullable=True)
    intent: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    purchase_intent_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    category: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    normalized_query: Mapped[str] = mapped_column(Text, nullable=False, default="")
    constraints_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    matched_products_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    suggested_response: Mapped[str] = mapped_column(Text, nullable=False, default="")
    response_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="manual_copy")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="new", index=True)
    intake_source: Mapped[str] = mapped_column(String(64), nullable=False, default="telegram_manual", index=True)
    scan_account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reply_account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    external_reply_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class DemandAction(Base):
    __tablename__ = "demand_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    account_name: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    result: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    details: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
