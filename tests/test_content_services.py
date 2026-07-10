from types import SimpleNamespace

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.models import Base, ThreadsDemandAction, ThreadsDemandOpportunity, ThreadsPost, ThreadsPostLink, ThreadsPostMetric, ThreadsReply
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
from app.services.threads_account_service import get_threads_account, load_threads_accounts, select_account_for_post
from app.services.reply_analysis import analyze_reply, calculate_purchase_intent_score
from app.services import demand_product_matcher, threads_analytics_scheduler, threads_demand_scanner, threads_insights_service, threads_reply_service, threads_sync_service, trend_service
from app.services.demand_comment_generator import generate_demand_comment
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

    engine = create_engine("sqlite:///:memory:")
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


def test_purchase_intent_rules_and_price_extract() -> None:
    ask_link = classify_purchase_intent("Cho mình xin link quạt mini để bàn dưới 200k với", "quạt mini")
    reco = classify_purchase_intent("Mọi người recommend áo đá bóng loại nào ổn?", "áo đá bóng")
    general = classify_purchase_intent("Hôm nay bàn làm việc bừa quá", None)
    assert ask_link["intent"] == "ask_link"
    assert ask_link["constraints"]["price_max"] == 200000
    assert ask_link["eligible"]
    assert reco["intent"] == "ask_recommendation"
    assert not general["eligible"]


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
