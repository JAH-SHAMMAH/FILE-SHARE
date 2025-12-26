from fastapi.testclient import TestClient
from app.main import app
from app.database import engine
from sqlmodel import Session, select
from app.models import User, Presentation, Message
from app.auth import get_password_hash, create_access_token
import uuid

client = TestClient(app)

def create_user(session, username, email):
    u = User(username=username, email=email, hashed_password=get_password_hash("pass"))
    session.add(u)
    session.commit()
    session.refresh(u)
    return u

with Session(engine) as session:
    suffix = uuid.uuid4().hex[:8]
    owner = create_user(session, f"owner_{suffix}", f"owner+{suffix}@example.test")
    viewer = create_user(session, f"viewer_{suffix}", f"viewer+{suffix}@example.test")
    # create a presentation for owner
    p = Presentation(title="Smoke Test Presentation", description="desc", filename="smoke.pdf", owner_id=owner.id)
    session.add(p)
    session.commit()
    session.refresh(p)
    owner_id = owner.id
    viewer_id = viewer.id
    pid = p.id

print('Created owner', owner_id, 'viewer', viewer_id, 'presentation', pid)

# tokens
token_viewer = create_access_token({"sub": viewer.username})

# GET presentation as viewer
res = client.get(f"/presentations/{pid}", cookies={"access_token": f"Bearer {token_viewer}"})
print('GET presentation status:', res.status_code)
if res.status_code == 200:
    txt = res.text
    has_contact = 'Contact Owner' in txt
    has_subscribe = 'Subscribe' in txt
    print('Contact visible:', has_contact, 'Subscribe visible:', has_subscribe)
else:
    print('Failed to load presentation')

# Follow via AJAX
res = client.post(f"/users/{owner.id}/follow", cookies={"access_token": f"Bearer {token_viewer}"}, headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"})
print('Follow status:', res.status_code, res.text)
try:
    data = res.json()
    print('Follow JSON:', data)
except Exception as e:
    print('No JSON follow response', e)

# GET presentation again to see Unsubscribe
res2 = client.get(f"/presentations/{pid}", cookies={"access_token": f"Bearer {token_viewer}"})
print('GET after follow status:', res2.status_code)
if res2.status_code == 200:
    txt2 = res2.text
    has_unsub = 'Unsubscribe' in txt2
    print('Unsubscribe visible after follow:', has_unsub)

# Contact owner
payload = {"name": "Viewer", "email": viewer.email, "message": "Hello owner", "owner_id": str(owner.id)}
res = client.post('/contact', data=payload, cookies={"access_token": f"Bearer {token_viewer}"}, headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"})
print('Contact post status:', res.status_code)
try:
    print('Contact JSON:', res.json())
except Exception:
    print('Contact no JSON')

# Check message persisted
with Session(engine) as session:
    m = session.exec(select(Message).where((Message.sender_id == viewer.id) & (Message.recipient_id == owner.id))).first()
    print('Message persisted:', bool(m))

# Attempt buy
res = client.post(f"/presentations/{pid}/buy", cookies={"access_token": f"Bearer {token_viewer}"})
print('Buy status:', res.status_code)
if res.is_redirect:
    print('Buy redirected to:', res.headers.get('location'))
else:
    print('Buy response:', res.text)
