"""Add site_role column to user table

Revision ID: 0004_add_site_role
"""
from sqlalchemy import text


def upgrade(engine):
    """Add the `site_role` column to the `user` table."""
    with engine.connect() as conn:
        # SQLite supports ADD COLUMN for simple cases
        conn.execute(text("ALTER TABLE user ADD COLUMN site_role TEXT"))
        conn.commit()


def downgrade(engine):
    """Downgrade is a no-op for SQLite (dropping columns is unsupported)."""
    # Not implemented: dropping a column in SQLite requires table rebuild.
    return
