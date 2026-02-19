"""Add presentation AI fields, downloads, and collections tables

Revision ID: 0005_add_presentation_ai_and_collections
"""
from sqlalchemy import text


def upgrade(engine):
    with engine.connect() as conn:
        # presentation columns
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info('presentation')"))]
        if 'downloads' not in cols:
            conn.execute(text("ALTER TABLE presentation ADD COLUMN downloads INTEGER DEFAULT 0"))
        if 'ai_title' not in cols:
            conn.execute(text("ALTER TABLE presentation ADD COLUMN ai_title TEXT"))
        if 'ai_description' not in cols:
            conn.execute(text("ALTER TABLE presentation ADD COLUMN ai_description TEXT"))
        if 'ai_summary' not in cols:
            conn.execute(text("ALTER TABLE presentation ADD COLUMN ai_summary TEXT"))

        # collection tables
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


def downgrade(engine):
    # SQLite: dropping columns/tables is not supported without rebuild.
    return
