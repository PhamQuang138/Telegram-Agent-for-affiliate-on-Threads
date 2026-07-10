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
