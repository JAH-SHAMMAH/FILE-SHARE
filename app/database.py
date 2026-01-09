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
    DATABASE_URL, echo=False, connect_args={"check_same_thread": False}
)


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
            # add airesult table if missing - create_all will handle it, but ensure column migration idempotent
    except Exception:
        # best-effort only; do not fail startup if migration cannot run
        pass


def get_session():
    with Session(engine) as session:
        yield session
