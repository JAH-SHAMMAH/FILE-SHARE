import os, sys
from sqlmodel import Session, select

# Ensure package path for SLIDESHARE app
sys.path.insert(0, os.path.join(os.getcwd(), "SLIDESHARE"))
from app.database import engine
from app.models import Presentation

with Session(engine) as s:
    rows = s.exec(select(Presentation)).all()
    print("COUNT", len(rows))
    for p in rows[:50]:
        print(p.id, "|", (p.title or "<no title>"), "| owner_id=", p.owner_id, "| file=", p.filename)
