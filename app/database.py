import os
from sqlmodel import create_engine, SQLModel, Session
from dotenv import load_dotenv

load_dotenv()

from pathlib import Path

# Default to the repository-level db.sqlite next to the SLIDESHARE package
default_db_path = Path(__file__).resolve().parents[1] / "db.sqlite"
default_db_url = f"sqlite:///{default_db_path.as_posix()}"
DATABASE_URL = os.getenv("DATABASE_URL", default_db_url)
engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False, "timeout": 30},
)

# Enable WAL journal mode where possible to reduce write-lock contention on SQLite
try:
    with engine.connect() as conn:
        try:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        try:
            conn.exec_driver_sql("PRAGMA synchronous=NORMAL")
        except Exception:
            pass
except Exception:
    pass


def create_db_and_tables():
    print(f"[db] creating tables on {DATABASE_URL}")
    SQLModel.metadata.create_all(engine)
    # perform simple additive migrations for SQLite: add new columns if missing
    try:
        with engine.connect() as conn:
            # check user table columns
            res = conn.exec_driver_sql("PRAGMA table_info('user')").fetchall()
            cols = [r[1] for r in res]
            if 'spotify_refresh_token' not in cols:
                try:
                    conn.exec_driver_sql("ALTER TABLE user ADD COLUMN spotify_refresh_token TEXT")
                except Exception:
                    pass
            # add site_role column if missing (used for persisted role choices)
            if 'site_role' not in cols:
                try:
                    conn.exec_driver_sql("ALTER TABLE user ADD COLUMN site_role TEXT")
                except Exception:
                    pass
            # check presentation table columns
            res2 = conn.exec_driver_sql("PRAGMA table_info('presentation')").fetchall()
            pcols = [r[1] for r in res2]
            if 'music_url' not in pcols:
                try:
                    conn.exec_driver_sql("ALTER TABLE presentation ADD COLUMN music_url TEXT")
                except Exception:
                    pass
            # add presentation.file_size and language if missing
            if 'file_size' not in pcols:
                try:
                    conn.exec_driver_sql("ALTER TABLE presentation ADD COLUMN file_size INTEGER")
                except Exception:
                    pass
            if 'language' not in pcols:
                try:
                    conn.exec_driver_sql("ALTER TABLE presentation ADD COLUMN language TEXT")
                except Exception:
                    pass
            # classroom -> space terminology migration bridge (idempotent)
            #
            # The app is being refactored from classroom_id/classroom tables to space_id/space tables.
            # For SQLite we do additive migrations so existing data keeps working.

            def _table_cols(table: str) -> list[str]:
                try:
                    res = conn.exec_driver_sql(f"PRAGMA table_info('{table}')").fetchall()
                    return [r[1] for r in res]
                except Exception:
                    return []

            def _add_space_id(table: str) -> None:
                cols = _table_cols(table)
                if not cols:
                    return
                if 'space_id' in cols:
                    return
                if 'classroom_id' not in cols:
                    return
                try:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN space_id INTEGER")
                except Exception:
                    return
                try:
                    conn.exec_driver_sql(
                        f"UPDATE {table} SET space_id = classroom_id WHERE space_id IS NULL"
                    )
                except Exception:
                    pass

            # Ensure new space table has the old classroom rows
            if _table_cols('classroom') and _table_cols('space'):
                try:
                    conn.exec_driver_sql(
                        "INSERT OR IGNORE INTO space (id, school_id, name, code, created_at) "
                        "SELECT id, school_id, name, code, created_at FROM classroom"
                    )
                except Exception:
                    pass

            # Add and backfill space_id columns on existing tables that still have classroom_id
            for t in ['membership', 'assignment', 'attendance', 'libraryitem', 'studentanalytics']:
                _add_space_id(t)

            # Migrate classroom chat messages into spacemessage table (created by create_all)
            if _table_cols('classroommessage') and _table_cols('spacemessage'):
                try:
                    conn.exec_driver_sql(
                        "INSERT OR IGNORE INTO spacemessage (id, space_id, sender_id, content, created_at) "
                        "SELECT id, classroom_id, sender_id, content, created_at FROM classroommessage"
                    )
                except Exception:
                    pass

            try:
                conn.commit()
            except Exception:
                pass
    except Exception:
        # best-effort only; do not fail startup if migration cannot run
        pass


def get_session():
    with Session(engine) as session:
        yield session
