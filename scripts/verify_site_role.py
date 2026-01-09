"""Quick verification script to test `site_role` persistence.

Run with the project's venv active:
  & .\.venv\Scripts\Activate.ps1
  python .\scripts\verify_site_role.py
"""

from sqlmodel import Session, select
import uuid
import sys
from pathlib import Path

# Ensure the SLIDESHARE package path is available when running from repo root
ROOT = Path(__file__).resolve().parents[0].parent
sys.path.insert(0, str(ROOT / "SLIDESHARE"))

from app.database import engine, create_db_and_tables
from app.models import User


def main():
  # Ensure DB tables and additive columns exist (best-effort)
  try:
    create_db_and_tables()
  except Exception:
    pass

  username = f"testuser_{uuid.uuid4().hex[:6]}"
  email = f"{username}@example.com"
  with Session(engine) as session:
    u = User(username=username, email=email, hashed_password="x", full_name="Test User", site_role="teacher")
    session.add(u)
    session.commit()
    session.refresh(u)
    print("Created user id=", u.id, "site_role=", u.site_role)

    # read back
    fetched = session.exec(select(User).where(User.id == u.id)).first()
    print("Fetched user id=", fetched.id, "site_role=", fetched.site_role)


if __name__ == '__main__':
  main()
