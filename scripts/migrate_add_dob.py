"""Safe SQLite migration: add `date_of_birth` column to `user` table if missing.

Usage:
  python scripts/migrate_add_dob.py [--db DATABASE_URL]

If DATABASE_URL not provided, reads from env var DATABASE_URL or defaults to sqlite:///./db.sqlite
"""
import os
import sys
import sqlite3
import argparse
from urllib.parse import urlparse


def sqlite_path_from_database_url(database_url: str) -> str:
    # Expect forms like sqlite:///./db.sqlite or sqlite:///C:/full/path/db.sqlite
    if not database_url.startswith('sqlite'):
        raise ValueError('This migration only supports sqlite DATABASE_URL values')
    # Strip sqlite:/// or sqlite:///
    if database_url.startswith('sqlite:///'):
        path = database_url[len('sqlite:///'):]
    elif database_url.startswith('sqlite://'):
        path = database_url[len('sqlite://'):]
    else:
        path = database_url
    # On Windows, a leading slash may precede the drive letter; normalize
    if path.startswith('/') and path[1:3].isalpha() and path[2] == ':' :
        path = path[1:]
    # If relative, make it workspace-relative (script's parent)
    if not os.path.isabs(path):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.normpath(os.path.join(base, path))
    return path


def has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute("PRAGMA table_info('%s')" % table)
    cols = [r[1] for r in cur.fetchall()]
    return column in cols


def add_column(conn: sqlite3.Connection, table: str, column: str, coltype: str = 'TEXT'):
    sql = f"ALTER TABLE {table} ADD COLUMN {column} {coltype};"
    conn.execute(sql)
    conn.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', help='Database URL (sqlite:///...)')
    args = parser.parse_args()

    database_url = args.db or os.getenv('DATABASE_URL') or 'sqlite:///./db.sqlite'
    try:
        db_path = sqlite_path_from_database_url(database_url)
    except Exception as e:
        print('Error parsing DATABASE_URL:', e)
        sys.exit(2)

    if not os.path.exists(db_path):
        print('Database file does not exist:', db_path)
        sys.exit(1)

    print('Opening sqlite DB at:', db_path)
    conn = sqlite3.connect(db_path)
    try:
        table = 'user'
        column = 'date_of_birth'
        if not has_column(conn, table, column):
            print(f"Column '{column}' missing on table '{table}'. Adding as TEXT (nullable)...")
            add_column(conn, table, column, 'TEXT')
            print('Migration applied: column added.')
        else:
            print(f"Column '{column}' already exists on '{table}'. No action taken.")
    except Exception as e:
        print('Migration failed:', e)
        sys.exit(3)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
