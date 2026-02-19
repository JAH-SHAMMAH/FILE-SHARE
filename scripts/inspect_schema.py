import os
import sqlite3
from pathlib import Path


def main() -> None:
    db_path = Path(__file__).resolve().parents[1] / "db.sqlite"
    print("db:", db_path, "exists:", db_path.exists())
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()

    cur.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table','view') ORDER BY type, name")
    rows = cur.fetchall()
    print("objects:", len(rows))
    for name, typ in rows:
        if name.startswith("sqlite_"):
            continue
        print(f"{typ}: {name}")

    def show(table: str) -> None:
        cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
        if not cur.fetchone():
            return
        cur.execute(f"PRAGMA table_info('{table}')")
        cols = [r[1] for r in cur.fetchall()]
        print("\nTABLE", table)
        print(cols)

    for t in [
        "membership",
        "classroom",
        "classrooms",
        "space",
        "classroommessage",
        "spacemessage",
        "message",
        "assignment",
        "attendance",
        "libraryitem",
        "studentanalytics",
    ]:
        show(t)

    con.close()


if __name__ == "__main__":
    main()
