from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, ThreadsPost
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
