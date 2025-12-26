"""Apply migration 0003: add thumbnail_url to message table."""
import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.environ.get('DATABASE_URL') or 'sqlite:///SLIDESHARE/db.sqlite'
engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    res = conn.execute(text("PRAGMA table_info('message')"))
    cols = [row[1] for row in res]
    if 'thumbnail_url' in cols:
        print('thumbnail_url already exists; nothing to do')
    else:
        print('Adding thumbnail_url column to message table...')
        conn.execute(text('ALTER TABLE message ADD COLUMN thumbnail_url TEXT'))
        conn.commit()
        print('Migration applied: thumbnail_url added')
