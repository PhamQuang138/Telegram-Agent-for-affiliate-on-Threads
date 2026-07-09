import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db import SessionLocal, init_db
from app.services.shopee_csv_importer import import_shopee_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Shopee affiliate CSV into Threads draft queue.")
    parser.add_argument("csv_path", help="Path to Shopee affiliate CSV")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of new drafts to create")
    parser.add_argument("--group-size", type=int, default=5, help="Product links per Threads post, from 1 to 6")
    args = parser.parse_args()

    csv_path = Path(args.csv_path).expanduser()
    if not csv_path.exists():
        raise SystemExit(f"CSV file not found: {csv_path}")

    init_db()
    with SessionLocal() as db:
        result = import_shopee_csv(db, csv_path, limit=args.limit, group_size=args.group_size)

    print(f"Created: {result.created}")
    print(f"Skipped: {result.skipped}")
    if result.errors:
        print("Errors:")
        for error in result.errors[:20]:
            print(f"- {error}")


if __name__ == "__main__":
    main()
