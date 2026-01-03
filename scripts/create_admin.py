from sqlmodel import Session, select
from app.database import engine
from app.models import User
from app.auth import get_password_hash, create_access_token

username = 'admin'
email = 'admin@example.local'
password = 'adminpass'
with Session(engine) as session:
    stmt = select(User).where(User.username == username)
    u = session.exec(stmt).first()
    if not u:
        u = User(username=username, email=email, hashed_password=get_password_hash(password), full_name='Administrator')
        session.add(u)
        session.commit()
        session.refresh(u)
        print('Created admin user:', u.id)
    else:
        print('Admin user exists:', u.id)
    token = create_access_token({'sub': username})
    print('ACCESS_TOKEN:', token)
    print('Use this as Authorization: Bearer <token>')
