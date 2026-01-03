import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.database import engine
from sqlmodel import Session, select
from app.models import User, Follow, Message
from app.auth import create_access_token, get_password_hash
import uuid

client = TestClient(app)


def create_user(session, username, email):
    u = User(username=username, email=email, hashed_password=get_password_hash("pass"))
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def test_follow_unfollow_and_contact():
    # create two users
    with Session(engine) as session:
        # create unique test users to avoid collisions
        suffix = uuid.uuid4().hex[:8]
        user_a = create_user(session, f"testuser_a_{suffix}", f"a+{suffix}@example.test")
        user_b = create_user(session, f"testuser_b_{suffix}", f"b+{suffix}@example.test")
        ua_name = user_a.username
        ua_id = user_a.id
        ub_id = user_b.id

    token_a = create_access_token({"sub": ua_name})

    # follow
    res = client.post(f"/users/{ub_id}/follow", cookies={"access_token": f"Bearer {token_a}"}, headers={"Accept": "application/json"})
    assert res.status_code == 200
    data = res.json()
    assert data.get("following") is True
    assert data.get("followers_count") == 1

    # DB check
    with Session(engine) as session:
        f = session.exec(select(Follow).where((Follow.follower_id == ua_id) & (Follow.following_id == ub_id))).first()
        assert f is not None

    # unfollow
    res = client.post(f"/users/{ub_id}/unfollow", cookies={"access_token": f"Bearer {token_a}"}, headers={"Accept": "application/json"})
    assert res.status_code == 200
    data = res.json()
    assert data.get("following") is False
    assert data.get("followers_count") == 0

    with Session(engine) as session:
        f = session.exec(select(Follow).where((Follow.follower_id == ua_id) & (Follow.following_id == ub_id))).first()
        assert f is None

    # contact: send a message from A to B
    payload = {"name": "Tester", "email": "a@example.test", "message": "Hello B", "owner_id": str(ub_id)}
    res = client.post("/contact", data=payload, cookies={"access_token": f"Bearer {token_a}"}, headers={"Accept": "application/json"})
    assert res.status_code == 200
    data = res.json()
    assert data.get("success") is True

    # message persisted only if following — after unfollow it should not exist
    with Session(engine) as session:
        m = session.exec(select(Message).where((Message.sender_id == ua_id) & (Message.recipient_id == ub_id))).first()
        assert m is None
        # End of test