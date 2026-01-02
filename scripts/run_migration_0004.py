"""Run migration 0004 to add `site_role` column to the user table.

This script loads the migration file directly and calls its `upgrade(engine)`
function using the application's SQLAlchemy engine.

Run from the repository root with the project's venv active:
  & .\.venv\Scripts\Activate.ps1
  python .\scripts\run_migration_0004.py
"""
from pathlib import Path
import importlib.machinery
import importlib.util
import sys

ROOT = Path(__file__).resolve().parents[0].parent
MIGRATION = ROOT / "migrations" / "versions" / "0004_add_site_role.py"

if not MIGRATION.exists():
    print("Migration file not found:", MIGRATION)
    sys.exit(2)

try:
    # Import the application's engine
    # Ensure the package path includes the SLIDESHARE package if running from repo root
    sys.path.insert(0, str(ROOT / "SLIDESHARE"))
    from app.database import engine
except Exception as e:
    print("Failed to import app.database.engine:", e)
    sys.exit(3)

try:
    loader = importlib.machinery.SourceFileLoader("mig_0004", str(MIGRATION))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    print("Running upgrade() from", MIGRATION)
    mod.upgrade(engine)
    print("Migration 0004 applied successfully.")
except Exception as e:
    print("Migration failed:", e)
    sys.exit(1)
