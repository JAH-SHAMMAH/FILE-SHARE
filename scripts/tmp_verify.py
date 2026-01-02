from pathlib import Path
import sys

sys.path.insert(0, str(Path('.').resolve() / 'SLIDESHARE'))

from app.database import engine, create_db_and_tables
from app.models import User
from sqlmodel import Session
import uuid

print('ensuring db...')
create_db_and_tables()
print('creating user...')
username = 'test_' + uuid.uuid4().hex[:6]
email = username + '@example.com'
with Session(engine) as s:
    u = User(username=username, email=email, hashed_password='x', full_name='Test', site_role='teacher')
    s.add(u)
    s.commit()
    s.refresh(u)
    print('created', u.id, u.site_role)
