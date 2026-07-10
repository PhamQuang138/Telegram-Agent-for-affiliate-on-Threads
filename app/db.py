from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    get_settings().database_url,
    connect_args={"check_same_thread": False}
    if get_settings().database_url.startswith("sqlite")
    else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    if get_settings().database_url.startswith("sqlite"):
        _migrate_threads_posts_columns()
        _migrate_topic_memory_table()
        _migrate_app_settings_table()
        _migrate_threads_analytics_tables()
        _migrate_threads_demand_tables()
        _allow_duplicate_post_link_affiliate_urls()


def _migrate_threads_posts_columns() -> None:
    columns = {
        "need": "TEXT",
        "persona": "VARCHAR(255)",
        "angle": "TEXT",
        "persona_id": "VARCHAR(128)",
        "angle_id": "VARCHAR(128)",
        "hook": "TEXT",
        "hook_type": "VARCHAR(255)",
        "story_type": "VARCHAR(255)",
        "content_type": "VARCHAR(128)",
        "content_goal": "VARCHAR(64)",
        "diversity_key": "VARCHAR(255)",
        "target_platform": "VARCHAR(64)",
        "click_count": "INTEGER NOT NULL DEFAULT 0",
        "impression_estimate": "INTEGER",
        "performance_score": "FLOAT",
        "posted_account_name": "VARCHAR(128)",
        "posted_account_user_id": "VARCHAR(255)",
    }
    with engine.begin() as connection:
        existing = {
            row["name"]
            for row in connection.exec_driver_sql("PRAGMA table_info('threads_posts')").mappings().all()
        }
        for column, column_type in columns.items():
            if column not in existing:
                connection.exec_driver_sql(f"ALTER TABLE threads_posts ADD COLUMN {column} {column_type}")


def _migrate_topic_memory_table() -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS topic_memory (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                keyword VARCHAR(255) NOT NULL,
                product_ids_json TEXT NOT NULL DEFAULT '[]',
                post_id INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_topic_memory_keyword ON topic_memory (keyword)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_topic_memory_post_id ON topic_memory (post_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_topic_memory_created_at ON topic_memory (created_at)"
        )


def _migrate_app_settings_table() -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT NOT NULL PRIMARY KEY,
                value TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            )
            """
        )


def _migrate_threads_analytics_tables() -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS threads_post_metrics (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER,
                threads_media_id VARCHAR(255) NOT NULL,
                account_name VARCHAR(128) NOT NULL,
                threads_user_id VARCHAR(255),
                views INTEGER NOT NULL DEFAULT 0,
                likes INTEGER NOT NULL DEFAULT 0,
                replies INTEGER NOT NULL DEFAULT 0,
                reposts INTEGER NOT NULL DEFAULT 0,
                quotes INTEGER NOT NULL DEFAULT 0,
                shares INTEGER,
                click_count INTEGER NOT NULL DEFAULT 0,
                affiliate_ctr FLOAT,
                engagement_rate FLOAT,
                purchase_intent_score FLOAT,
                performance_score FLOAT,
                synced_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                CONSTRAINT uq_threads_metric_account_media UNIQUE (account_name, threads_media_id)
            )
            """
        )
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_threads_post_metrics_post_id ON threads_post_metrics (post_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_threads_post_metrics_threads_media_id ON threads_post_metrics (threads_media_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_threads_post_metrics_account_name ON threads_post_metrics (account_name)")

        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS threads_replies (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER,
                threads_media_id VARCHAR(255) NOT NULL,
                reply_media_id VARCHAR(255) NOT NULL,
                account_name VARCHAR(128) NOT NULL,
                reply_user_id VARCHAR(255),
                reply_username VARCHAR(255),
                reply_text TEXT NOT NULL DEFAULT '',
                intent VARCHAR(64) NOT NULL DEFAULT 'unknown',
                sentiment VARCHAR(32) NOT NULL DEFAULT 'neutral',
                asks_for_link INTEGER NOT NULL DEFAULT 0,
                asks_for_price INTEGER NOT NULL DEFAULT 0,
                product_interest INTEGER NOT NULL DEFAULT 0,
                is_spam INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME,
                synced_at DATETIME,
                CONSTRAINT uq_threads_reply_account_reply UNIQUE (account_name, reply_media_id)
            )
            """
        )
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_threads_replies_post_id ON threads_replies (post_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_threads_replies_threads_media_id ON threads_replies (threads_media_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_threads_replies_reply_media_id ON threads_replies (reply_media_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_threads_replies_account_name ON threads_replies (account_name)")

        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS threads_keyword_snapshots (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                keyword VARCHAR(255) NOT NULL,
                account_name VARCHAR(128) NOT NULL,
                result_count INTEGER NOT NULL DEFAULT 0,
                recent_result_count INTEGER NOT NULL DEFAULT 0,
                related_topics_json TEXT NOT NULL DEFAULT '[]',
                common_intents_json TEXT NOT NULL DEFAULT '[]',
                tone_summary TEXT NOT NULL DEFAULT '',
                sampled_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
            )
            """
        )
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_threads_keyword_snapshots_keyword ON threads_keyword_snapshots (keyword)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_threads_keyword_snapshots_account_name ON threads_keyword_snapshots (account_name)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_threads_keyword_snapshots_sampled_at ON threads_keyword_snapshots (sampled_at)")

        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS account_learning_profiles (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                account_name VARCHAR(128) NOT NULL UNIQUE,
                sample_size INTEGER NOT NULL DEFAULT 0,
                profile_json TEXT NOT NULL DEFAULT '{}',
                updated_at DATETIME
            )
            """
        )
        connection.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS ix_account_learning_profiles_account_name ON account_learning_profiles (account_name)")


def _migrate_threads_demand_tables() -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS threads_demand_opportunities (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                external_post_id VARCHAR(255) NOT NULL UNIQUE,
                author_id VARCHAR(255),
                author_username VARCHAR(255),
                permalink TEXT,
                source_text_excerpt TEXT NOT NULL DEFAULT '',
                matched_keyword VARCHAR(255) NOT NULL DEFAULT '',
                intent VARCHAR(64) NOT NULL DEFAULT '',
                purchase_intent_score FLOAT NOT NULL DEFAULT 0,
                category VARCHAR(255) NOT NULL DEFAULT '',
                normalized_query TEXT NOT NULL DEFAULT '',
                constraints_json TEXT NOT NULL DEFAULT '{}',
                matched_products_json TEXT NOT NULL DEFAULT '[]',
                suggested_comment TEXT NOT NULL DEFAULT '',
                status VARCHAR(32) NOT NULL DEFAULT 'new',
                scan_account_name VARCHAR(128) NOT NULL DEFAULT '',
                reply_account_name VARCHAR(128),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                expires_at DATETIME,
                approved_at DATETIME,
                replied_at DATETIME,
                threads_reply_id VARCHAR(255),
                error_message TEXT
            )
            """
        )
        connection.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS ix_threads_demand_opportunities_external_post_id ON threads_demand_opportunities (external_post_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_threads_demand_opportunities_status ON threads_demand_opportunities (status)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_threads_demand_opportunities_created_at ON threads_demand_opportunities (created_at)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_threads_demand_opportunities_expires_at ON threads_demand_opportunities (expires_at)")

        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS threads_demand_actions (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                opportunity_id INTEGER,
                action VARCHAR(64) NOT NULL,
                account_name VARCHAR(128),
                result VARCHAR(64) NOT NULL DEFAULT '',
                details TEXT NOT NULL DEFAULT '',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
            )
            """
        )
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_threads_demand_actions_opportunity_id ON threads_demand_actions (opportunity_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_threads_demand_actions_action ON threads_demand_actions (action)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_threads_demand_actions_account_name ON threads_demand_actions (account_name)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_threads_demand_actions_created_at ON threads_demand_actions (created_at)")


def _allow_duplicate_post_link_affiliate_urls() -> None:
    with engine.begin() as connection:
        indexes = connection.exec_driver_sql("PRAGMA index_list('threads_post_links')").mappings().all()
        has_unique_affiliate_index = False

        for index in indexes:
            if not index["unique"]:
                continue
            columns = connection.exec_driver_sql(f"PRAGMA index_info('{index['name']}')").mappings().all()
            if [column["name"] for column in columns] == ["affiliate_url"]:
                has_unique_affiliate_index = True
                break

        if not has_unique_affiliate_index:
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_threads_post_links_affiliate_url "
                "ON threads_post_links (affiliate_url)"
            )
            return

        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.exec_driver_sql(
            """
            CREATE TABLE threads_post_links_new (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                product_name TEXT NOT NULL,
                affiliate_url TEXT NOT NULL,
                product_url TEXT,
                price VARCHAR(64),
                shop_name VARCHAR(255),
                tracking_url TEXT NOT NULL,
                slug VARCHAR(64) NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                FOREIGN KEY(post_id) REFERENCES threads_posts (id)
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO threads_post_links_new (
                id, post_id, product_name, affiliate_url, product_url, price,
                shop_name, tracking_url, slug, created_at
            )
            SELECT
                id, post_id, product_name, affiliate_url, product_url, price,
                shop_name, tracking_url, slug, created_at
            FROM threads_post_links
            """
        )
        connection.exec_driver_sql("DROP TABLE threads_post_links")
        connection.exec_driver_sql("ALTER TABLE threads_post_links_new RENAME TO threads_post_links")
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_threads_post_links_post_id "
            "ON threads_post_links (post_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_threads_post_links_affiliate_url "
            "ON threads_post_links (affiliate_url)"
        )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_threads_post_links_slug "
            "ON threads_post_links (slug)"
        )
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
