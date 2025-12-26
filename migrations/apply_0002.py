"""Apply migration 0002: add file_url column to message table if missing."""
import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ.get('DATABASE_URL') or 'sqlite:///SLIDESHARE/db.sqlite'
engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    # Check if column exists
    try:
        res = conn.execute(text("PRAGMA table_info('message')"))
        cols = [row[1] for row in res]
    except Exception:
        # Not SQLite or PRAGMA failed; try generic inspection
        res = conn.execute(text("SELECT * FROM message LIMIT 1"))
        cols = res.keys()

    if 'file_url' in cols:
        print('file_url column already exists; nothing to do')
    else:
        print('Adding file_url column to message table...')
        conn.execute(text('ALTER TABLE message ADD COLUMN file_url TEXT'))
        conn.commit()
        print('Migration applied: file_url added')
