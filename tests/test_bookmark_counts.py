import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.database import engine, create_db_and_tables, Session
from app.models import User, Presentation, Bookmark
from app.auth import create_access_token


def setup_module(module):
    # ensure tables exist
    create_db_and_tables()


def test_presentation_bookmarks_and_user_bookmarks():
    client = TestClient(app)
    with Session(engine) as session:
        # create a user
        u = User(username="test_user_counts", email="counts@example.com", hashed_password="x")
        session.add(u)
        session.commit()
        session.refresh(u)
        # create a presentation
        p = Presentation(title="Counts Test Presentation", filename=None, owner_id=u.id)
        session.add(p)
        session.commit()
        session.refresh(p)
        # ensure no bookmarks initially
        res = client.get(f"/api/presentations/{p.id}/bookmarks")
        assert res.status_code == 200
        data = res.json()
        assert data.get("count") == 0
        assert data.get("bookmarked") in (False, None)
        # create a bookmark
        bm = Bookmark(user_id=u.id, presentation_id=p.id)
        session.add(bm)
        session.commit()
        # unauthenticated count should be 1
        res = client.get(f"/api/presentations/{p.id}/bookmarks")
        assert res.status_code == 200
        data = res.json()
        assert data.get("count") == 1
        # authenticated: simulate cookie with Bearer token
        token = create_access_token({"sub": u.username})
        cookies = {"access_token": f"Bearer {token}"}
        res = client.get(f"/api/presentations/{p.id}/bookmarks", cookies=cookies)
        assert res.status_code == 200
        data = res.json()
        assert data.get("count") == 1
        assert data.get("bookmarked") is True
        # test /api/bookmarks returns list with this presentation id
        res = client.get("/api/bookmarks", cookies=cookies)
        assert res.status_code == 200
        bk = res.json()
        ids = bk.get("bookmarks") or []
        assert any(int(x) == p.id for x in ids)
