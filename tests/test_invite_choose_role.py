import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
import uuid

from app.main import app
from app.database import engine
from app.models import User, Classroom, Membership
from app.auth import create_access_token

client = TestClient(app)


def make_user(username: str, email: str, password_hash: str = "x") -> User:
    # ensure username is unique by appending a short uuid
    suffix = uuid.uuid4().hex[:8]
    unique = f"{username}_{suffix}"
    # make email unique as well by inserting suffix before the @
    if "@" in email:
        local, domain = email.split("@", 1)
        unique_email = f"{local}+{suffix}@{domain}"
    else:
        unique_email = f"{email}_{suffix}"
    with Session(engine) as s:
        u = User(username=unique, email=unique_email, hashed_password=password_hash)
        s.add(u)
        s.commit()
        s.refresh(u)
        return u


def test_choose_role_persists():
    # create user and token
    u = make_user("choosetest", "choosetest@example.com")
    token = create_access_token({"sub": u.username})

    # post choose-role as authenticated user
    r = client.post("/choose-role", data={"role": "teacher"}, headers={"Authorization": f"Bearer {token}"}, follow_redirects=False)
    assert r.status_code in (302, 303, 200)

    # verify DB updated
    with Session(engine) as s:
        uu = s.get(User, u.id)
        assert uu.site_role == "teacher"


def test_invite_accept_creates_membership():
    # set up inviter (teacher) and invitee (existing user)
    inviter = make_user("inviter", "inviter@example.com")
    invitee = make_user("invitee", "invitee@example.com")

    # create classroom and membership for inviter
    with Session(engine) as s:
        c = Classroom(name="Test Class", school_id=None)
        s.add(c)
        s.commit()
        s.refresh(c)
        m = Membership(user_id=inviter.id, classroom_id=c.id, role="teacher")
        s.add(m)
        s.commit()
        classroom_id = c.id

    # build invite token locally (same algorithm as app._make_invite_token)
    import os, json, hmac, hashlib, base64
    secret = os.getenv('SECRET_KEY', 'devsecret')
    import time
    payload = {"inviter_id": inviter.id, "classroom_id": classroom_id, "email": invitee.email, "ts": int(time.time())}
    # deterministic raw excludes no fields (ts included)
    raw = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8')
    sig = hmac.new(secret.encode('utf-8'), raw, hashlib.sha256).digest()
    braw = base64.urlsafe_b64encode(raw).decode('utf-8')
    bsig = base64.urlsafe_b64encode(sig).decode('utf-8')
    token = f"{braw}.{bsig}"

    # accept the invite as the invitee
    token_header = create_access_token({"sub": invitee.username})
    r = client.get(f"/invitations/respond?token={token}&action=accept", headers={"Authorization": f"Bearer {token_header}"})
    assert r.status_code in (200, 302)

    # verify membership created
    with Session(engine) as s:
        mem = s.exec(select(Membership).where((Membership.user_id == invitee.id) & (Membership.classroom_id == c.id))).first()
        assert mem is not None
        assert mem.role == "student"
