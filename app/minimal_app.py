"""
A minimal, readable FastAPI app that focuses on core flows:
- static mounting and templates
- simple login (cookie-based via existing auth helpers)
- featured page showing the signed-in user's uploads first

This is a lightweight alternative entrypoint for local development.
"""
from fastapi import FastAPI, Request, Form, status
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from types import SimpleNamespace
from pathlib import Path
from .database import engine
from .models import Presentation, User
from .auth import authenticate_user, create_access_token, create_refresh_token, get_current_user_optional

app = FastAPI(title="Slideshare - Minimal")
# mount static and templates (reuse existing folders)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent.parent / "static")), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    """If the user is signed-in, redirect to /featured. Otherwise render the public index.
    This mirrors the main app but keeps the implementation concise.
    """
    current_user = get_current_user_optional(request)
    if current_user:
        return RedirectResponse(url="/featured", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse("index.html", {"request": request, "current_user": None})


@app.get("/featured", response_class=HTMLResponse)
def featured_page(request: Request):
    current_user = get_current_user_optional(request)
    featured = []
    with Session(engine) as session:
        # If signed-in, show their recent uploads first
        if current_user:
            my_rows = session.exec(
                select(Presentation).where(Presentation.owner_id == current_user.id).order_by(Presentation.created_at.desc()).limit(6)
            ).all()
            for p in my_rows:
                owner = session.get(User, p.owner_id) if p.owner_id else None
                featured.append(SimpleNamespace(
                    id=p.id,
                    title=p.title,
                    filename=p.filename,
                    owner_id=p.owner_id,
                    owner_username=getattr(owner, 'username', None) if owner else None,
                    views=getattr(p, 'views', 0),
                ))

        # then fill with top-viewed presentations
        rows = session.exec(select(Presentation).order_by(Presentation.views.desc()).limit(12)).all()
        seen = {f.id for f in featured}
        for p in rows:
            if p.id in seen:
                continue
            owner = session.get(User, p.owner_id) if p.owner_id else None
            featured.append(SimpleNamespace(
                id=p.id,
                title=p.title,
                filename=p.filename,
                owner_id=p.owner_id,
                owner_username=getattr(owner, 'username', None) if owner else None,
                views=getattr(p, 'views', 0),
            ))
        featured = featured[:12]

    return templates.TemplateResponse("featured.html", {"request": request, "featured": featured, "current_user": current_user})


@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
def login_post(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form(None)):
    with Session(engine) as session:
        user = authenticate_user(username, password, session)
        if not user:
            return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"})
    token = create_access_token({"sub": user.username})
    refresh = create_refresh_token({"sub": user.username})
    dest = "/featured"
    if next and next.startswith("/"):
        dest = next
    response = RedirectResponse(url=dest, status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="access_token", value=f"Bearer {token}")
    response.set_cookie(key="refresh_token", value=f"Bearer {refresh}")
    return response


@app.get("/logout")
def logout():
    resp = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    resp.delete_cookie("access_token")
    resp.delete_cookie("refresh_token")
    return resp
