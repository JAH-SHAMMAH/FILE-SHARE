import os
import json
from fastapi.testclient import TestClient
from sqlmodel import Session, select
import time

from app.main import app, UPLOAD_DIR, make_signed_token
from app.database import create_db_and_tables, engine
from app.models import User, Presentation
from app.auth import create_access_token, get_password_hash
from app import tasks


client = TestClient(app)


def setup_module(module):
    # ensure DB and uploads
    create_db_and_tables()
    os.makedirs(UPLOAD_DIR, exist_ok=True)


def make_auth_header(username: str):
    token = create_access_token({"sub": username})
    return {"Authorization": f"Bearer {token}"}


def test_phase1_endpoints_flow(tmp_path):
    # create users
    with Session(engine) as session:
        t = session.exec(select(User).where(User.username == 'teacher_api')).first()
        if not t:
            t = User(username='teacher_api', email='t@example.com', hashed_password=get_password_hash('x'))
            session.add(t); session.commit(); session.refresh(t)
        s = session.exec(select(User).where(User.username == 'student_api')).first()
        if not s:
            s = User(username='student_api', email='s@example.com', hashed_password=get_password_hash('x'))
            session.add(s); session.commit(); session.refresh(s)

    teacher_hdr = make_auth_header('teacher_api')
    student_hdr = make_auth_header('student_api')

    # create school
    r = client.post('/api/schools', json={'name': 'Test School'}, headers=teacher_hdr)
    assert r.status_code == 200
    school = r.json()['school']

    # create classroom
    r = client.post('/api/classrooms', json={'name': 'Test Class', 'school_id': school['id']}, headers=teacher_hdr)
    assert r.status_code == 200
    classroom = r.json()['classroom']

    # student join
    r = client.post(f"/api/classrooms/{classroom['id']}/join", json={}, headers=student_hdr)
    assert r.status_code == 200

    # teacher upload library file
    sample = tmp_path / 'sample.pdf'
    sample.write_bytes(b'%PDF-1.4 test')
    with open(sample, 'rb') as f:
        r = client.post(f"/api/classrooms/{classroom['id']}/library", files={'file': ('sample.pdf', f, 'application/pdf')}, data={'title': 'Lecture 1'}, headers=teacher_hdr)
    assert r.status_code == 200
    lib = r.json()['library_item']

    # create assignment
    r = client.post(f"/api/classrooms/{classroom['id']}/assignments", json={'title': 'HW1', 'description': 'Do stuff'}, headers=teacher_hdr)
    assert r.status_code == 200
    assignment = r.json()['assignment']

    # student submit assignment
    submit_file = tmp_path / 'answer.txt'
    submit_file.write_text('Answer')
    with open(submit_file, 'rb') as f:
        r = client.post(f"/api/assignments/{assignment['id']}/submit", files={'file': ('answer.txt', f, 'text/plain')}, headers=student_hdr)
    assert r.status_code == 200
    submission_id = r.json()['submission_id']

    # teacher list submissions
    r = client.get(f"/api/assignments/{assignment['id']}/submissions", headers=teacher_hdr)
    assert r.status_code == 200
    subs = r.json()['submissions']
    assert any(s['id'] == submission_id for s in subs)

    # teacher grade submission
    r = client.post(f"/api/submissions/{submission_id}/grade", json={'grade': 90, 'feedback': 'Good'}, headers=teacher_hdr)
    assert r.status_code == 200

    # mark attendance
    with Session(engine) as session:
        stud = session.exec(select(User).where(User.username == 'student_api')).first()
    r = client.post(f"/api/classrooms/{classroom['id']}/attendance", json={'entries':[{'user_id': stud.id, 'status':'present'}]}, headers=teacher_hdr)
    assert r.status_code == 200

    # list attendance
    r = client.get(f"/api/classrooms/{classroom['id']}/attendance", headers=teacher_hdr)
    assert r.status_code == 200

    # fetch library list
    r = client.get(f"/api/classrooms/{classroom['id']}/library", headers=teacher_hdr)
    assert r.status_code == 200

    # signed URL for presentation (owner) - find presentation id from library->presentation
    with Session(engine) as session:
        pres = session.exec(select(Presentation).where(Presentation.owner_id == t.id)).first()
        assert pres is not None
        pid = pres.id

    r = client.get(f"/api/presentations/{pid}/ai/results", headers=teacher_hdr)
    assert r.status_code == 200

    # enqueue AI summary via HTTP, then run worker synchronously
    r = client.post(f"/api/presentations/{pid}/ai/summary", headers=teacher_hdr)
    assert r.status_code == 200
    # call worker directly for test
    tasks.ai_summarize_presentation(pid)
    r = client.get(f"/api/presentations/{pid}/ai/results", headers=teacher_hdr)
    assert r.status_code == 200
    results = r.json()['results']
    assert isinstance(results, list)

    # signed download: generate token and request a byte range
    path = f"/uploads/{pres.filename}"
    token_qs = make_signed_token(path, expires=3600)
    # token_qs is URL encoded query like p=...&e=...&s=...
    url = f"/download_signed?{token_qs}"
    # request a small range
    headers = {'Range': 'bytes=0-9'}
    r = client.get(url, headers=headers)
    assert r.status_code in (200, 206)
