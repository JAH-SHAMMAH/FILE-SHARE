from sqlmodel import Session, select
from app.database import engine, create_db_and_tables
from app.models import Presentation, AIResult
from app import tasks

create_db_and_tables()
with Session(engine) as session:
    p = session.exec(select(Presentation)).first()
    if not p:
        print('No presentations found in DB')
    else:
        print('Found presentation id=', p.id, 'title=', p.title)
        print('Running synchronous AI summarization...')
        tasks.ai_summarize_presentation(p.id)
        # show latest AIResult
        res = session.exec(select(AIResult).where(AIResult.presentation_id == p.id).order_by(AIResult.created_at.desc())).first()
        if res:
            print('\nAIResult:')
            print('task_type=', res.task_type)
            print('result=')
            print(res.result[:2000])
        else:
            print('No AIResult produced')
