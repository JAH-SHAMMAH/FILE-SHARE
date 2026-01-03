from sqlmodel import Session, select
from app.models import Notification, User
from app.database import engine

with Session(engine) as s:
    rows = s.exec(select(Notification).order_by(Notification.created_at.desc()).limit(50)).all()
    if not rows:
        print('No notifications found.')
    for n in rows:
        actor_name = 'system'
        if n.actor_id:
            actor = s.get(User, n.actor_id)
            actor_name = actor.username if actor else f'user:{n.actor_id}'
        status = 'read' if n.read else 'unread'
        print(f"{n.id}\t{n.created_at}\t{status}\trecipient:{n.recipient_id}\tactor:{actor_name}\t{n.verb}\ttarget:{n.target_type}:{n.target_id}")
