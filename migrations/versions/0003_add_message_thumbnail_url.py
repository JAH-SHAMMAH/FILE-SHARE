"""Add thumbnail_url column to message table

Revision ID: 0003_add_message_thumbnail_url
"""
from sqlalchemy import text


def upgrade(engine):
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE message ADD COLUMN thumbnail_url TEXT"))
        conn.commit()


def downgrade(engine):
    # Dropping columns in SQLite requires table rebuild; leave as no-op
    return
