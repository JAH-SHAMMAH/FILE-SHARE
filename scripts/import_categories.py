import os
import json
import sys

# ensure app package is importable
ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)

from app.database import engine, create_db_and_tables
from sqlmodel import Session, select
from app.models import Category


def main():
    create_db_and_tables()
    data_path = os.path.join(ROOT, "data", "categories.json")
    if not os.path.exists(data_path):
        print("categories.json not found at", data_path)
        return

    with open(data_path, "r", encoding="utf-8") as f:
        cats = json.load(f)

    inserted = 0
    skipped = 0
    with Session(engine) as session:
        for name in cats:
            name = (name or "").strip()
            if not name:
                continue
            q = select(Category).where(Category.name == name)
            if session.exec(q).first():
                skipped += 1
                continue
            session.add(Category(name=name))
            inserted += 1
        session.commit()

    print(f"Inserted {inserted} categories, skipped {skipped} existing.")


if __name__ == "__main__":
    main()
