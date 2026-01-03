from sqlmodel import Session, select
from app.database import engine, create_db_and_tables
from app.models import User, Presentation, AIResult
from app import tasks
from pathlib import Path
import os

create_db_and_tables()

with Session(engine) as session:
    # ensure test user
    u = session.exec(select(User).where(User.username == 'phase1_tester')).first()
    if not u:
        u = User(username='phase1_tester', email='phase1@example.com', hashed_password='x')
        session.add(u)
        session.commit()
        session.refresh(u)
    print('User:', u.id, u.username)

    # create a presentation and a small sample file
    updir = Path(os.getenv('UPLOAD_DIR', './uploads'))
    updir.mkdir(parents=True, exist_ok=True)
    sample_dir = updir / 'testfiles'
    sample_dir.mkdir(parents=True, exist_ok=True)
    sample_file = sample_dir / 'sample.txt'
    sample_file.write_text('This is a sample presentation text for AI testing.\nIt has a few lines to summarize.')

    p = session.exec(select(Presentation).where((Presentation.owner_id == u.id) & (Presentation.title == 'Phase1 Test'))).first()
    if not p:
        p = Presentation(title='Phase1 Test', description='Short description for AI.', owner_id=u.id, filename=str(sample_file.relative_to(updir)))
        session.add(p)
        session.commit()
        session.refresh(p)
    print('Presentation:', p.id, p.title, 'file=', p.filename)

print('\nInvoking AI summary worker synchronously (local test)...')
# call worker directly to produce AIResult without background queue
try:
    tasks.ai_summarize_presentation(p.id)
    print('AI summarization completed (or attempted).')
except Exception as e:
    print('AI worker error:', e)

with Session(engine) as session:
    rows = session.exec(select(AIResult).where(AIResult.presentation_id == p.id).order_by(AIResult.created_at.desc())).all()
    print('\nAI Results:')
    for r in rows:
        print(r.id, r.task_type, (r.result[:200] + '...') if r.result and len(r.result) > 200 else r.result)

print('\nSigned URL sample (not performing HTTP request here)')
try:
    from app.main import make_signed_token
    path = f"/uploads/{p.filename}"
    token = make_signed_token(path, expires=3600)
    print('Signed query:', token)
except Exception as e:
    print('Signed token generation failed:', e)

print('\nDone')
