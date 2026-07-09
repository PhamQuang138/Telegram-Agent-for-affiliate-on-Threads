from sqlalchemy import create_engine

from app.services.content_engine import generate_affiliate_content
from app.services.content_quality import evaluate_content
from app.services.content_similarity import is_too_similar
from app.services.product_scoring import score_products


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
    assert isinstance(result["selected_products"], list)
    assert 0 <= result["quality_score"] <= 100


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

    with engine.begin() as connection:
        columns = {
            row["name"]
            for row in connection.exec_driver_sql("PRAGMA table_info('threads_posts')").mappings().all()
        }

    assert "need" in columns
    assert "performance_score" in columns
