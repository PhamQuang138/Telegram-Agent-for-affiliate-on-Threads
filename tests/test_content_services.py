from types import SimpleNamespace
from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base, AffiliateProduct, DailyLinkEntry, DemandOpportunity, ThreadsDemandAction, ThreadsDemandOpportunity, ThreadsPost, ThreadsPostLink, ThreadsPostMetric, ThreadsReply
from app.models import AdminAffiliateLink
from app.services.angle_library import select_angle
import app.services.angle_library as angle_library
from app.services.content_diversity import should_reduce_repetition
from app.services.content_engine import generate_affiliate_content, generate_affiliate_content_from_idea, generate_content_ideas
from app.services.content_quality import evaluate_content
from app.services.content_similarity import is_too_similar
from app.services.hook_library import choose_hook, load_hooks
import app.services.hook_library as hook_library
from app.services.persona_library import select_persona
import app.services.persona_library as persona_library
from app.services.product_scoring import score_products
from app.services.daily_link_catalog import build_category_message, category_counts, import_daily_csv, parse_import_date, resolve_import_date
from app.services.daily_link_repository import get_categories_for_date_and_type, get_link_types_for_date, get_products_for_date_type_category
from app.services.affiliate_link_type_classifier import classify_affiliate_link_type
from app.services import daily_link_cleanup
from app.services.admin_curated_links import (
    cleanup_expired_admin_links,
    close_batch as close_admin_link_batch,
    get_links_for_delivery as get_admin_links_for_delivery,
    ingest_admin_message,
    parse_link_lines,
    start_batch as start_admin_link_batch,
)
from app.services.threads_account_service import get_threads_account, load_threads_accounts, select_account_for_post
from app.services.telegram_cta_generator import generate_telegram_cta
from app.services.reply_analysis import analyze_reply, calculate_purchase_intent_score
from app.services import demand_product_matcher, threads_analytics_scheduler, threads_demand_scanner, threads_insights_service, threads_reply_service, threads_sync_service, trend_service
from app.services.demand_comment_generator import generate_demand_comment
from app.services import manual_demand_intake
from app.services.feature_flags import is_feature_enabled
from app.services.platform_url_parser import parse_platform_url
from app.services.purchase_intent import classify_purchase_intent
from app.services.trend_service import GoogleSuggestProvider
from app.services import learning_engine
from agents.threads_shopee_agent import _openrouter_reset_at, _soft_parse_json


def test_similarity_detects_repeated_opening() -> None:
    previous = ["Ban lam viec bua qua nen minh mua them ke nho de do."]
    assert is_too_similar("Ban lam viec bua qua nen lai muon don lai goc nho.", previous)


def test_quality_checker_rejects_raw_link_and_spam() -> None:
    result = evaluate_content(
        "Mua ngay sale soc 100% https://shopee.vn/test",
        [],
        [],
    )
    assert not result["passed"]
    assert "raw_shopee_link" in result["issues"]
    assert "spam_language" in result["issues"]


def test_product_scoring_prefers_keyword_match() -> None:
    products = [
        {"product_name": "Ao the thao da bong co gian", "price": "120000"},
        {"product_name": "Hop dung but hoc sinh", "price": "50000"},
    ]
    scored = score_products(products, "ao the thao")
    assert scored[0]["product_name"].startswith("Ao the thao")
    assert scored[0]["score"] > scored[1]["score"]


def test_content_engine_returns_required_shape_without_ai() -> None:
    result = generate_affiliate_content(
        "quat mini",
        [{"product_name": "Quat mini de ban van phong", "price": "99000"}],
        [],
        {},
    )
    assert result["content"]
    assert result["need"]
    assert result["persona"]
    assert result["angle"]
    assert result["hook_type"]
    assert result["persona_id"]
    assert result["angle_id"]
    assert result["diversity_key"]
    assert isinstance(result["selected_products"], list)
    assert 0 <= result["quality_score"] <= 100


def test_content_engine_does_not_dump_mixed_catalog_names() -> None:
    result = generate_affiliate_content(
        "dân văn phòng",
        [
            {"product_name": "ICON Dù Gấp Tự Động 8 Xương UV", "price": "99000"},
            {"product_name": "[RẺ VÔ ĐỐI] Áo thun nam màu xám cotton", "price": "79000"},
        ],
        [],
        {},
    )
    content = result["content"].lower()
    assert "rẻ vô đối" not in content
    assert "icon dù" not in content
    assert "8 xương" not in content


def test_libraries_select_reasonable_items() -> None:
    hooks = load_hooks()
    hook = choose_hook("question")
    persona = select_persona("bàn làm việc", {"product_name": "kệ để bàn laptop"})
    angle = select_angle("bàn làm việc", {"product_name": "kệ để bàn laptop"}, persona)
    assert hooks
    assert hook["hook_type"] == "question"
    assert persona["id"]
    assert angle["id"]


def test_libraries_use_learned_weights(monkeypatch) -> None:
    monkeypatch.setattr(persona_library, "load_learned_weights", lambda: {"personas": {"style_basic": 1.8}})
    monkeypatch.setattr(angle_library, "load_learned_weights", lambda: {"angles": {"wishlist_reason": 1.8}})
    monkeypatch.setattr(hook_library, "load_learned_weights", lambda: {"hook_types": {"question": 1.8}})
    persona = select_persona("áo khoác", {"product_name": "áo khoác basic"})
    angle = select_angle("áo khoác", {"product_name": "áo khoác basic"}, persona)
    hook = choose_hook("question")
    assert persona["id"] == "style_basic"
    assert angle["id"] in {"wishlist_reason", "seasonal_context"}
    assert hook["hook_type"] == "question"


def test_content_diversity_rejects_repeated_key() -> None:
    candidate = {"diversity_key": "a|b|c|d"}
    recent = [{"diversity_key": "a|b|c|d"} for _ in range(5)] + [{"diversity_key": "x"} for _ in range(5)]
    result = should_reduce_repetition(candidate, recent, max_same_key_ratio=0.35)
    assert not result["passed"]


def test_generate_content_ideas() -> None:
    ideas = generate_content_ideas("quạt mini", [{"product_name": "Quạt mini để bàn"}], {}, count=2)
    assert len(ideas) == 2
    assert ideas[0]["idea"]


def test_generate_content_from_idea_uses_seed() -> None:
    idea = {
        "keyword": "quạt mini",
        "need": "ngồi bàn làm việc nóng bí",
        "persona": "Dân văn phòng tối giản",
        "angle": "đồ nhỏ để bàn",
        "hook": "Có ai ngồi làm việc mà nóng tới mức mất mood không...",
        "idea": "Có ai ngồi làm việc mà nóng tới mức mất mood không? Một món nhỏ để bàn đôi khi không cứu cả mùa hè, nhưng cứu được vài giờ tập trung.",
    }
    result = generate_affiliate_content_from_idea(
        "quạt mini",
        [{"product_name": "Quạt mini để bàn"}],
        idea,
        [],
        {},
    )
    assert "nóng" in result["content"].lower()
    assert result["need"] == idea["need"]


def test_soft_parser_extracts_content_from_partial_engine_json() -> None:
    raw = '{ "content": "Bàn làm việc bừa đôi khi chỉ cần một món nhỏ để bớt cáu.", "cta": "", "hashtags": [], "quality_score": 82, "need": "gọn bàn"'
    parsed = _soft_parse_json(raw)
    assert parsed["content"].startswith("Bàn làm việc bừa")
    assert parsed["quality_score"] == 82


def test_sqlite_post_migration_is_idempotent(monkeypatch) -> None:
    import app.db as db_module

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE threads_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword VARCHAR(255) NOT NULL,
                product_name VARCHAR(255) NOT NULL DEFAULT '',
                affiliate_url TEXT,
                tracking_url TEXT,
                slug VARCHAR(64),
                content TEXT NOT NULL,
                cta TEXT NOT NULL DEFAULT '',
                hashtags TEXT NOT NULL DEFAULT '[]',
                status VARCHAR(32) NOT NULL,
                quality_score FLOAT NOT NULL DEFAULT 0,
                scheduled_at DATETIME,
                threads_post_id VARCHAR(255),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    monkeypatch.setattr(db_module, "engine", engine)
    db_module._migrate_threads_posts_columns()
    db_module._migrate_threads_posts_columns()
    db_module._migrate_topic_memory_table()
    db_module._migrate_topic_memory_table()
    db_module._migrate_app_settings_table()
    db_module._migrate_app_settings_table()

    with engine.begin() as connection:
        columns = {
            row["name"]
            for row in connection.exec_driver_sql("PRAGMA table_info('threads_posts')").mappings().all()
        }

    assert "need" in columns
    assert "performance_score" in columns
    assert "posted_account_name" in columns
    with engine.begin() as connection:
        tables = {
            row["name"]
            for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'").mappings().all()
        }
    assert "topic_memory" in tables
    assert "app_settings" in tables


def test_google_suggest_provider_uses_mocked_request(monkeypatch) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return ["quạt mini", ["quạt mini để bàn", "quạt mini sạc pin"]]

    def fake_get(*args, **kwargs):
        return Response()

    monkeypatch.setattr("app.services.trend_service.httpx.get", fake_get)
    provider = GoogleSuggestProvider(db=None)
    monkeypatch.setattr(provider, "_seeds", lambda: ["quạt mini"])
    signals = provider.collect()
    assert signals
    assert signals[0].source == "google_suggest"


def test_threads_accounts_named_config(monkeypatch) -> None:
    monkeypatch.setenv("THREADS_ACCOUNTS", "acc1,acc2")
    monkeypatch.setenv("THREADS_ACC1_USER_ID", "111")
    monkeypatch.setenv("THREADS_ACC1_ACCESS_TOKEN", "tok1")
    monkeypatch.setenv("THREADS_ACC1_PERSONA", "office")
    monkeypatch.setenv("THREADS_ACC1_TOPICS", "bàn làm việc,laptop")
    monkeypatch.setenv("THREADS_ACC2_USER_ID", "222")
    monkeypatch.setenv("THREADS_ACC2_ACCESS_TOKEN", "tok2")
    monkeypatch.setenv("THREADS_ACC2_TOPICS", "áo khoác,outfit")

    accounts = load_threads_accounts()
    assert len(accounts) == 2
    assert get_threads_account("acc2")["user_id"] == "222"
    selected = select_account_for_post({"keyword": "setup bàn làm việc laptop"}, accounts)
    assert selected["name"] == "acc1"


def test_openrouter_reset_at_parses_milliseconds() -> None:
    raw = '{"error":{"metadata":{"headers":{"X-RateLimit-Reset":"1783641600000"}}}}'
    assert _openrouter_reset_at(raw) == 1783641600


def test_learning_profile_and_weights_update(monkeypatch, tmp_path) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr(learning_engine, "SessionLocal", Session)
    monkeypatch.setattr(learning_engine, "WEIGHTS_PATH", tmp_path / "learned_weights.json")

    with Session() as db:
        for index in range(12):
            db.add(
                ThreadsPost(
                    keyword="quạt mini" if index < 8 else "áo khoác",
                    product_name="p",
                    content="content",
                    cta="",
                    hashtags="[]",
                    status="posted",
                    quality_score=80,
                    persona_id="office_minimal" if index < 8 else "style_basic",
                    persona="office_minimal" if index < 8 else "style_basic",
                    angle_id="problem_solution" if index < 8 else "wishlist_reason",
                    angle="problem_solution" if index < 8 else "wishlist_reason",
                    hook_type="observation" if index < 8 else "question",
                    click_count=2 if index < 8 else 0,
                    posted_account_name="acc1" if index < 8 else "acc2",
                )
            )
        db.commit()

    profile = learning_engine.build_learning_profile(min_posts=10)
    assert profile["enough_data"]
    assert profile["top_personas"][0]["name"] == "office_minimal"
    assert profile["weak_personas"][0]["name"] == "style_basic"

    updated = learning_engine.update_learned_weights(min_posts=10)
    weights = updated["learned_weights"]
    assert weights["personas"]["office_minimal"] > 1.0
    assert weights["personas"]["style_basic"] < 1.0
    assert 0.5 <= weights["personas"]["style_basic"] <= 1.8


def test_learning_profile_not_enough_data(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr(learning_engine, "SessionLocal", Session)
    assert not learning_engine.build_learning_profile(min_posts=10)["enough_data"]


def test_autolearn_gate_waits_six_hours(monkeypatch, tmp_path) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr(learning_engine, "SessionLocal", Session)
    monkeypatch.setattr(learning_engine, "WEIGHTS_PATH", tmp_path / "learned_weights.json")
    learning_engine.set_app_setting(learning_engine.SETTING_AUTO_LEARNING, "on")
    learning_engine.set_app_setting(learning_engine.SETTING_LAST_LEARNING_RUN, learning_engine.datetime.now(learning_engine.timezone.utc).isoformat())
    with Session() as db:
        for index in range(10):
            db.add(
                ThreadsPost(
                    keyword="quạt mini",
                    product_name="p",
                    content="content",
                    cta="",
                    hashtags="[]",
                    status="posted",
                    quality_score=80,
                    persona_id="office_minimal",
                    angle_id="problem_solution",
                    hook_type="observation",
                    click_count=1,
                )
            )
        db.commit()
    assert learning_engine.maybe_run_auto_learning() is None


def test_reply_analysis_detects_intents() -> None:
    ask_link = analyze_reply("Cho mình link với")
    ask_price = analyze_reply("Giá bao nhiêu vậy?")
    spam = analyze_reply("https://a.test https://b.test")
    assert ask_link["intent"] == "ask_link"
    assert ask_link["asks_for_link"]
    assert ask_price["intent"] == "ask_price"
    assert ask_price["asks_for_price"]
    assert spam["is_spam"]
    assert calculate_purchase_intent_score([ask_link, ask_price]) > 50


def test_sync_posts_does_not_duplicate(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr(threads_sync_service, "SessionLocal", Session)
    monkeypatch.setenv("THREADS_ACCOUNTS", "acc1")
    monkeypatch.setenv("THREADS_ACC1_USER_ID", "111")
    monkeypatch.setenv("THREADS_ACC1_ACCESS_TOKEN", "tok1")
    monkeypatch.setattr(
        threads_sync_service,
        "get_user_threads",
        lambda account, limit=50: [{"id": "m1", "text": "hello"}],
    )
    with Session() as db:
        db.add(
            ThreadsPost(
                keyword="k",
                product_name="p",
                content="content",
                cta="",
                hashtags="[]",
                status="posted",
                quality_score=80,
                threads_post_id="m1",
            )
        )
        db.commit()
    result = threads_sync_service.sync_account_posts("acc1")
    with Session() as db:
        count = len(list(db.query(ThreadsPost).all()))
    assert result["matched"] == 1
    assert count == 1


def test_sync_insights_calculates_metrics(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr(threads_insights_service, "SessionLocal", Session)
    monkeypatch.setenv("THREADS_ACCOUNTS", "acc1")
    monkeypatch.setenv("THREADS_ACC1_USER_ID", "111")
    monkeypatch.setenv("THREADS_ACC1_ACCESS_TOKEN", "tok1")
    monkeypatch.setattr(
        threads_insights_service,
        "get_post_insights",
        lambda account, media_id: {"views": 100, "likes": 10, "replies": 5, "reposts": 2, "quotes": 1},
    )
    with Session() as db:
        db.add(
            ThreadsPost(
                keyword="k",
                product_name="p",
                content="content",
                cta="",
                hashtags="[]",
                status="posted",
                quality_score=80,
                threads_post_id="m1",
                posted_account_name="acc1",
                click_count=4,
            )
        )
        db.commit()
    result = threads_insights_service.sync_post_insights(1)
    assert result["synced"]
    assert result["metrics"]["affiliate_ctr"] == 0.04
    assert result["metrics"]["engagement_rate"] == 0.28


def test_sync_replies_updates_purchase_intent(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr(threads_reply_service, "SessionLocal", Session)
    monkeypatch.setattr(threads_insights_service, "SessionLocal", Session)
    monkeypatch.setenv("THREADS_ACCOUNTS", "acc1")
    monkeypatch.setenv("THREADS_ACC1_USER_ID", "111")
    monkeypatch.setenv("THREADS_ACC1_ACCESS_TOKEN", "tok1")
    monkeypatch.setattr(
        threads_reply_service,
        "get_post_replies",
        lambda account, media_id, limit=100: [{"id": "r1", "text": "xin link với"}, {"id": "r2", "text": "giá bao nhiêu"}],
    )
    with Session() as db:
        db.add(
            ThreadsPost(
                keyword="k",
                product_name="p",
                content="content",
                cta="",
                hashtags="[]",
                status="posted",
                quality_score=80,
                threads_post_id="m1",
                posted_account_name="acc1",
            )
        )
        db.commit()
    result = threads_reply_service.sync_post_replies(1)
    with Session() as db:
        replies = list(db.query(ThreadsReply).all())
        metric = db.query(ThreadsPostMetric).one()
    assert result["synced"] == 2
    assert replies[0].intent == "ask_link"
    assert metric.purchase_intent_score > 0


def test_keyword_snapshot_does_not_store_raw_post_text(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr(
        trend_service,
        "search_threads_keywords",
        lambda account, keyword, limit=50: [{"id": "x", "text": "xin link áo đá bóng này với", "timestamp": "2026-07-10T00:00:00+00:00"}],
    )
    with Session() as db:
        snapshot = trend_service.collect_threads_keyword_snapshot({"name": "acc1", "access_token": "tok", "user_id": "111"}, "áo đá bóng", db=db)
        stored = db.execute(text("SELECT related_topics_json, common_intents_json FROM threads_keyword_snapshots")).first()
    assert snapshot["result_count"] == 1
    assert "xin link áo đá bóng này với" not in str(stored)


def test_account_learning_profile_is_account_specific(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr(learning_engine, "SessionLocal", Session)
    with Session() as db:
        for index in range(12):
            db.add(
                ThreadsPost(
                    keyword="desk setup" if index < 10 else "áo bóng đá",
                    product_name="p",
                    content="content",
                    cta="",
                    hashtags="[]",
                    status="posted",
                    quality_score=80,
                    persona_id="office" if index < 10 else "sport",
                    angle_id="setup" if index < 10 else "outfit",
                    hook_type="observation",
                    click_count=1,
                    posted_account_name="acc1" if index < 10 else "acc2",
                )
            )
        db.commit()
    acc1 = learning_engine.build_account_learning_profile("acc1", min_posts=10)
    acc2 = learning_engine.build_account_learning_profile("acc2", min_posts=10)
    assert acc1["enough_data"]
    assert not acc2["enough_data"]
    assert "office" in acc1["weights"]["personas"]


def test_scheduler_does_not_overlap() -> None:
    assert threads_analytics_scheduler._LOCK.acquire(blocking=False)
    try:
        result = threads_analytics_scheduler.run_sync_once()
    finally:
        threads_analytics_scheduler._LOCK.release()
    assert not result["started"]


def test_feature_flags_default_freeze() -> None:
    assert is_feature_enabled("daily_link_catalog")
    assert is_feature_enabled("threads_engagement_posts")
    assert not is_feature_enabled("manual_demand_intake")
    assert not is_feature_enabled("purchase_intent")
    assert not is_feature_enabled("learning_engine")
    assert not is_feature_enabled("threads_background_sync")


def test_daily_cleanup_keeps_four_days_and_preview(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr(daily_link_cleanup, "SessionLocal", Session)

    with Session() as db:
        for index, import_date in enumerate(["2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10", "2026-07-11"], start=1):
            product = AffiliateProduct(product_name=f"Product {index}", affiliate_url=f"https://s.shopee.vn/{index}", category_id="home")
            db.add(product)
            db.flush()
            db.add(DailyLinkEntry(product_id=product.id, import_date=import_date))
        db.commit()

    preview = daily_link_cleanup.cleanup_expired_daily_links(retention_days=4, reference_date=daily_link_cleanup.date(2026, 7, 11), preview=True)
    assert preview["cutoff_date"] == "2026-07-08"
    assert preview["entries_deleted"] == 1
    with Session() as db:
        assert db.query(DailyLinkEntry).count() == 5

    result = daily_link_cleanup.cleanup_expired_daily_links(retention_days=4, reference_date=daily_link_cleanup.date(2026, 7, 11))
    assert result["entries_deleted"] == 1
    with Session() as db:
        dates = [row.import_date for row in db.query(DailyLinkEntry).order_by(DailyLinkEntry.import_date).all()]
    assert dates == ["2026-07-08", "2026-07-09", "2026-07-10", "2026-07-11"]


def test_daily_category_message_and_counts_ignore_inactive() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    with Session() as db:
        active = AffiliateProduct(product_name="Quat mini de ban", affiliate_url="https://s.shopee.vn/a", price="79000", category_id="home", is_active=1)
        inactive = AffiliateProduct(product_name="Ke bep", affiliate_url="https://s.shopee.vn/b", category_id="home", is_active=0)
        db.add_all([active, inactive])
        db.flush()
        db.add_all([DailyLinkEntry(product_id=active.id, import_date="2024-02-29"), DailyLinkEntry(product_id=inactive.id, import_date="2024-02-29")])
        db.commit()
        counts = category_counts(db, "2024-02-29")
        messages = build_category_message("2024-02-29", "home", [active])
    assert counts[0]["count"] == 1
    assert "Quat mini de ban" in messages[0]
    assert "https://s.shopee.vn/a" in messages[0]


def test_daily_import_classifies_link_type_and_category(tmp_path, monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr("app.services.daily_link_catalog.get_settings", lambda: SimpleNamespace(
        daily_link_timezone="Asia/Bangkok",
        enable_daily_link_auto_cleanup=False,
        daily_default_link_type="shopee_commission",
        daily_max_products_per_category=20,
        telegram_daily_link_disclosure="Cac link tren la link tiep thi lien ket.",
        daily_links_per_message=5,
    ))
    csv_path = tmp_path / "xtra20260711160405.csv"
    csv_path.write_text(
        "Tên sản phẩm,Link ưu đãi,Loại hoa hồng,Danh mục sản phẩm,Giá,Tên cửa hàng\n"
        "Quạt mini để bàn,https://s.shopee.vn/a,Hoa hồng Xtra,Gia dụng,79000,Shop A\n"
        "Áo đá bóng nam,https://s.shopee.vn/b,Ưu đãi độc quyền,Thể thao,120000,Shop B\n",
        encoding="utf-8-sig",
    )
    with Session() as db:
        result = import_daily_csv(db, csv_path)
        types = get_link_types_for_date(db, "2026-07-11")
        cats = get_categories_for_date_and_type(db, "2026-07-11", "xtra_commission")
        products = get_products_for_date_type_category(db, "2026-07-11", "xtra_commission", "home")
    assert result.new_entries == 2
    assert {item["link_type_id"] for item in types} == {"xtra_commission", "exclusive_offer"}
    assert cats[0]["category_id"] == "home"
    assert products["products"][0].product_name == "Quạt mini để bàn"


def test_daily_import_admin_override_and_fallback_type(tmp_path, monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr("app.services.daily_link_catalog.get_settings", lambda: SimpleNamespace(
        daily_link_timezone="Asia/Bangkok",
        enable_daily_link_auto_cleanup=False,
        daily_default_link_type="shopee_commission",
        daily_max_products_per_category=20,
        telegram_daily_link_disclosure="Cac link tren la link tiep thi lien ket.",
        daily_links_per_message=5,
    ))
    csv_path = tmp_path / "daily.csv"
    csv_path.write_text(
        "Product Name,Affiliate Link\n"
        "Chuột không dây Acer,https://s.shopee.vn/c\n",
        encoding="utf-8-sig",
    )
    with Session() as db:
        import_daily_csv(db, csv_path, "2026-07-11", default_link_type_id="product_commission")
        product = db.query(AffiliateProduct).one()
    assert product.link_type_id == "product_commission"
    assert product.category_id == "electronics"


def test_link_type_alias_without_accents() -> None:
    classified = classify_affiliate_link_type({"Commission Type": "hoa hong san pham"})
    assert classified["link_type_id"] == "product_commission"


def test_daily_import_thousand_rows_does_not_call_ai(tmp_path, monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr("app.services.daily_link_catalog.get_settings", lambda: SimpleNamespace(
        daily_link_timezone="Asia/Bangkok",
        enable_daily_link_auto_cleanup=False,
        daily_default_link_type="shopee_commission",
        daily_max_products_per_category=20,
        telegram_daily_link_disclosure="Cac link tren la link tiep thi lien ket.",
        daily_links_per_message=5,
    ))
    calls = {"ai": 0}
    monkeypatch.setattr("agents.threads_shopee_agent.generate_threads_shopee_content", lambda *args, **kwargs: calls.__setitem__("ai", calls["ai"] + 1))
    csv_path = tmp_path / "bulk20260711.csv"
    rows = ["Tên sản phẩm,Link ưu đãi,Loại chiến dịch"]
    rows.extend(f"Quạt mini để bàn {index},https://s.shopee.vn/bulk{index},Hoa hồng Shopee" for index in range(1000))
    csv_path.write_text("\n".join(rows), encoding="utf-8-sig")
    with Session() as db:
        result = import_daily_csv(db, csv_path)
    assert result.new_entries == 1000
    assert calls["ai"] == 0


def test_admin_curated_intake_requires_active_admin_batch(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr("app.services.admin_curated_links.get_settings", lambda: SimpleNamespace(
        telegram_admin_user_ids="1",
        telegram_community_group_id="-100",
        link_intake_batch_timeout_minutes=30,
        link_retention_days=4,
        max_links_per_category=15,
        private_link_request_cooldown_seconds=10,
        private_link_max_requests_per_user_per_hour=10,
        telegram_daily_link_disclosure="Cac link tren la link tiep thi lien ket.",
    ))
    with Session() as db:
        ignored = ingest_admin_message(db, 1, -100, "Quat mini | https://s.shopee.vn/a")
        batch = start_admin_link_batch(db, 1, -100, "exclusive_offer", "home")
        saved = ingest_admin_message(db, 1, -100, "Quat mini | https://s.shopee.vn/a\nhttps://s.shopee.vn/b")
        duplicate = ingest_admin_message(db, 1, -100, "Trung | https://s.shopee.vn/a")
        close_admin_link_batch(db, 1, -100)
        links = get_admin_links_for_delivery(db, "exclusive_offer", "home")
    assert ignored.added == 0
    assert batch.link_type_id == "exclusive_offer"
    assert saved.added == 2
    assert duplicate.duplicates == 1
    assert [link.affiliate_url for link in links] == ["https://s.shopee.vn/b", "https://s.shopee.vn/a"]


def test_admin_curated_parse_lines_does_not_fetch_or_infer() -> None:
    rows = parse_link_lines("Quạt mini | https://s.shopee.vn/a?x=1\nhttps://s.shopee.vn/b\nkhong co link")
    assert rows[0]["display_name"] == "Quạt mini"
    assert rows[0]["affiliate_url"] == "https://s.shopee.vn/a?x=1"
    assert rows[1]["display_name"] == "Link ưu đãi 2"
    assert rows[2] is None


def test_admin_curated_delivery_max_15_and_cleanup(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr("app.services.admin_curated_links.get_settings", lambda: SimpleNamespace(
        telegram_admin_user_ids="1",
        telegram_community_group_id="-100",
        link_intake_batch_timeout_minutes=30,
        link_retention_days=4,
        max_links_per_category=15,
        private_link_request_cooldown_seconds=10,
        private_link_max_requests_per_user_per_hour=10,
        telegram_daily_link_disclosure="Cac link tren la link tiep thi lien ket.",
    ))
    with Session() as db:
        start_admin_link_batch(db, 1, -100, "xtra_commission", "electronics")
        text = "\n".join(f"Mon {index} | https://s.shopee.vn/{index}" for index in range(20))
        ingest_admin_message(db, 1, -100, text)
        links = get_admin_links_for_delivery(db, "xtra_commission", "electronics")
        old = links[-1]
        old.created_at = datetime(2026, 7, 1)
        db.commit()
        cleanup = cleanup_expired_admin_links(db)
        active_after_cleanup = db.query(AdminAffiliateLink).filter(AdminAffiliateLink.is_active == 1).count()
    assert len(links) == 15
    assert cleanup["links_deactivated"] == 1
    assert active_after_cleanup == 19


def test_telegram_cta_contains_group_url_and_avoids_recent() -> None:
    group_url = "https://t.me/example_group"
    recent = [generate_telegram_cta({}, group_url, [])]
    cta = generate_telegram_cta({"keyword": "ao the thao"}, group_url, recent)
    assert group_url in cta
    assert "s.shopee.vn" not in cta
    assert cta not in recent


def test_parse_import_date_accepts_local_formats() -> None:
    assert parse_import_date("2026-01-01") == "2026-01-01"
    assert parse_import_date("29/02/2024") == "2024-02-29"
    assert parse_import_date("20260711160405") == "2026-07-11"
    assert resolve_import_date("upload.tmp", "file20260711160341.csv") == "2026-07-11"
    assert resolve_import_date("file20260711160341.csv", "not-a-date") == "2026-07-11"


def test_platform_url_parser_threads_url() -> None:
    parsed = parse_platform_url("https://www.threads.com/@abc/post/XYZ?x=1")
    assert parsed["valid"]
    assert parsed["platform"] == "threads"
    assert parsed["username"] == "abc"
    assert parsed["external_content_id"] == "XYZ"
    assert "?" not in parsed["normalized_url"]


def test_manual_demand_url_only_does_not_scrape() -> None:
    result = manual_demand_intake.create_manual_demand("", url="https://www.threads.com/@abc/post/XYZ")
    assert not result["created"]
    assert "cannot scrape" in result["reason"]


def test_manual_demand_text_only_creates_manual_copy(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr(manual_demand_intake, "SessionLocal", Session)
    monkeypatch.setattr(demand_product_matcher, "SessionLocal", Session)
    with Session() as db:
        post = ThreadsPost(keyword="catalog", product_name="p", content="c", cta="", hashtags="[]", status="posted", quality_score=0)
        db.add(post)
        db.flush()
        db.add(ThreadsPostLink(post_id=post.id, product_name="Quạt mini để bàn dưới 200k", affiliate_url="https://s.shopee.vn/quat", price="150000", tracking_url="t1", slug="s1"))
        db.commit()
    result = manual_demand_intake.create_manual_demand("Cho mình xin link quạt mini để bàn dưới 200k")
    assert result["created"]
    assert result["response_mode"] == "manual_copy"
    assert not result["can_api_reply"]
    with Session() as db:
        assert db.query(DemandOpportunity).count() == 1


def test_manual_demand_duplicate_by_url(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr(manual_demand_intake, "SessionLocal", Session)
    monkeypatch.setattr(demand_product_matcher, "SessionLocal", Session)
    with Session() as db:
        post = ThreadsPost(keyword="catalog", product_name="p", content="c", cta="", hashtags="[]", status="posted", quality_score=0)
        db.add(post)
        db.flush()
        db.add(ThreadsPostLink(post_id=post.id, product_name="Quạt mini để bàn", affiliate_url="https://s.shopee.vn/quat", price="150000", tracking_url="t1", slug="s1"))
        db.commit()
    url = "https://www.threads.com/@abc/post/XYZ?utm=1"
    first = manual_demand_intake.create_manual_demand("xin link quạt mini để bàn", url=url)
    second = manual_demand_intake.create_manual_demand("xin link quạt mini để bàn", url=url)
    assert first["created"]
    assert not second["created"]
    assert second["reason"] == "duplicate"


def test_purchase_intent_rules_and_price_extract() -> None:
    ask_link = classify_purchase_intent("Cho mình xin link quạt mini để bàn dưới 200k với", "quạt mini")
    reco = classify_purchase_intent("Mọi người recommend áo đá bóng loại nào ổn?", "áo đá bóng")
    general = classify_purchase_intent("Hôm nay bàn làm việc bừa quá", None)
    assert ask_link["intent"] == "ask_link"
    assert ask_link["constraints"]["price_max"] == 200000
    assert ask_link["eligible"]
    assert reco["intent"] == "ask_recommendation"
    assert not general["eligible"]


def test_build_scan_keywords_manual_stays_narrow(monkeypatch) -> None:
    monkeypatch.setattr(threads_demand_scanner, "SessionLocal", lambda: (_ for _ in ()).throw(AssertionError("manual keyword should not read db")))
    keywords = threads_demand_scanner.build_scan_keywords("quần áo", limit=30)
    assert len(keywords) == 5
    assert all("quần áo" in keyword for keyword in keywords)


def test_product_matcher_respects_budget_and_relevance(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr(demand_product_matcher, "SessionLocal", Session)
    with Session() as db:
        post = ThreadsPost(keyword="catalog", product_name="p", content="c", cta="", hashtags="[]", status="posted", quality_score=0)
        db.add(post)
        db.flush()
        db.add_all(
            [
                ThreadsPostLink(post_id=post.id, product_name="Quạt mini để bàn sạc pin", affiliate_url="https://s.shopee.vn/quat", price="150000", tracking_url="t1", slug="s1"),
                ThreadsPostLink(post_id=post.id, product_name="Áo khoác nam mùa đông", affiliate_url="https://s.shopee.vn/ao", price="300000", tracking_url="t2", slug="s2"),
            ]
        )
        db.commit()
    rows = demand_product_matcher.match_products_for_demand("quạt mini", "quạt mini để bàn", {"price_max": 200000, "features": ["để bàn"]})
    assert rows
    assert rows[0]["affiliate_url"].endswith("/quat")
    assert all("ao" not in row["affiliate_url"] for row in rows)


def test_demand_comment_max_four_links_and_no_fake_review() -> None:
    products = [
        {"name": f"Quạt mini mẫu {idx}", "affiliate_url": f"https://s.shopee.vn/{idx}", "match_score": 80}
        for idx in range(6)
    ]
    result = generate_demand_comment({}, {"category": "quạt mini", "intent": "ask_recommendation", "constraints": {}}, products)
    assert result["product_count"] <= 4
    assert result["comment"].count("https://") <= 4
    assert "mình dùng rồi" not in result["comment"].lower()


def test_scan_demand_deduplicates_external_post(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr(threads_demand_scanner, "SessionLocal", Session)
    monkeypatch.setattr(demand_product_matcher, "SessionLocal", Session)
    monkeypatch.setattr(threads_demand_scanner, "get_settings", lambda: SimpleNamespace(
        threads_demand_scanner_enabled=True,
        threads_demand_min_score=70,
        threads_demand_max_links_per_comment=4,
        threads_demand_opportunity_ttl_hours=36,
        threads_demand_max_results_per_scan=10,
        threads_demand_max_approve_batch=5,
        threads_demand_max_reply_batch=3,
        threads_demand_max_replies_per_account_per_day=3,
        threads_demand_reply_cooldown_minutes=30,
    ))
    monkeypatch.setenv("THREADS_ACCOUNTS", "acc1")
    monkeypatch.setenv("THREADS_ACC1_USER_ID", "111")
    monkeypatch.setenv("THREADS_ACC1_ACCESS_TOKEN", "tok1")
    monkeypatch.setattr(
        threads_demand_scanner,
        "search_keyword",
        lambda account, keyword, limit=20: [{"id": "ext1", "user_id": "222", "username": "u", "text": "xin link quạt mini để bàn dưới 200k"}],
    )
    with Session() as db:
        post = ThreadsPost(keyword="catalog", product_name="p", content="c", cta="", hashtags="[]", status="posted", quality_score=0)
        db.add(post)
        db.flush()
        db.add(ThreadsPostLink(post_id=post.id, product_name="Quạt mini để bàn sạc pin", affiliate_url="https://s.shopee.vn/quat", price="150000", tracking_url="t1", slug="s1"))
        db.commit()
    first = threads_demand_scanner.scan_threads_demand("acc1", ["xin link quạt mini"], max_opportunities=2)
    second = threads_demand_scanner.scan_threads_demand("acc1", ["xin link quạt mini"], max_opportunities=2)
    assert first["opportunities_created"] == 1
    assert second["duplicates_skipped"] == 1


def test_replybuy_requires_approval_and_expiry(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr(threads_demand_scanner, "SessionLocal", Session)
    monkeypatch.setattr(threads_demand_scanner, "get_settings", lambda: SimpleNamespace(
        threads_demand_max_links_per_comment=4,
        threads_demand_max_replies_per_account_per_day=3,
        threads_demand_reply_cooldown_minutes=30,
    ))
    monkeypatch.setenv("THREADS_ACCOUNTS", "acc1")
    monkeypatch.setenv("THREADS_ACC1_USER_ID", "111")
    monkeypatch.setenv("THREADS_ACC1_ACCESS_TOKEN", "tok1")
    with Session() as db:
        db.add(
            ThreadsDemandOpportunity(
                external_post_id="ext1",
                source_text_excerpt="xin link quạt",
                matched_keyword="quạt",
                intent="ask_link",
                purchase_intent_score=90,
                category="quạt mini",
                normalized_query="quạt mini",
                matched_products_json="[]",
                suggested_comment="comment https://s.shopee.vn/1",
                status="new",
                scan_account_name="acc1",
                expires_at=threads_demand_scanner.datetime.now(threads_demand_scanner.timezone.utc) + threads_demand_scanner.timedelta(hours=1),
            )
        )
        db.commit()
    ok, message = threads_demand_scanner.reply_opportunity(1, "acc1")
    assert not ok
    assert "approved" in message
    assert threads_demand_scanner.approve_opportunity(1)[0]
    monkeypatch.setattr(threads_demand_scanner, "publish_reply", lambda account, post_id, text: {"id": "reply1"})
    ok, message = threads_demand_scanner.reply_opportunity(1, "acc1")
    assert ok
    with Session() as db:
        assert db.get(ThreadsDemandOpportunity, 1).status == "replied"


def test_demand_batch_limits(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr(threads_demand_scanner, "SessionLocal", Session)
    with Session() as db:
        for idx in range(7):
            db.add(
                ThreadsDemandOpportunity(
                    external_post_id=f"ext{idx}",
                    source_text_excerpt="xin link quạt",
                    matched_keyword="quạt",
                    intent="ask_link",
                    purchase_intent_score=90,
                    category="quạt mini",
                    normalized_query="quạt mini",
                    matched_products_json="[]",
                    suggested_comment=f"comment {idx} https://s.shopee.vn/{idx}",
                    status="new",
                    scan_account_name="acc1",
                    expires_at=threads_demand_scanner.datetime.now(threads_demand_scanner.timezone.utc) + threads_demand_scanner.timedelta(hours=1),
                )
            )
        db.commit()
    approved = threads_demand_scanner.approve_batch([1, 2, 3, 4, 5, 6, 7])
    assert len(approved["approved"]) <= 5
    monkeypatch.setattr(threads_demand_scanner, "reply_opportunity", lambda opportunity_id, account_name=None: (True, "ok"))
    replied = threads_demand_scanner.reply_batch([1, 2, 3, 4], "acc1")
    assert len(replied["results"]) <= 3


def test_daily_limit_blocks_reply(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr(threads_demand_scanner, "SessionLocal", Session)
    monkeypatch.setattr(threads_demand_scanner, "get_settings", lambda: SimpleNamespace(
        threads_demand_max_links_per_comment=4,
        threads_demand_max_replies_per_account_per_day=3,
        threads_demand_reply_cooldown_minutes=30,
    ))
    monkeypatch.setenv("THREADS_ACCOUNTS", "acc1")
    monkeypatch.setenv("THREADS_ACC1_USER_ID", "111")
    monkeypatch.setenv("THREADS_ACC1_ACCESS_TOKEN", "tok1")
    with Session() as db:
        for idx in range(3):
            db.add(ThreadsDemandAction(action="replied", account_name="acc1", result="ok", details=""))
        db.add(
            ThreadsDemandOpportunity(
                external_post_id="ext-limit",
                source_text_excerpt="xin link quạt",
                matched_keyword="quạt",
                intent="ask_link",
                purchase_intent_score=90,
                category="quạt mini",
                normalized_query="quạt mini",
                matched_products_json="[]",
                suggested_comment="comment https://s.shopee.vn/1",
                status="approved",
                scan_account_name="acc1",
                expires_at=threads_demand_scanner.datetime.now(threads_demand_scanner.timezone.utc) + threads_demand_scanner.timedelta(hours=1),
            )
        )
        db.commit()
    ok, message = threads_demand_scanner.reply_opportunity(1, "acc1")
    assert not ok
    assert "daily" in message


def test_telegram_webhook_rejects_invalid_secret(monkeypatch) -> None:
    import app.api as api_module
    import app.config as config_module

    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "secret")
    config_module.get_settings.cache_clear()

    async def fake_process(payload):
        raise AssertionError("should not process invalid webhook")

    monkeypatch.setattr(api_module, "process_telegram_update", fake_process)
    client = TestClient(api_module.app)
    response = client.post("/api/telegram/webhook", json={"update_id": 1}, headers={"X-Telegram-Bot-Api-Secret-Token": "bad"})
    assert response.status_code == 401


def test_telegram_webhook_accepts_valid_secret(monkeypatch) -> None:
    import app.api as api_module
    import app.config as config_module

    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "secret")
    config_module.get_settings.cache_clear()
    seen = {}

    async def fake_process(payload):
        seen["payload"] = payload

    monkeypatch.setattr(api_module, "process_telegram_update", fake_process)
    monkeypatch.setattr(api_module, "claim_telegram_update", lambda update_id: True)
    client = TestClient(api_module.app)
    response = client.post("/api/telegram/webhook", json={"update_id": 1}, headers={"X-Telegram-Bot-Api-Secret-Token": "secret"})
    assert response.status_code == 200
    assert seen["payload"]["update_id"] == 1


def test_telegram_webhook_skips_duplicate_update(monkeypatch) -> None:
    import app.api as api_module
    import app.config as config_module

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    monkeypatch.setattr(api_module, "SessionLocal", Session)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "")
    config_module.get_settings.cache_clear()
    calls = {"count": 0}

    async def fake_process(payload):
        calls["count"] += 1

    monkeypatch.setattr(api_module, "process_telegram_update", fake_process)
    client = TestClient(api_module.app)
    first = client.post("/api/telegram/webhook", json={"update_id": 123})
    second = client.post("/api/telegram/webhook", json={"update_id": 123})
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["duplicate"]
    assert calls["count"] == 1


def test_cleanup_cron_requires_secret(monkeypatch) -> None:
    import app.api as api_module
    import app.config as config_module

    monkeypatch.setenv("CRON_SECRET", "cron")
    config_module.get_settings.cache_clear()
    client = TestClient(api_module.app)
    assert client.get("/api/cron/cleanup-daily-links").status_code == 401


def test_cleanup_cron_accepts_bearer_secret(monkeypatch) -> None:
    import app.api as api_module
    import app.config as config_module

    monkeypatch.setenv("CRON_SECRET", "cron")
    config_module.get_settings.cache_clear()
    monkeypatch.setattr(api_module, "cleanup_expired_daily_links", lambda retention_days: {"cutoff_date": "2026-07-08", "entries_deleted": 0})
    client = TestClient(api_module.app)
    response = client.get("/api/cron/cleanup-daily-links", headers={"Authorization": "Bearer cron"})
    assert response.status_code == 200
    assert response.json()["cutoff_date"] == "2026-07-08"


def test_main_vercel_does_not_start_polling(monkeypatch) -> None:
    import app.main as main_module

    monkeypatch.setattr(main_module, "get_settings", lambda: SimpleNamespace(vercel=True, telegram_use_webhook=False))
    monkeypatch.setattr(main_module, "run_api", lambda: (_ for _ in ()).throw(AssertionError("api should not run in vercel import mode")))
    monkeypatch.setattr(main_module, "build_application", lambda: (_ for _ in ()).throw(AssertionError("polling should not start on vercel")))
    assert main_module.main() is None


def test_main_local_polling_still_runs(monkeypatch) -> None:
    import app.main as main_module

    class DummyThread:
        def __init__(self, target=None, daemon=None):
            self.target = target
            self.daemon = daemon

        def start(self):
            return None

    class DummyApp:
        def __init__(self):
            self.polled = False

        def run_polling(self):
            self.polled = True

    dummy = DummyApp()
    monkeypatch.setattr(main_module, "get_settings", lambda: SimpleNamespace(vercel=False, telegram_use_webhook=False, tracking_port=8000))
    monkeypatch.setattr(main_module.threading, "Thread", DummyThread)
    monkeypatch.setattr(main_module, "is_feature_enabled", lambda name: False)
    monkeypatch.setattr(main_module, "build_application", lambda: dummy)
    main_module.main()
    assert dummy.polled


def test_importdaily_parser_keeps_quoted_windows_path() -> None:
    from app.telegram_bot import _parse_importdaily_args, _parse_importdaily_upload_caption

    path, import_date, link_type_id = _parse_importdaily_args(
        '/importdaily "C:\\Users\\duyqu\\Downloads\\Lay link san pham hang loat20260711160341-test.csv" 2026-07-11 xtra_commission'
    )
    assert path == "C:\\Users\\duyqu\\Downloads\\Lay link san pham hang loat20260711160341-test.csv"
    assert import_date == "2026-07-11"
    assert link_type_id == "xtra_commission"
    upload_date, upload_type = _parse_importdaily_upload_caption("/importdaily today xtra_commission")
    assert upload_date == "today"
    assert upload_type == "xtra_commission"
