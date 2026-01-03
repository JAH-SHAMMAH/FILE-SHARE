from sqlmodel import Session, select
from app.database import engine, create_db_and_tables
from app.models import User, Presentation, Follow, Like, Notification

# Ensure tables exist
create_db_and_tables()

with Session(engine) as session:
    # create users A and B if missing
    a = session.exec(select(User).where(User.username == 'alice_test')).first()
    if not a:
        a = User(username='alice_test', email='alice_test@example.com', hashed_password='x')
        session.add(a)
        session.commit()
        session.refresh(a)
    b = session.exec(select(User).where(User.username == 'bob_test')).first()
    if not b:
        b = User(username='bob_test', email='bob_test@example.com', hashed_password='x')
        session.add(b)
        session.commit()
        session.refresh(b)

    print('Users:', a.id, a.username, '->', b.id, b.username)

    # create a presentation for bob
    p = session.exec(select(Presentation).where((Presentation.owner_id == b.id) & (Presentation.title == 'Test Deck'))).first()
    if not p:
        p = Presentation(title='Test Deck', description='Auto-test', owner_id=b.id)
        session.add(p)
        session.commit()
        session.refresh(p)
    print('Presentation id:', p.id)

    # alice follows bob (if not already)
    f = session.exec(select(Follow).where((Follow.follower_id == a.id) & (Follow.following_id == b.id))).first()
    if not f:
        f = Follow(follower_id=a.id, following_id=b.id)
        session.add(f)
        session.commit()
        # create notification for bob
        n = Notification(recipient_id=b.id, actor_id=a.id, verb='follow', target_type='user', target_id=a.id)
        session.add(n)
        session.commit()
        print('Alice followed Bob and notification created')
    else:
        print('Follow already exists')

    # alice likes bob's presentation (if not already)
    lk = session.exec(select(Like).where((Like.user_id == a.id) & (Like.presentation_id == p.id))).first()
    if not lk:
        lk = Like(user_id=a.id, presentation_id=p.id)
        session.add(lk)
        session.commit()
        # create notification for bob
        n2 = Notification(recipient_id=b.id, actor_id=a.id, verb='like', target_type='presentation', target_id=p.id)
        session.add(n2)
        session.commit()
        print('Alice liked presentation and notification created')
    else:
        print('Like already exists')

    # fetch bob's notifications
    rows = session.exec(select(Notification).where(Notification.recipient_id == b.id).order_by(Notification.created_at.desc())).all()
    print('\nNotifications for', b.username)
    for r in rows:
        print(r.id, r.actor_id, r.verb, r.target_type, r.target_id, 'read=' + str(r.read), r.created_at)

    # print resolved usernames/titles for convenience
    from app.models import Presentation as PresModel
    print('\nResolved friendly messages:')
    for r in rows:
        actor = session.get(User, r.actor_id)
        actor_name = actor.username if actor else str(r.actor_id)
        target_title = None
        if r.target_type == 'presentation' and r.target_id:
            pr = session.get(PresModel, r.target_id)
            target_title = pr.title if pr else None
        if r.verb == 'follow':
            print(f"{actor_name} followed {b.username}")
        elif r.verb == 'like':
            print(f"{actor_name} liked '{target_title or 'your presentation'}'")
        else:
            print(f"{actor_name} {r.verb}")

print('\nDone')
