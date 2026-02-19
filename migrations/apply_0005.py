"""Apply migration 0005: presentation AI fields + collections tables."""
import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ.get('DATABASE_URL') or 'sqlite:///SLIDESHARE/db.sqlite'
engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    cols = [row[1] for row in conn.execute(text("PRAGMA table_info('presentation')"))]
    if 'downloads' not in cols:
        conn.execute(text("ALTER TABLE presentation ADD COLUMN downloads INTEGER DEFAULT 0"))
        print('Added presentation.downloads')
    if 'ai_title' not in cols:
        conn.execute(text("ALTER TABLE presentation ADD COLUMN ai_title TEXT"))
        print('Added presentation.ai_title')
    if 'ai_description' not in cols:
        conn.execute(text("ALTER TABLE presentation ADD COLUMN ai_description TEXT"))
        print('Added presentation.ai_description')
    if 'ai_summary' not in cols:
        conn.execute(text("ALTER TABLE presentation ADD COLUMN ai_summary TEXT"))
        print('Added presentation.ai_summary')

    conn.execute(text(
        """
        CREATE TABLE IF NOT EXISTS collection (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT
        )
        """
    ))
    conn.execute(text(
        """
        CREATE TABLE IF NOT EXISTS collectionitem (
            id INTEGER PRIMARY KEY,
            collection_id INTEGER NOT NULL,
            presentation_id INTEGER NOT NULL,
            created_at TEXT,
            CONSTRAINT uq_collection_item UNIQUE (collection_id, presentation_id)
        )
        """
    ))
    conn.commit()
    print('Migration applied: collections tables ready')
