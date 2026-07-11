from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _normalized_database_url() -> str:
    url = get_settings().database_url
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


_engine_kwargs = {
    "connect_args": {"check_same_thread": False}
    if get_settings().database_url.startswith("sqlite")
    else {},
    "pool_pre_ping": True,
}
if get_settings().vercel:
    _engine_kwargs["poolclass"] = NullPool

engine = create_engine(_normalized_database_url(), **_engine_kwargs)
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
        _migrate_demand_opportunity_tables()
        _migrate_daily_link_catalog_tables()
        _migrate_admin_curated_link_tables()
        _allow_duplicate_post_link_affiliate_urls()
    elif _normalized_database_url().startswith("postgresql"):
        _migrate_threads_posts_columns_postgres()
        _migrate_daily_link_catalog_tables_postgres()
        _migrate_admin_curated_link_tables_postgres()


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
        "telegram_cta_text": "TEXT",
        "telegram_cta_mode": "VARCHAR(32)",
        "telegram_cta_reply_id": "VARCHAR(255)",
        "telegram_cta_posted_at": "DATETIME",
        "telegram_cta_status": "VARCHAR(32)",
    }
    with engine.begin() as connection:
        existing = {
            row["name"]
            for row in connection.exec_driver_sql("PRAGMA table_info('threads_posts')").mappings().all()
        }
        for column, column_type in columns.items():
            if column not in existing:
                connection.exec_driver_sql(f"ALTER TABLE threads_posts ADD COLUMN {column} {column_type}")


def _migrate_threads_posts_columns_postgres() -> None:
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
        "click_count": "INTEGER DEFAULT 0",
        "impression_estimate": "INTEGER",
        "performance_score": "FLOAT",
        "posted_account_name": "VARCHAR(128)",
        "posted_account_user_id": "VARCHAR(255)",
        "telegram_cta_text": "TEXT",
        "telegram_cta_mode": "VARCHAR(32)",
        "telegram_cta_reply_id": "VARCHAR(255)",
        "telegram_cta_posted_at": "TIMESTAMP WITH TIME ZONE",
        "telegram_cta_status": "VARCHAR(32)",
    }
    with engine.begin() as connection:
        for column, column_type in columns.items():
            connection.exec_driver_sql(
                f"ALTER TABLE threads_posts ADD COLUMN IF NOT EXISTS {column} {column_type}"
            )


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


def _migrate_demand_opportunity_tables() -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS demand_opportunities (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                platform VARCHAR(64) NOT NULL DEFAULT 'threads',
                content_type VARCHAR(64) NOT NULL DEFAULT 'post',
                external_content_id VARCHAR(255),
                source_url TEXT,
                author_username VARCHAR(255),
                source_text_excerpt TEXT NOT NULL DEFAULT '',
                content_hash VARCHAR(64) NOT NULL,
                matched_query VARCHAR(255),
                intent VARCHAR(64) NOT NULL DEFAULT '',
                purchase_intent_score FLOAT NOT NULL DEFAULT 0,
                category VARCHAR(255) NOT NULL DEFAULT '',
                normalized_query TEXT NOT NULL DEFAULT '',
                constraints_json TEXT NOT NULL DEFAULT '{}',
                matched_products_json TEXT NOT NULL DEFAULT '[]',
                suggested_response TEXT NOT NULL DEFAULT '',
                response_mode VARCHAR(32) NOT NULL DEFAULT 'manual_copy',
                status VARCHAR(32) NOT NULL DEFAULT 'new',
                intake_source VARCHAR(64) NOT NULL DEFAULT 'telegram_manual',
                scan_account_name VARCHAR(128),
                reply_account_name VARCHAR(128),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                expires_at DATETIME,
                approved_at DATETIME,
                replied_at DATETIME,
                external_reply_id VARCHAR(255),
                error_message TEXT
            )
            """
        )
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_demand_opportunities_platform ON demand_opportunities (platform)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_demand_opportunities_external_content_id ON demand_opportunities (external_content_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_demand_opportunities_content_hash ON demand_opportunities (content_hash)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_demand_opportunities_status ON demand_opportunities (status)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_demand_opportunities_intake_source ON demand_opportunities (intake_source)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_demand_opportunities_created_at ON demand_opportunities (created_at)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_demand_opportunities_expires_at ON demand_opportunities (expires_at)")

        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS demand_actions (
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
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_demand_actions_opportunity_id ON demand_actions (opportunity_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_demand_actions_action ON demand_actions (action)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_demand_actions_account_name ON demand_actions (account_name)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_demand_actions_created_at ON demand_actions (created_at)")

        legacy_exists = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='threads_demand_opportunities'"
        ).first()
        if legacy_exists:
            connection.exec_driver_sql(
                """
                INSERT INTO demand_opportunities (
                    platform, content_type, external_content_id, source_url, author_username,
                    source_text_excerpt, content_hash, matched_query, intent, purchase_intent_score,
                    category, normalized_query, constraints_json, matched_products_json,
                    suggested_response, response_mode, status, intake_source, scan_account_name,
                    reply_account_name, created_at, expires_at, approved_at, replied_at,
                    external_reply_id, error_message
                )
                SELECT
                    'threads', 'post', external_post_id, permalink, author_username,
                    source_text_excerpt, lower(hex(randomblob(16))), matched_keyword, intent,
                    purchase_intent_score, category, normalized_query, constraints_json,
                    matched_products_json, suggested_comment,
                    CASE WHEN threads_reply_id IS NOT NULL AND threads_reply_id != '' THEN 'api_reply' ELSE 'manual_copy' END,
                    status, 'api_scan', scan_account_name, reply_account_name, created_at,
                    expires_at, approved_at, replied_at, threads_reply_id, error_message
                FROM threads_demand_opportunities old
                WHERE NOT EXISTS (
                    SELECT 1 FROM demand_opportunities d
                    WHERE d.platform = 'threads' AND d.external_content_id = old.external_post_id
                )
                """
            )


def _migrate_daily_link_catalog_tables() -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS affiliate_products (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                product_name TEXT NOT NULL DEFAULT '',
                affiliate_url TEXT NOT NULL UNIQUE,
                product_url TEXT,
                price VARCHAR(64),
                shop_name VARCHAR(255),
                link_type_id VARCHAR(64) NOT NULL DEFAULT 'shopee_commission',
                category_id VARCHAR(64) NOT NULL DEFAULT 'other',
                subcategory VARCHAR(128),
                subcategory_id VARCHAR(128),
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
            )
            """
        )
        connection.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS ix_affiliate_products_affiliate_url ON affiliate_products (affiliate_url)")
        _add_sqlite_column_if_missing(connection, "affiliate_products", "link_type_id", "VARCHAR(64) NOT NULL DEFAULT 'shopee_commission'")
        _add_sqlite_column_if_missing(connection, "affiliate_products", "subcategory_id", "VARCHAR(128)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_affiliate_products_category_id ON affiliate_products (category_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_affiliate_products_link_type_id ON affiliate_products (link_type_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_affiliate_products_is_active ON affiliate_products (is_active)")
        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS affiliate_import_batches (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                batch_name VARCHAR(255),
                import_date VARCHAR(10) NOT NULL,
                source VARCHAR(255) NOT NULL DEFAULT '',
                total_rows INTEGER NOT NULL DEFAULT 0,
                imported_count INTEGER NOT NULL DEFAULT 0,
                duplicate_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                type_stats_json TEXT NOT NULL DEFAULT '{}',
                category_stats_json TEXT NOT NULL DEFAULT '{}',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL
            )
            """
        )
        _add_sqlite_column_if_missing(connection, "affiliate_import_batches", "type_stats_json", "TEXT NOT NULL DEFAULT '{}'")
        _add_sqlite_column_if_missing(connection, "affiliate_import_batches", "category_stats_json", "TEXT NOT NULL DEFAULT '{}'")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_affiliate_import_batches_import_date ON affiliate_import_batches (import_date)")
        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS daily_link_entries (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                import_date VARCHAR(10) NOT NULL,
                batch_id INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                CONSTRAINT uq_daily_link_product_date UNIQUE (product_id, import_date),
                FOREIGN KEY(product_id) REFERENCES affiliate_products (id),
                FOREIGN KEY(batch_id) REFERENCES affiliate_import_batches (id)
            )
            """
        )
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_daily_link_entries_product_id ON daily_link_entries (product_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_daily_link_entries_import_date ON daily_link_entries (import_date)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_daily_link_entries_batch_id ON daily_link_entries (batch_id)")


def _add_sqlite_column_if_missing(connection, table: str, column: str, column_type: str) -> None:
    existing = {
        row["name"]
        for row in connection.exec_driver_sql(f"PRAGMA table_info('{table}')").mappings().all()
    }
    if column not in existing:
        connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def _migrate_daily_link_catalog_tables_postgres() -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE affiliate_products ADD COLUMN IF NOT EXISTS link_type_id VARCHAR(64) NOT NULL DEFAULT 'shopee_commission'"
        )
        connection.exec_driver_sql(
            "ALTER TABLE affiliate_products ADD COLUMN IF NOT EXISTS subcategory_id VARCHAR(128)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_affiliate_products_link_type_id ON affiliate_products (link_type_id)"
        )
        connection.exec_driver_sql(
            "ALTER TABLE affiliate_import_batches ADD COLUMN IF NOT EXISTS type_stats_json TEXT NOT NULL DEFAULT '{}'"
        )
        connection.exec_driver_sql(
            "ALTER TABLE affiliate_import_batches ADD COLUMN IF NOT EXISTS category_stats_json TEXT NOT NULL DEFAULT '{}'"
        )


def _migrate_admin_curated_link_tables() -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS admin_link_batches (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                admin_user_id INTEGER NOT NULL,
                group_chat_id VARCHAR(64) NOT NULL,
                link_type_id VARCHAR(64) NOT NULL,
                category_id VARCHAR(64) NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'active',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                closed_at DATETIME,
                link_count INTEGER NOT NULL DEFAULT 0,
                guide_message_id VARCHAR(255)
            )
            """
        )
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_admin_link_batches_admin_user_id ON admin_link_batches (admin_user_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_admin_link_batches_group_chat_id ON admin_link_batches (group_chat_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_admin_link_batches_status ON admin_link_batches (status)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_admin_link_batches_created_at ON admin_link_batches (created_at)")
        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS admin_affiliate_links (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL,
                admin_user_id INTEGER NOT NULL,
                group_chat_id VARCHAR(64) NOT NULL,
                link_type_id VARCHAR(64) NOT NULL,
                category_id VARCHAR(64) NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                affiliate_url TEXT NOT NULL,
                content_hash VARCHAR(64) NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                expires_at DATETIME NOT NULL,
                CONSTRAINT uq_admin_link_batch_url UNIQUE (batch_id, affiliate_url),
                FOREIGN KEY(batch_id) REFERENCES admin_link_batches (id)
            )
            """
        )
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_admin_affiliate_links_batch_id ON admin_affiliate_links (batch_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_admin_affiliate_links_link_type_id ON admin_affiliate_links (link_type_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_admin_affiliate_links_category_id ON admin_affiliate_links (category_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_admin_affiliate_links_is_active ON admin_affiliate_links (is_active)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_admin_affiliate_links_created_at ON admin_affiliate_links (created_at)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_admin_affiliate_links_expires_at ON admin_affiliate_links (expires_at)")
        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS private_link_requests (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                request_token VARCHAR(64) NOT NULL UNIQUE,
                telegram_user_id INTEGER NOT NULL,
                group_chat_id VARCHAR(64) NOT NULL DEFAULT '',
                link_type_id VARCHAR(64) NOT NULL,
                category_id VARCHAR(64) NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'pending',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                expires_at DATETIME NOT NULL,
                completed_at DATETIME
            )
            """
        )
        connection.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS ix_private_link_requests_request_token ON private_link_requests (request_token)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_private_link_requests_telegram_user_id ON private_link_requests (telegram_user_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_private_link_requests_status ON private_link_requests (status)")


def _migrate_admin_curated_link_tables_postgres() -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS admin_link_batches (
                id SERIAL PRIMARY KEY,
                admin_user_id INTEGER NOT NULL,
                group_chat_id VARCHAR(64) NOT NULL,
                link_type_id VARCHAR(64) NOT NULL,
                category_id VARCHAR(64) NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'active',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
                closed_at TIMESTAMP WITH TIME ZONE,
                link_count INTEGER NOT NULL DEFAULT 0,
                guide_message_id VARCHAR(255)
            )
            """
        )
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_admin_link_batches_admin_user_id ON admin_link_batches (admin_user_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_admin_link_batches_group_chat_id ON admin_link_batches (group_chat_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_admin_link_batches_status ON admin_link_batches (status)")
        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS admin_affiliate_links (
                id SERIAL PRIMARY KEY,
                batch_id INTEGER NOT NULL REFERENCES admin_link_batches(id),
                admin_user_id INTEGER NOT NULL,
                group_chat_id VARCHAR(64) NOT NULL,
                link_type_id VARCHAR(64) NOT NULL,
                category_id VARCHAR(64) NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                affiliate_url TEXT NOT NULL,
                content_hash VARCHAR(64) NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
                expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
                CONSTRAINT uq_admin_link_batch_url UNIQUE (batch_id, affiliate_url)
            )
            """
        )
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_admin_affiliate_links_batch_id ON admin_affiliate_links (batch_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_admin_affiliate_links_link_type_id ON admin_affiliate_links (link_type_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_admin_affiliate_links_category_id ON admin_affiliate_links (category_id)")
        connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_admin_affiliate_links_is_active ON admin_affiliate_links (is_active)")
        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS private_link_requests (
                id SERIAL PRIMARY KEY,
                request_token VARCHAR(64) NOT NULL UNIQUE,
                telegram_user_id INTEGER NOT NULL,
                group_chat_id VARCHAR(64) NOT NULL DEFAULT '',
                link_type_id VARCHAR(64) NOT NULL,
                category_id VARCHAR(64) NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
                expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
                completed_at TIMESTAMP WITH TIME ZONE
            )
            """
        )
        connection.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS ix_private_link_requests_request_token ON private_link_requests (request_token)")


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
