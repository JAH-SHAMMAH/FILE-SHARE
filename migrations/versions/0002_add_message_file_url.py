"""Add file_url column to message table

Revision ID: 0002_add_message_file_url
"""
from sqlalchemy import text


def upgrade(engine):
    """Add the `file_url` column to the `message` table."""
    with engine.connect() as conn:
        # SQLite supports ADD COLUMN for simple cases
        conn.execute(text("ALTER TABLE message ADD COLUMN file_url TEXT"))
        conn.commit()


def downgrade(engine):
    """Downgrade is a no-op for SQLite (dropping columns is unsupported)."""
    # Not implemented: dropping a column in SQLite requires table rebuild.
    return
