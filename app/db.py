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
        _allow_duplicate_post_link_affiliate_urls()


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
