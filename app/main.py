import os
import shutil
import logging
import traceback
import mimetypes
from fastapi.exceptions import RequestValidationError
from fastapi import (
    FastAPI,
    Request,
    Form,
    UploadFile,
    File,
    Depends,
    HTTPException,
    status,
    Response,
    Body,
    Query,
)
from fastapi.responses import RedirectResponse, FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi import WebSocket, WebSocketDisconnect
import asyncio
from typing import Dict, List
import json
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from types import SimpleNamespace
from dotenv import load_dotenv
from urllib.parse import quote
from .database import engine, create_db_and_tables, get_session
from .models import (
    User,
    Presentation,
    Bookmark,
    Like,
    Comment,
    Follow,
    Tag,
    Category,
    PresentationTag,
    Transaction,
    WebhookEvent,
    Activity,
)
from .auth import (
    get_password_hash,
    authenticate_user,
    create_access_token,
    get_current_user,
    create_refresh_token,
    get_current_user_optional,
)
from .auth import SECRET_KEY, ALGORITHM
from .models import Message
from jose import jwt
from .payments import (
    create_order,
    capture_order,
    get_access_token,
    verify_webhook_signature,
    paystack_initialize_transaction,
    paystack_verify_transaction,
)
from .oauth import oauth
from .tasks import enqueue_conversion
from .models import ConversionJob
import uuid
from pathlib import Path
from typing import List, Optional
import textwrap
import json

load_dotenv()

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

PAYSTACK_PUBLIC_KEY = os.getenv("PAYSTACK_PUBLIC_KEY", "")
PAYSTACK_AMOUNT_KOBO = int(os.getenv("PAYSTACK_AMOUNT_KOBO", "500000"))  # default 5000.00 NGN
PAYSTACK_CURRENCY = os.getenv("PAYSTACK_CURRENCY", "NGN")

app = FastAPI()
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent.parent / "static")),
    name="static",
)
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
try:
    templates.env.filters['url_encode'] = lambda s: quote(s or '')
except Exception:
    pass


# Simple keyword-based classifier to suggest categories for presentations
def auto_classify_category(session: Session, title: str):
    if not title:
        return None
    title_low = title.lower()
    # mapping keywords to category names (small heuristic)
    mapping = {
        'business': ['business', 'strategy', 'market', 'sales', 'finance', 'startup'],
        'marketing': ['marketing', 'seo', 'content', 'campaign', 'brand'],
        'technology': ['tech', 'ai', 'machine learning', 'data', 'cloud', 'software'],
        'design': ['design', 'ui', 'ux', 'visual', 'graphics'],
        'education': ['education', 'teaching', 'curriculum', 'lecture', 'learning'],
        'career': ['career', 'resume', 'interview', 'job', 'hiring'],
        'art & photos': ['art', 'photo', 'photography', 'illustration'],
        'social media': ['social', 'instagram', 'facebook', 'twitter', 'linkedin'],
        'mobile': ['mobile', 'ios', 'android', 'app'],
        'presentations & public speaking': ['presentation', 'public speaking', 'slides', 'pitch'],
    }

    # score categories by keyword occurrence
    scores = {}
    for cat, kws in mapping.items():
        for kw in kws:
            if kw in title_low:
                scores[cat] = scores.get(cat, 0) + 1

    if not scores:
        return None
    # pick best matching category
    best = sorted(scores.items(), key=lambda x: (-x[1], x[0]))[0][0]
    # find or create Category row
    cat = session.exec(select(Category).where(func.lower(Category.name) == best)).first()
    if not cat:
        try:
            cat = Category(name=best)
            session.add(cat)
            session.commit()
            session.refresh(cat)
        except Exception:
            return None
    return cat


# Simple WebSocket connection manager for chat
class ConnectionManager:
    def __init__(self):
        # map user_id -> list of websocket
        self.active: Dict[int, List[WebSocket]] = {}
        self.lock = asyncio.Lock()

    async def connect(self, user_id: int, websocket: WebSocket):
        await websocket.accept()
        async with self.lock:
            self.active.setdefault(user_id, []).append(websocket)

    async def disconnect(self, user_id: int, websocket: WebSocket):
        async with self.lock:
            conns = self.active.get(user_id, [])
            if websocket in conns:
                conns.remove(websocket)
            if not conns:
                self.active.pop(user_id, None)

    async def send_personal(self, user_id: int, message: dict):
        conns = list(self.active.get(user_id, []))
        data = json.dumps(message)
        for ws in conns:
            try:
                await ws.send_text(data)
            except Exception:
                pass

    def is_online(self, user_id: int) -> bool:
        return user_id in self.active

    async def broadcast_presence(self, user_id: int, online: bool):
        msg = json.dumps({"type": "presence", "user_id": user_id, "online": online})
        async with self.lock:
            for uid, conns in list(self.active.items()):
                if uid == user_id:
                    continue
                for ws in list(conns):
                    try:
                        await ws.send_text(msg)
                    except Exception:
                        pass


manager = ConnectionManager()
logger = logging.getLogger("slideshare")
logging.basicConfig(level=logging.INFO)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.exception("Validation error on %s: %s", request.url, exc.errors(), exc_info=exc)
    return JSONResponse(
        {"detail": exc.errors()}, status_code=status.HTTP_422_UNPROCESSABLE_ENTITY
    )

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s", request.url, exc_info=exc)
    return PlainTextResponse("Server error", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@app.middleware("http")
async def add_current_user_to_request(request: Request, call_next):
    # Make the optional user available on request.state for templates/layouts.
    _u = get_current_user_optional(request)
    if _u:
        # copy a few safe attributes onto a lightweight object to avoid detached-instance issues
        request.state.current_user = SimpleNamespace(
            id=getattr(_u, "id", None),
            username=getattr(_u, "username", None),
            email=getattr(_u, "email", None),
            is_premium=getattr(_u, "is_premium", False),
            avatar=getattr(_u, "avatar", None),
        )
    else:
        request.state.current_user = None
    # load categories for the header/hamburger menu (simple list of names)
    try:
        with Session(engine) as session:
            cats = session.exec(select(Category).order_by(Category.name)).all()
            request.state.categories = [c.name for c in cats if getattr(c, 'name', None)]
    except Exception:
        request.state.categories = []
    response = await call_next(request)
    return response

@app.get("/favicon.ico")
def favicon():
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.on_event("startup")
def on_startup():
    create_db_and_tables()
    ensure_conversionjob_log_column()


def ensure_conversionjob_log_column():
    # SQLite-safe migration: add log column if it doesn't exist
    try:
        with engine.begin() as conn:
            cols = conn.exec_driver_sql("PRAGMA table_info(conversionjob)").fetchall()
            names = {c[1] for c in cols}
            if "log" not in names:
                conn.exec_driver_sql("ALTER TABLE conversionjob ADD COLUMN log TEXT")
    except Exception:
        # If database is missing or other engines are used, skip quietly
        pass


@app.get("/", response_class=HTMLResponse)
def index(request: Request, q: str = ""):
    current_user = get_current_user_optional(request)
    # If the user is signed in, show the Featured page as their homepage
    if current_user:
        return RedirectResponse(url="/featured", status_code=status.HTTP_302_FOUND)
    with Session(engine) as session:
        if q:
            statement = (
                select(Presentation)
                .where(
                    Presentation.title.contains(q)
                    | Presentation.description.contains(q)
                )
                .options(selectinload(Presentation.owner))
                .order_by(Presentation.created_at.desc())
            )
        else:
            statement = select(Presentation).options(selectinload(Presentation.owner)).order_by(Presentation.created_at.desc())
        presentations_raw = session.exec(statement).all()
        presentations = []
        owner_ids = {p.owner_id for p in presentations_raw if getattr(p, 'owner_id', None) is not None}
        # batch follower counts and follow-state for current user
        follower_counts = {}
        following_set = set()
        if owner_ids:
            rows = session.exec(
                select(Follow.following_id, func.count(Follow.id)).where(Follow.following_id.in_(list(owner_ids))).group_by(Follow.following_id)
            ).all()
            for r in rows:
                follower_counts[int(r[0])] = int(r[1])
            if current_user:
                follow_rows = session.exec(select(Follow).where((Follow.follower_id == current_user.id) & (Follow.following_id.in_(list(owner_ids))))).all()
                following_set = {f.following_id for f in follow_rows}

        for p in presentations_raw:
            owner = getattr(p, "owner", None)
            oid = p.owner_id
            presentations.append(
                SimpleNamespace(
                    id=p.id,
                    title=p.title,
                    description=getattr(p, "description", None),
                    filename=p.filename,
                    mimetype=p.mimetype,
                    owner_id=oid,
                    owner_username=getattr(owner, "username", None) if owner else None,
                    owner_email=getattr(owner, "email", None) if owner else None,
                    views=getattr(p, "views", None),
                    cover_url=getattr(p, "cover_url", None) if hasattr(p, "cover_url") else None,
                    created_at=getattr(p, "created_at", None),
                    followers_count=follower_counts.get(oid, 0) if oid else 0,
                    is_following=(oid in following_set) if oid else False,
                )
            )
        # attach bookmark counts for profile presentations
        pres_ids = [p.id for p in presentations if getattr(p, 'id', None)]
        if pres_ids:
            rows = session.exec(
                select(Bookmark.presentation_id, func.count(Bookmark.id))
                .where(Bookmark.presentation_id.in_(list(pres_ids)))
                .group_by(Bookmark.presentation_id)
            ).all()
            bc = {int(r[0]): int(r[1]) for r in rows}
        else:
            bc = {}
        for p in presentations:
            setattr(p, 'bookmarks_count', bc.get(getattr(p, 'id', None), 0))
        my_uploads = []
        my_upload_count = 0
        if current_user:
            my_uploads_raw = session.exec(
                select(Presentation)
                .where(Presentation.owner_id == current_user.id)
                .options(selectinload(Presentation.owner))
                .order_by(Presentation.created_at.desc())
            ).all()
            my_uploads = []
            for p in my_uploads_raw:
                owner = getattr(p, "owner", None)
                my_uploads.append(
                    SimpleNamespace(
                        id=p.id,
                        title=p.title,
                        description=getattr(p, "description", None),
                        filename=p.filename,
                        mimetype=p.mimetype,
                        owner_id=p.owner_id,
                        owner_username=getattr(owner, "username", None) if owner else None,
                        owner_email=getattr(owner, "email", None) if owner else None,
                        views=getattr(p, "views", None),
                        cover_url=getattr(p, "cover_url", None) if hasattr(p, "cover_url") else None,
                        created_at=getattr(p, "created_at", None),
                    )
                )
            my_upload_count = len(my_uploads_raw)
        # If DB had no presentations, fall back to listing files in UPLOAD_DIR
        if not presentations:
            try:
                uploads_path = Path(UPLOAD_DIR)
                files = sorted(uploads_path.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
                presentations = []
                for i, f in enumerate(files):
                    if not f.is_file():
                        continue
                    presentations.append(SimpleNamespace(
                        id=None,
                        title=f.stem,
                        filename=f.name,
                        owner_id=None,
                        owner_username=None,
                        mimetype=mimetypes.guess_type(str(f))[0] or "application/octet-stream",
                        views=None,
                        cover_url=None,
                    ))
            except Exception:
                # ignore filesystem errors and keep presentations as empty list
                presentations = []
        # compute top 3 most viewed
        most_viewed_raw = session.exec(select(Presentation).order_by(Presentation.views.desc()).limit(3)).all()
        most_viewed = []
        for p in most_viewed_raw:
            owner = session.get(User, p.owner_id) if p.owner_id else None
            most_viewed.append(SimpleNamespace(id=p.id, title=p.title, owner_username=getattr(owner, 'username', None) if owner else None, views=p.views, filename=p.filename))

        # compute featured list for public homepage (top viewed, limit 12)
        featured = []
        featured_rows = session.exec(select(Presentation).order_by(Presentation.views.desc()).limit(12)).all()
        for p in featured_rows:
            owner = session.get(User, p.owner_id) if p.owner_id else None
            featured.append(SimpleNamespace(
                id=p.id,
                title=p.title,
                filename=p.filename,
                owner_id=p.owner_id,
                owner_username=getattr(owner, 'username', None) if owner else None,
                owner_email=getattr(owner, 'email', None) if owner else None,
                views=getattr(p, 'views', 0),
            ))
        # attach bookmark counts for any listing we will render (batch query to avoid N+1)
        all_ids = set()
        for item in presentations:
            if getattr(item, 'id', None):
                all_ids.add(item.id)
        for item in most_viewed:
            if getattr(item, 'id', None):
                all_ids.add(item.id)
        for item in featured:
            if getattr(item, 'id', None):
                all_ids.add(item.id)
        for item in my_uploads:
            if getattr(item, 'id', None):
                all_ids.add(item.id)

        if all_ids:
            rows = session.exec(
                select(Bookmark.presentation_id, func.count(Bookmark.id))
                .where(Bookmark.presentation_id.in_(list(all_ids)))
                .group_by(Bookmark.presentation_id)
            ).all()
            bookmark_counts = {int(r[0]): int(r[1]) for r in rows}
        else:
            bookmark_counts = {}

        # set a default count of 0 where missing
        for item in presentations:
            setattr(item, 'bookmarks_count', bookmark_counts.get(getattr(item, 'id', None), 0))
        for item in most_viewed:
            setattr(item, 'bookmarks_count', bookmark_counts.get(getattr(item, 'id', None), 0))
        for item in featured:
            setattr(item, 'bookmarks_count', bookmark_counts.get(getattr(item, 'id', None), 0))
        for item in my_uploads:
            setattr(item, 'bookmarks_count', bookmark_counts.get(getattr(item, 'id', None), 0))

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "presentations": presentations,
            "current_user": current_user,
            "my_uploads": my_uploads,
            "most_viewed": most_viewed,
            "my_upload_count": my_upload_count,
        },
    )


@app.get('/api/categories', response_class=JSONResponse)
def api_categories(request: Request):
    """Return a JSON array of category names for client-side lazy loading."""
    try:
        with Session(engine) as session:
            cats = session.exec(select(Category).order_by(Category.name)).all()
            names = [c.name for c in cats if getattr(c, 'name', None)]
            return JSONResponse(names)
    except Exception:
        return JSONResponse([], status_code=200)


@app.get("/featured", response_class=HTMLResponse, name="featured")
def featured_page(request: Request):
    current_user = get_current_user_optional(request)
    with Session(engine) as session:
        # Prefer showing the signed-in user's uploads first, then top presentations by views
        featured = []
        if current_user:
            my_rows = session.exec(
                select(Presentation)
                .where(Presentation.owner_id == current_user.id)
                .order_by(Presentation.created_at.desc())
                .limit(6)
            ).all()
            for p in my_rows:
                owner = session.get(User, p.owner_id) if p.owner_id else None
                featured.append(SimpleNamespace(
                    id=p.id,
                    title=p.title,
                    filename=p.filename,
                    owner_id=p.owner_id,
                    owner_username=getattr(owner, 'username', None) if owner else None,
                    owner_email=getattr(owner, 'email', None) if owner else None,
                    views=getattr(p, 'views', 0),
                ))

        # fill remaining slots with top presentations (avoid duplicates)
        rows = session.exec(select(Presentation).order_by(Presentation.views.desc()).limit(12)).all()
        seen_ids = {f.id for f in featured}
        for p in rows:
            if p.id in seen_ids:
                continue
            owner = session.get(User, p.owner_id) if p.owner_id else None
            featured.append(SimpleNamespace(
                id=p.id,
                title=p.title,
                filename=p.filename,
                owner_id=p.owner_id,
                owner_username=getattr(owner, 'username', None) if owner else None,
                owner_email=getattr(owner, 'email', None) if owner else None,
                views=getattr(p, 'views', 0),
            ))
        featured = featured[:12]
        # attach bookmark counts for featured items
        feat_ids = [f.id for f in featured if getattr(f, 'id', None)]
        if feat_ids:
            rows = session.exec(
                select(Bookmark.presentation_id, func.count(Bookmark.id))
                .where(Bookmark.presentation_id.in_(list(feat_ids)))
                .group_by(Bookmark.presentation_id)
            ).all()
            bc = {int(r[0]): int(r[1]) for r in rows}
        else:
            bc = {}
        for f in featured:
            setattr(f, 'bookmarks_count', bc.get(getattr(f, 'id', None), 0))

    return templates.TemplateResponse("featured.html", {"request": request, "featured": featured, "current_user": current_user})


@app.get("/register", response_class=HTMLResponse, name="register")
def register_get(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@app.get("/uploads")
def uploads_redirect():
    # legacy or mistyped route: redirect to /featured
    return RedirectResponse(url="/featured", status_code=status.HTTP_302_FOUND)


@app.post("/register")
def register_post(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
):
    with Session(engine) as session:
        user_exists = session.exec(
            select(User).where((User.username == username) | (User.email == email))
        ).first()
        if user_exists:
            return templates.TemplateResponse(
                "register.html", {"request": request, "error": "User already exists"}
            )
        user = User(
            username=username, email=email, hashed_password=get_password_hash(password)
        )
        session.add(user)
        session.commit()
        session.refresh(user)
    token = create_access_token({"sub": user.username})
    refresh = create_refresh_token({"sub": user.username})
    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    cookie_secure = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    cookie_samesite = os.getenv("COOKIE_SAMESITE", "lax")
    response.set_cookie(
        key="access_token",
        value=f"Bearer {token}",
        httponly=True,
        secure=cookie_secure,
        samesite=cookie_samesite,
    )
    response.set_cookie(
        key="refresh_token",
        value=f"Bearer {refresh}",
        httponly=True,
        secure=cookie_secure,
        samesite=cookie_samesite,
    )
    return response


@app.get("/login", response_class=HTMLResponse, name="login")
def login_get(request: Request):
    # Surface any error passed via query params (e.g., missing OAuth config)
    err = request.query_params.get("error")
    return templates.TemplateResponse("login.html", {"request": request, "error": err})


@app.post("/login")
def login_post(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form(None)):
    with Session(engine) as session:
        user = authenticate_user(username, password, session)
        if not user:
            return templates.TemplateResponse(
                "login.html", {"request": request, "error": "Invalid credentials"}
            )
    token = create_access_token({"sub": user.username})
    refresh = create_refresh_token({"sub": user.username})
    # Prefer an explicit `next` target when provided; fallback to `/featured` so users land on the featured page
    dest = "/featured"
    if next and next.startswith("/"):
        dest = next
    response = RedirectResponse(url=dest, status_code=status.HTTP_302_FOUND)
    cookie_secure = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    cookie_samesite = os.getenv("COOKIE_SAMESITE", "lax")
    response.set_cookie(
        key="access_token",
        value=f"Bearer {token}",
        httponly=True,
        secure=cookie_secure,
        samesite=cookie_samesite,
    )
    response.set_cookie(
        key="refresh_token",
        value=f"Bearer {refresh}",
        httponly=True,
        secure=cookie_secure,
        samesite=cookie_samesite,
    )
    return response


@app.get('/about', response_class=HTMLResponse, name='about')
def about_get(request: Request):
    return templates.TemplateResponse('about.html', {'request': request})


@app.get('/help', response_class=HTMLResponse, name='help')
def help_get(request: Request):
    return templates.TemplateResponse('help.html', {'request': request})


@app.get('/contact', response_class=HTMLResponse, name='contact')
def contact_get(request: Request):
    return templates.TemplateResponse('contact.html', {'request': request})


@app.post('/contact')
def contact_post(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    message: str = Form(...),
    owner_id: Optional[int] = Form(None),
    owner_username: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user_optional),
):
    # If this POST targets a specific owner and the sender is signed-in, persist a Message
    recipient = None
    try:
        with Session(engine) as session:
            if owner_id:
                recipient = session.get(User, owner_id)
            elif owner_username:
                recipient = session.exec(select(User).where(User.username == owner_username)).first()
            # If recipient exists and sender is signed-in and not messaging themselves, create Message
            if recipient and current_user and getattr(current_user, 'id', None) != recipient.id:
                msg = Message(sender_id=current_user.id, recipient_id=recipient.id, content=message)
                session.add(msg)
                session.commit()
                session.refresh(msg)
                # notify recipient if online
                try:
                    out = {
                        'type': 'message',
                        'id': msg.id,
                        'from': current_user.id,
                        'to': recipient.id,
                        'content': message,
                        'created_at': msg.created_at.isoformat(),
                    }
                    # schedule send_personal without awaiting to avoid blocking
                    import asyncio

                    asyncio.create_task(manager.send_personal(recipient.id, out))
                except Exception:
                    pass

    except Exception:
        # ignore persistence errors for contact form; fall through to acknowledgement
        pass

    # If this was an AJAX/JSON request, return JSON (clean for client JS). Otherwise render template
    accept = request.headers.get('accept', '')
    xreq = request.headers.get('x-requested-with', '')
    wants_json = ('application/json' in accept) or (xreq == 'XMLHttpRequest')
    if wants_json:
        return JSONResponse({'success': True})

    return templates.TemplateResponse('contact.html', {'request': request, 'success': 'Thanks — we received your message.'})


@app.post('/api/register')
def api_register(payload: dict = Body(...)):
    username = payload.get('username')
    email = payload.get('email')
    password = payload.get('password')
    if not username or not email or not password:
        return JSONResponse({'error': 'username, email and password required'}, status_code=400)
    with Session(engine) as session:
        exists = session.exec(select(User).where((User.username == username) | (User.email == email))).first()
        if exists:
            return JSONResponse({'error': 'user exists'}, status_code=400)
        user = User(username=username, email=email, hashed_password=get_password_hash(password))
        session.add(user)
        session.commit()
        session.refresh(user)
    token = create_access_token({'sub': user.username})
    return {'access_token': token, 'token_type': 'bearer'}


@app.post('/api/login')
def api_login(payload: dict = Body(...)):
    username = payload.get('username')
    password = payload.get('password')
    if not username or not password:
        return JSONResponse({'error': 'username and password required'}, status_code=400)
    with Session(engine) as session:
        user = authenticate_user(username, password, session)
        if not user:
            return JSONResponse({'error': 'invalid credentials'}, status_code=401)
    token = create_access_token({'sub': user.username})
    return {'access_token': token, 'token_type': 'bearer'}


@app.get("/logout")
def logout():
    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    return response


@app.get("/token/refresh")
def refresh_token(request: Request):
    cookie = request.cookies.get("refresh_token")
    if not cookie:
        raise HTTPException(status_code=401, detail="Missing refresh token")
    token = cookie.split(" ", 1)[-1] if cookie.startswith("Bearer ") else cookie
    try:
        payload = jwt.decode(
            token,
            os.getenv("JWT_SECRET", "changeme_super_secret"),
            algorithms=["HS256"],
        )
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Invalid token")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
    new_access = create_access_token({"sub": username})
    cookie_secure = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    cookie_samesite = os.getenv("COOKIE_SAMESITE", "lax")
    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key="access_token",
        value=f"Bearer {new_access}",
        httponly=True,
        secure=cookie_secure,
        samesite=cookie_samesite,
    )
    return response


@app.get("/upload", response_class=HTMLResponse, name="upload")
def upload_get(
    request: Request,
    page: int = Query(1),
    per_page: int = Query(12),
    category: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    sort: str = Query("newest"),
    current_user: User = Depends(get_current_user_optional),
):
    # Redirect unauthenticated users to login and preserve desired target
    if not current_user:
        return RedirectResponse(url="/login?next=/upload", status_code=status.HTTP_302_FOUND)

    with Session(engine) as session:
        categories = session.exec(select(Category)).all()

        # Build base selection scoped to the current user's uploads
        stmt = select(Presentation).where(Presentation.owner_id == current_user.id)

        # Filter by category name if provided
        if category:
            cat = session.exec(select(Category).where(Category.name == category)).first()
            if cat:
                stmt = stmt.where(Presentation.category_id == cat.id)

        # Text search in title/description
        if q:
            stmt = stmt.where(
                (Presentation.title.contains(q)) | (Presentation.description.contains(q))
            )

        # Sorting
        if sort == "oldest":
            stmt = stmt.order_by(Presentation.created_at.asc())
        else:
            stmt = stmt.order_by(Presentation.created_at.desc())

        # Execute and paginate in-Python (acceptable for modest user counts)
        all_matches = session.exec(stmt).all()
        total = len(all_matches)
        start = (max(page, 1) - 1) * per_page
        end = start + per_page
        page_rows = all_matches[start:end]
        uploads = []
        for p in page_rows:
            owner = getattr(p, 'owner', None)
            uploads.append(SimpleNamespace(
                id=p.id,
                title=p.title,
                description=getattr(p, 'description', None),
                filename=p.filename,
                mimetype=p.mimetype,
                owner_id=p.owner_id,
                owner_username=getattr(owner, 'username', None) if owner else None,
                owner_email=getattr(owner, 'email', None) if owner else None,
                views=getattr(p, 'views', None),
                cover_url=getattr(p, 'cover_url', None) if hasattr(p, 'cover_url') else None,
                created_at=getattr(p, 'created_at', None),
            ))
        # attach bookmark counts for uploads on this page
        up_ids = [u.id for u in uploads if getattr(u, 'id', None)]
        if up_ids:
            rows_bc = session.exec(
                select(Bookmark.presentation_id, func.count(Bookmark.id))
                .where(Bookmark.presentation_id.in_(list(up_ids)))
                .group_by(Bookmark.presentation_id)
            ).all()
            bc = {int(r[0]): int(r[1]) for r in rows_bc}
        else:
            bc = {}
        for u in uploads:
            setattr(u, 'bookmarks_count', bc.get(getattr(u, 'id', None), 0))

    return templates.TemplateResponse(
        "upload.html",
        {
            "request": request,
            "categories": categories,
            "current_user": current_user,
            "uploads": uploads,
            "total_uploads": total,
            "page": page,
            "per_page": per_page,
            "category": category,
            "q": q,
            "sort": sort,
            "bare": True,
        },
    )


@app.post("/upload")
async def upload_post(
    request: Request,
    title: str = Form(None),
    description: str = Form(None),
    file: UploadFile = File(None),
    tags: str = Form(None),
    category: str = Form(None),
    current_user: User = Depends(get_current_user),
):
    def render_error(msg: str):
        with Session(engine) as session:
            categories = session.exec(select(Category)).all()
        return templates.TemplateResponse(
            "upload.html",
            {
                "request": request,
                "error": msg,
                "categories": categories,
            },
        )

    if not file or not getattr(file, "filename", None):
        return render_error("Please choose a file to upload.")

    title_clean = (title or "").strip()
    if not title_clean:
        title_clean = Path(file.filename).stem

    # Ensure description is a string
    desc_clean = (description or "").strip()

    try:
        # Allowed extensions for presentations
        allowed_exts = {".pdf", ".ppt", ".pptx", ".pptm"}
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in allowed_exts:
            return render_error("Unsupported file type")

        unique_name = f"{uuid.uuid4().hex}{file_ext}"
        save_path = Path(UPLOAD_DIR) / unique_name
        # Stream upload with size limit
        max_mb = int(os.getenv("UPLOAD_MAX_MB", "50"))
        max_bytes = max_mb * 1024 * 1024
        size = 0
        with save_path.open("wb") as buffer:
            while True:
                chunk = await file.read(1024 * 64)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    buffer.close()
                    try:
                        save_path.unlink()
                    except Exception:
                        pass
                    with Session(engine) as session:
                        categories = session.exec(select(Category)).all()
                    return templates.TemplateResponse(
                        "upload.html",
                        {
                            "request": request,
                            "error": f"File exceeds maximum size of {max_mb} MB",
                            "categories": categories,
                        },
                    )
                buffer.write(chunk)

        p = Presentation(
            title=title_clean,
            description=desc_clean,
            filename=unique_name,
            mimetype=file.content_type or "application/octet-stream",
            owner_id=current_user.id,
        )
        with Session(engine) as session:
            # handle category (auto-classify when missing)
            if category:
                cat_name = category.strip()
                cat = session.exec(
                    select(Category).where(Category.name == cat_name)
                ).first()
                if not cat:
                    cat = Category(name=cat_name)
                    session.add(cat)
                    session.commit()
                    session.refresh(cat)
                p.category_id = cat.id
            else:
                # try to auto-classify from title
                try:
                    auto_cat = auto_classify_category(session, title_clean)
                    if auto_cat:
                        p.category_id = auto_cat.id
                except Exception:
                    pass

            session.add(p)
            session.commit()
            session.refresh(p)

            # handle tags (comma-separated)
            if tags:
                tag_names = [t.strip() for t in tags.split(",") if t.strip()]
                for tn in tag_names:
                    tag = session.exec(select(Tag).where(Tag.name == tn)).first()
                    if not tag:
                        tag = Tag(name=tn)
                        session.add(tag)
                        session.commit()
                        session.refresh(tag)
                    link = PresentationTag(presentation_id=p.id, tag_id=tag.id)
                    session.add(link)
                session.commit()

            # optional conversion: enqueue background job using RQ
            enable_conv = os.getenv("ENABLE_CONVERSION", "false").lower() == "true"
            if enable_conv and file_ext in {".ppt", ".pptx", ".pptm"}:
                try:
                    from .tasks import enqueue_conversion

                    enqueue_conversion(p.id, unique_name)
                except Exception:
                    pass

            # record activity
            try:
                act = Activity(
                    user_id=current_user.id, verb="uploaded_presentation", target_id=p.id
                )
                session.add(act)
                session.commit()
            except Exception:
                pass
            presentation_id = p.id

        return RedirectResponse(
            url=f"/presentations/{presentation_id}", status_code=status.HTTP_302_FOUND
        )
    except Exception:
        logger.exception("Upload failed")
        return render_error("Upload failed. Please try again.")


@app.post("/api/uploads")
async def api_upload(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(None),
    description: str = Form(None),
    tags: str = Form(None),
    category: str = Form(None),
):
    """API endpoint to upload a presentation and store metadata."""
    current_user = get_current_user_optional(request)

    allowed_exts = {".pdf", ".ppt", ".pptx", ".pptm"}
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in allowed_exts:
        return JSONResponse({"error": "Unsupported file type"}, status_code=400)

    max_mb = int(os.getenv("UPLOAD_MAX_MB", "50"))
    max_bytes = max_mb * 1024 * 1024
    size = 0
    unique_name = f"{uuid.uuid4().hex}{file_ext}"
    save_path = Path(UPLOAD_DIR) / unique_name

    with save_path.open("wb") as buffer:
        while True:
            chunk = await file.read(1024 * 64)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                buffer.close()
                try:
                    save_path.unlink()
                except Exception:
                    pass
                return JSONResponse(
                    {"error": f"File exceeds maximum size of {max_mb} MB"},
                    status_code=400,
                )
            buffer.write(chunk)

    with Session(engine) as session:
        p = Presentation(
            title=title or Path(file.filename).stem,
            description=description,
            filename=unique_name,
            mimetype=file.content_type,
            owner_id=current_user.id if current_user else None,
        )

        # optional category
        if category:
            cat_name = category.strip()
            cat = session.exec(select(Category).where(Category.name == cat_name)).first()
            if not cat:
                cat = Category(name=cat_name)
                session.add(cat)
                session.commit()
                session.refresh(cat)
            p.category_id = cat.id

        session.add(p)
        session.commit()
        session.refresh(p)
        # capture owner primitive values now to avoid detached-instance access later
        owner_username = owner.username if owner else None
        owner_email = owner.email if owner else None

        # optional tags
        if tags:
            tag_names = [t.strip() for t in tags.split(",") if t.strip()]
            for tn in tag_names:
                tag = session.exec(select(Tag).where(Tag.name == tn)).first()
                if not tag:
                    tag = Tag(name=tn)
                    session.add(tag)
                    session.commit()
                    session.refresh(tag)
                link = PresentationTag(presentation_id=p.id, tag_id=tag.id)
                session.add(link)
            session.commit()

    return {
        "id": p.id,
        "title": p.title,
        "download_url": f"/download/{p.filename}",
        "view_url": f"/presentations/{p.id}",
    }


def _ai_transform_text(content: str, mode: str) -> str:
    # Deterministic lightweight transforms; not a true AI model.
    if not content:
        return ""
    sentences = [s.strip() for s in content.replace("\n", " ").split(".") if s.strip()]
    if not sentences:
        sentences = [content]

    def clip_sentence(s: str, words: int = 14):
        parts = s.split()
        return " ".join(parts[:words]) + ("…" if len(parts) > words else "")

    if mode == "simplify":
        simplified = [clip_sentence(s, 12) for s in sentences]
        return " ".join(simplified)
    if mode == "rephrase":
        rephrased = [f"In short, {clip_sentence(s, 16)}" for s in sentences]
        return " ".join(rephrased)
    # default rewrite: merge and slightly shorten
    merged = " ".join(sentences)
    return clip_sentence(merged, 22)


@app.post("/api/ai/rewrite")
def ai_rewrite(
    payload: dict = Body(...), current_user: User = Depends(get_current_user)
):
    if not current_user.is_premium:
        raise HTTPException(status_code=403, detail="Premium feature only")

    content = (payload.get("content") or "").strip()
    mode = (payload.get("mode") or "rewrite").lower()
    if mode not in {"rewrite", "rephrase", "simplify"}:
        mode = "rewrite"
    result = _ai_transform_text(content, mode)
    return {"result": result, "mode": mode}


@app.get("/presentations/{presentation_id}", response_class=HTMLResponse)
def view_presentation(request: Request, presentation_id: int):
    owner_username = None
    owner_email = None
    with Session(engine) as session:
        p = session.get(Presentation, presentation_id)
        if not p:
            raise HTTPException(status_code=404, detail="Not found")
        # Resolve owner safely and increment views on the persisted model
        owner = session.get(User, p.owner_id) if p.owner_id else None
        p.views = (p.views or 0) + 1
        session.add(p)
        session.commit()
        session.refresh(p)
        comments = session.exec(
            select(Comment).where(Comment.presentation_id == presentation_id)
        ).all()
        comment_users = {}
        if comments:
            user_ids = {c.user_id for c in comments if c.user_id is not None}
            if user_ids:
                users = session.exec(select(User).where(User.id.in_(user_ids))).all()
                comment_users = {
                    u.id: (u.full_name or u.username or f"User {u.id}") for u in users
                }

        likes = session.exec(
            select(Like).where(Like.presentation_id == presentation_id)
        ).all()

        # Decide what to show inline: converted PDF (if exists) or original PDF; other types auto-enqueue conversion and remain download-only until ready.
        viewer_url = None
        conversion_status = None
        if p.filename:
            ext = Path(p.filename).suffix.lower()
            original_url = f"/download/{p.filename}"
            job = session.exec(
                select(ConversionJob)
                .where(ConversionJob.presentation_id == presentation_id)
                .order_by(ConversionJob.created_at.desc())
            ).first()

            if ext == ".pdf":
                viewer_url = f"/download/{p.filename}?inline=1"
                conversion_status = "ready"
            elif ext in {".ppt", ".pptx", ".pptm"}:
                if job and job.status == "finished" and job.result:
                    conv_path = Path(UPLOAD_DIR) / job.result
                    if conv_path.exists():
                        viewer_url = f"/presentations/{presentation_id}/converted_pdf?inline=1"
                        conversion_status = "ready"
                if not viewer_url:
                    conversion_status = job.status if job else "queued"
                    if not job:
                        try:
                            enqueue_conversion(p.id, p.filename)
                            conversion_status = "queued"
                        except Exception:
                            conversion_status = "failed"
            else:
                conversion_status = "unsupported"
        else:
            original_url = None
        # build a lightweight presentation object for templates (avoid setting attrs on SQLModel)
        if owner:
            owner_username = getattr(owner, 'username', None)
            owner_email = getattr(owner, 'email', None)
    p_safe = SimpleNamespace(
        id=p.id,
        title=p.title,
        description=getattr(p, 'description', None),
        filename=p.filename,
        mimetype=p.mimetype,
        owner_id=p.owner_id,
        owner_username=owner_username,
        owner_email=owner_email,
        views=p.views,
        cover_url=getattr(p, 'cover_url', None) if hasattr(p, 'cover_url') else None,
        created_at=getattr(p, 'created_at', None),
    )

    # determine follow status for current user (subscribe)
    cu = getattr(request.state, 'current_user', None)
    is_following = False
    followers_count = 0
    if p.owner_id is not None:
        with Session(engine) as session:
            followers = session.exec(select(Follow).where(Follow.following_id == p.owner_id)).all()
            followers_count = len(followers)
            if cu and cu.id:
                exists = session.exec(
                    select(Follow).where((Follow.follower_id == cu.id) & (Follow.following_id == p.owner_id))
                ).first()
                is_following = bool(exists)
                # bookmarks for this presentation
                bookmarks = session.exec(select(Bookmark).where(Bookmark.presentation_id == presentation_id)).all()
                bookmarks_count = len(bookmarks)
                is_bookmarked = False
                if cu and cu.id:
                    b_exists = session.exec(select(Bookmark).where((Bookmark.presentation_id == presentation_id) & (Bookmark.user_id == cu.id))).first()
                    is_bookmarked = bool(b_exists)

    return templates.TemplateResponse(
        "presentation.html",
        {
            "request": request,
            "p": p_safe,
            "comments": comments,
            "comment_users": comment_users,
            "likes": len(likes),
            "viewer_url": viewer_url,
            "conversion_status": conversion_status,
            "original_url": original_url,
            "is_following": is_following,
            "followers_count": followers_count,
                "bookmarks_count": bookmarks_count,
                "is_bookmarked": is_bookmarked,
        },
    )


@app.get("/api/presentations/{presentation_id}/preview")
def presentation_preview(presentation_id: int):
    with Session(engine) as session:
        p = session.get(Presentation, presentation_id)
        if not p:
            raise HTTPException(status_code=404, detail="Not found")

        viewer_url = None
        original_url = None
        conversion_status = None

        if p.filename:
            ext = Path(p.filename).suffix.lower()
            original_url = f"/download/{p.filename}"
            job = session.exec(
                select(ConversionJob)
                .where(ConversionJob.presentation_id == presentation_id)
                .order_by(ConversionJob.created_at.desc())
            ).first()

            if ext == ".pdf":
                viewer_url = f"/download/{p.filename}?inline=1"
                conversion_status = "ready"
            elif ext in {".ppt", ".pptx", ".pptm"}:
                if job and job.status == "finished" and job.result:
                    conv_path = Path(UPLOAD_DIR) / job.result
                    if conv_path.exists():
                        viewer_url = f"/presentations/{presentation_id}/converted_pdf?inline=1"
                        conversion_status = "ready"
                if not viewer_url:
                    conversion_status = job.status if job else "queued"
            else:
                conversion_status = "unsupported"

    return {
        "id": p.id,
        "title": p.title,
        "viewer_url": viewer_url,
        "original_url": original_url,
        "conversion_status": conversion_status,
    }


@app.get("/presentations/{presentation_id}/conversion_status")
def conversion_status(presentation_id: int):
    with Session(engine) as session:
        job = session.exec(
            select(ConversionJob)
            .where(ConversionJob.presentation_id == presentation_id)
            .order_by(ConversionJob.created_at.desc())
        ).first()
        if not job:
            return {"status": "none", "job_id": None, "result": None}
        return {"status": job.status, "job_id": job.job_id, "result": job.result}


@app.get("/presentations/{presentation_id}/conversion_logs")
def conversion_logs(presentation_id: int):
    with Session(engine) as session:
        job = session.exec(
            select(ConversionJob)
            .where(ConversionJob.presentation_id == presentation_id)
            .order_by(ConversionJob.created_at.desc())
        ).first()
        if not job:
            return {"log": None}
        return {"log": job.log}


@app.get("/presentations/{presentation_id}/thumbnails")
def list_thumbnails(presentation_id: int):
    thumbs_dir = Path(UPLOAD_DIR) / "thumbs" / str(presentation_id)
    if not thumbs_dir.exists():
        # attempt to enqueue generation of thumbnails when possible
        try:
            from .tasks import enqueue_conversion
            with Session(engine) as session:
                p = session.get(Presentation, presentation_id)
                if p and getattr(p, 'filename', None):
                    src = Path(UPLOAD_DIR) / p.filename
                    if src.exists():
                        try:
                            enqueue_conversion(presentation_id, p.filename)
                            return {"thumbnails": [], "status": "queued"}
                        except Exception:
                            # fall back to synchronous conversion if queueing fails
                            try:
                                from .tasks import convert_presentation
                                convert_presentation(presentation_id, p.filename)
                                # after sync conversion, proceed to collect thumbnails below
                            except Exception:
                                return {"thumbnails": [], "status": "queued"}
        except Exception:
            # if RQ/Redis isn't available, try synchronous generation
            try:
                from .tasks import convert_presentation
                with Session(engine) as session:
                    p = session.get(Presentation, presentation_id)
                    if p and getattr(p, 'filename', None):
                        src = Path(UPLOAD_DIR) / p.filename
                        if src.exists():
                            convert_presentation(presentation_id, p.filename)
                            # after conversion, proceed to collect thumbnails below
            except Exception:
                pass
        return {"thumbnails": []}

    files = sorted(thumbs_dir.glob("slide_*.png"))
    # return URLs relative to server
    urls = [f"/presentations/{presentation_id}/slide/{i}" for i in range(len(files))]
    return {"thumbnails": urls}


@app.get("/presentations/{presentation_id}/slide/{index}")
def get_slide_image(presentation_id: int, index: int):
    thumbs_dir = Path(UPLOAD_DIR) / "thumbs" / str(presentation_id)
    path = thumbs_dir / f"slide_{index}.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Slide not found")
    return FileResponse(path, media_type="image/png", filename=path.name)


@app.get("/presentations/{presentation_id}/converted_pdf")
def get_converted_pdf(presentation_id: int, inline: bool = Query(False)):
    with Session(engine) as session:
        job = session.exec(
            select(ConversionJob)
            .where(ConversionJob.presentation_id == presentation_id)
            .order_by(ConversionJob.created_at.desc())
        ).first()
        if not job or not job.result:
            raise HTTPException(status_code=404, detail="Converted PDF not found")
        pdf_path = Path(UPLOAD_DIR) / job.result
        if not pdf_path.exists():
            raise HTTPException(status_code=404, detail="Converted PDF missing on disk")

    if inline:
        return FileResponse(
            pdf_path,
            media_type="application/pdf",
            headers={"Content-Disposition": f"inline; filename=\"{pdf_path.name}\""},
        )

    return FileResponse(
        pdf_path, media_type="application/pdf", filename=pdf_path.name
    )


@app.post("/presentations/{presentation_id}/enqueue_conversion")
def manual_enqueue_conversion(
    presentation_id: int, current_user: User = Depends(get_current_user)
):
    # allow only owner or premium users to request conversion
    with Session(engine) as session:
        p = session.get(Presentation, presentation_id)
        if not p:
            raise HTTPException(status_code=404, detail="Presentation not found")
        if p.owner_id != current_user.id and not current_user.is_premium:
            raise HTTPException(status_code=403, detail="Not authorized to convert")
    # enqueue using the original filename
    job_id = enqueue_conversion(presentation_id, p.filename)
    return {"enqueued": True, "job_id": job_id}


@app.get("/download/{filename}")
def download_file(filename: str, inline: bool = Query(False)):
    path = Path(UPLOAD_DIR) / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")

    guessed_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    # For inline view, use the real media type and explicit inline disposition.
    if inline:
        return FileResponse(
            path,
            media_type=guessed_type,
            headers={"Content-Disposition": f"inline; filename=\"{filename}\""},
        )

    # Force downloads across browsers (including iOS Safari) by always using
    # attachment disposition and a generic binary media type.
    return FileResponse(
        path,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename=\"{filename}\""},
    )


@app.post("/presentations/{presentation_id}/comment")
def post_comment(
    presentation_id: int,
    content: str = Form(...),
    current_user: User = Depends(get_current_user),
):
    c = Comment(
        user_id=current_user.id, presentation_id=presentation_id, content=content
    )
    with Session(engine) as session:
        session.add(c)
        session.commit()
        try:
            act = Activity(
                user_id=current_user.id, verb="commented", target_id=presentation_id
            )
            session.add(act)
            session.commit()
        except Exception:
            pass
    return RedirectResponse(
        url=f"/presentations/{presentation_id}", status_code=status.HTTP_302_FOUND
    )


@app.post("/presentations/{presentation_id}/like")
def post_like(presentation_id: int, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        existing = session.exec(
            select(Like).where(
                (Like.user_id == current_user.id)
                & (Like.presentation_id == presentation_id)
            )
        ).first()
        if existing:
            session.delete(existing)
            session.commit()
            return {"liked": False}
        l = Like(user_id=current_user.id, presentation_id=presentation_id)
        session.add(l)
        session.commit()
        try:
            act = Activity(
                user_id=current_user.id, verb="liked", target_id=presentation_id
            )
            session.add(act)
            session.commit()
        except Exception:
            pass
    return {"liked": True}


@app.post("/follow/{username}")
def follow_user(username: str, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        target = session.exec(select(User).where(User.username == username)).first()
        if not target or target.id == current_user.id:
            raise HTTPException(status_code=400, detail="Invalid target")
        existing = session.exec(
            select(Follow).where(
                (Follow.follower_id == current_user.id)
                & (Follow.following_id == target.id)
            )
        ).first()
        if existing:
            session.delete(existing)
            session.commit()
            return {"following": False}
        f = Follow(follower_id=current_user.id, following_id=target.id)
        session.add(f)
        session.commit()
        try:
            act = Activity(
                user_id=current_user.id, verb="followed", target_id=target.id
            )
            session.add(act)
            session.commit()
        except Exception:
            pass
    return {"following": True}


@app.get("/users/{username}", response_class=HTMLResponse)
def profile_view(
    request: Request, username: str, current_user: User = Depends(get_current_user_optional)
):
    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == username)).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        presentations_raw = session.exec(
            select(Presentation)
            .where(Presentation.owner_id == user.id)
            .options(selectinload(Presentation.owner), selectinload(Presentation.category))
            .order_by(Presentation.created_at.desc())
        ).all()
        presentations = []
        for p in presentations_raw:
            owner = getattr(p, "owner", None)
            cat = getattr(p, "category", None)
            presentations.append(
                SimpleNamespace(
                    id=p.id,
                    title=p.title,
                    description=getattr(p, "description", None),
                    filename=p.filename,
                    mimetype=p.mimetype,
                    owner_id=p.owner_id,
                    owner_username=getattr(owner, "username", None) if owner else None,
                        owner_email=getattr(owner, "email", None) if owner else None,
                    views=getattr(p, "views", None),
                    cover_url=getattr(p, "cover_url", None) if hasattr(p, "cover_url") else None,
                    category=SimpleNamespace(name=cat.name) if cat is not None else None,
                    created_at=getattr(p, "created_at", None),
                )
            )
        followers = session.exec(
            select(Follow).where(Follow.following_id == user.id)
        ).all()
        following = session.exec(
            select(Follow).where(Follow.follower_id == user.id)
        ).all()
        follower_count = len(followers)
        following_count = len(following)
    return templates.TemplateResponse(
        "profile.html",
        {
            "request": request,
            "user_obj": user,
            "presentations": presentations,
            "follower_count": follower_count,
            "following_count": following_count,
            "current_user": current_user,
        },
    )


@app.websocket("/ws/chat/{other_id}")
async def websocket_chat(websocket: WebSocket, other_id: int):
    # authenticate user from cookie (access_token)
    cookie = websocket.cookies.get("access_token")
    token = None
    if cookie:
        token = cookie.split(" ", 1)[-1] if " " in cookie else cookie
    if not token:
        await websocket.close(code=1008)
        return
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            await websocket.close(code=1008)
            return
    except Exception:
        await websocket.close(code=1008)
        return

    # resolve user
    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == username)).first()
        if not user:
            await websocket.close(code=1008)
            return
    me_id = user.id
    await manager.connect(me_id, websocket)
    try:
        # broadcast presence to other connected users
        await manager.broadcast_presence(me_id, True)
        while True:
            data = await websocket.receive_text()
            try:
                obj = json.loads(data)
            except Exception:
                continue
            # expected obj: { action: 'message', content: '...', to: <user_id> }
            if obj.get("action") == "message":
                to_id = int(obj.get("to"))
                content = obj.get("content", "")
                # persist message
                with Session(engine) as session:
                    msg = Message(sender_id=me_id, recipient_id=to_id, content=content)
                    session.add(msg)
                    session.commit()
                    session.refresh(msg)
                out = {
                    "type": "message",
                    "id": msg.id,
                    "from": me_id,
                    "to": to_id,
                    "content": content,
                    "created_at": msg.created_at.isoformat(),
                }
                # send to recipient if online
                await manager.send_personal(to_id, out)
                # echo back to sender(s)
                await manager.send_personal(me_id, out)
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await manager.broadcast_presence(me_id, False)
        except Exception:
            pass
        await manager.disconnect(me_id, websocket)


@app.get('/api/messages/unread_counts')
def unread_counts(current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        rows = session.exec(select(Message.sender_id, func.count(Message.id)).where(Message.recipient_id == current_user.id, Message.read == False).group_by(Message.sender_id)).all()
        counts = {int(r[0]): int(r[1]) for r in rows}
    return JSONResponse(counts)


@app.get('/api/messages/{other_id}')
def get_messages(other_id: int, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        msgs = session.exec(
            select(Message)
            .where(
                (Message.sender_id == current_user.id) & (Message.recipient_id == other_id)
                | ((Message.sender_id == other_id) & (Message.recipient_id == current_user.id))
            )
            .order_by(Message.created_at.desc())
        ).all()
        # mark messages as read where current_user is the recipient
        try:
            unread_to_mark = [m for m in msgs if m.recipient_id == current_user.id and not m.read]
            for m in unread_to_mark:
                m.read = True
                session.add(m)
            if unread_to_mark:
                session.commit()
        except Exception:
            session.rollback()

        # return most recent 50
        out = [
            {
                "id": m.id,
                "from": m.sender_id,
                "to": m.recipient_id,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
            }
            for m in msgs[:50]
        ]
    return JSONResponse(out)


@app.get('/api/online/{user_id}')
def api_online(user_id: int):
    return JSONResponse({"online": manager.is_online(user_id)})


@app.get('/api/resolve-username')
def resolve_username(username: str = Query(...)):
    with Session(engine) as session:
        u = session.exec(select(User).where(User.username == username)).first()
        if not u:
            return JSONResponse({'error': 'not_found'}, status_code=404)
        return JSONResponse({'id': u.id, 'username': u.username})



@app.get('/api/me')
def api_me(current_user: User = Depends(get_current_user)):
    return JSONResponse({'id': current_user.id, 'username': current_user.username})


@app.post('/api/messages/{other_id}')
def post_message(other_id: int, payload: dict = Body(...), current_user: User = Depends(get_current_user)):
    content = (payload.get('content') or '').strip()
    if not content:
        return JSONResponse({'error': 'empty'}, status_code=400)
    with Session(engine) as session:
        msg = Message(sender_id=current_user.id, recipient_id=other_id, content=content)
        session.add(msg)
        session.commit()
        session.refresh(msg)
    out = {
        'id': msg.id,
        'from': msg.sender_id,
        'to': msg.recipient_id,
        'content': msg.content,
        'created_at': msg.created_at.isoformat(),
    }
    # notify recipient if online
    try:
        asyncio.create_task(manager.send_personal(other_id, out))
    except Exception:
        pass
    return JSONResponse(out)


@app.get("/me/edit", response_class=HTMLResponse)
def edit_profile_get(request: Request, current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse(
        "edit_profile.html", {"request": request, "user_obj": current_user}
    )


@app.post("/me/edit")
async def edit_profile_post(
    request: Request,
    username: str = Form(None),
    full_name: str = Form(None),
    date_of_birth: str = Form(None),
    bio: str = Form(None),
    avatar: UploadFile = File(None),
    current_user: User = Depends(get_current_user),
):
    # Only treat avatar as provided when a real file was uploaded (non-empty filename)
    if avatar and getattr(avatar, "filename", ""):
        av_ext = Path(avatar.filename).suffix.lower()
        content_type = getattr(avatar, "content_type", "") or ""
        allowed_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif"}
        mime_map = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "image/svg+xml": ".svg",
            "image/avif": ".avif",
        }
        # Accept when extension is known OR when content-type is any image/*
        if not (av_ext in allowed_exts or content_type.startswith("image/")):
            return templates.TemplateResponse(
                "edit_profile.html",
                {
                    "request": request,
                    "error": "Unsupported avatar type",
                    "user_obj": current_user,
                },
            )
        # If extension missing or not standard, derive from content-type when possible
        if not av_ext or av_ext not in allowed_exts:
            av_ext = mime_map.get(content_type, ".png")
        avatar_name = f"avatar_{current_user.id}_{uuid.uuid4().hex}{av_ext}"
        save_path = Path(UPLOAD_DIR) / avatar_name
        with save_path.open("wb") as buffer:
            shutil.copyfileobj(avatar.file, buffer)
        with Session(engine) as session:
            u = session.get(User, current_user.id)
            u.avatar = avatar_name
            if username:
                # ensure username uniqueness
                exists = session.exec(select(User).where(User.username == username, User.id != u.id)).first()
                if exists:
                    return templates.TemplateResponse(
                        "edit_profile.html",
                        {"request": request, "error": "Username already taken", "user_obj": current_user},
                    )
                u.username = username
            if full_name is not None:
                u.full_name = full_name
            if date_of_birth:
                try:
                    from datetime import date as _date

                    u.date_of_birth = _date.fromisoformat(date_of_birth)
                except Exception:
                    pass
            if bio is not None:
                u.bio = bio
            session.add(u)
            session.commit()
            new_username = u.username
    else:
        with Session(engine) as session:
            u = session.get(User, current_user.id)
            if username:
                exists = session.exec(select(User).where(User.username == username, User.id != u.id)).first()
                if exists:
                    return templates.TemplateResponse(
                        "edit_profile.html",
                        {"request": request, "error": "Username already taken", "user_obj": current_user},
                    )
                u.username = username
            if full_name is not None:
                u.full_name = full_name
            if date_of_birth:
                try:
                    from datetime import date as _date

                    u.date_of_birth = _date.fromisoformat(date_of_birth)
                except Exception:
                    pass
            if bio is not None:
                u.bio = bio
            session.add(u)
            session.commit()
            new_username = u.username
    return RedirectResponse(url=f"/users/{new_username}", status_code=status.HTTP_302_FOUND)


@app.post("/paypal/webhook")
async def paypal_webhook(request: Request):
    # Safely parse JSON body
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    # Raw body bytes (kept in case payments.verify needs them)
    try:
        payload_bytes = await request.body()
    except Exception:
        payload_bytes = b""

    # PayPal headers for webhook verification
    transmission_id = request.headers.get("PAYPAL-TRANSMISSION-ID")
    transmission_time = request.headers.get("PAYPAL-TRANSMISSION-TIME")
    cert_url = request.headers.get("PAYPAL-CERT-URL")
    auth_algo = request.headers.get("PAYPAL-AUTH-ALGO")
    transmission_sig = request.headers.get("PAYPAL-TRANSMISSION-SIG")
    webhook_id = os.getenv("PAYPAL_WEBHOOK_ID")

    verified = None
    if webhook_id and transmission_id and transmission_sig:
        try:
            # Pass the parsed JSON event to the verification helper
            result = await verify_webhook_signature(
                transmission_id,
                transmission_time,
                cert_url,
                auth_algo,
                transmission_sig,
                webhook_id,
                payload,
            )
            verified = result.get("verification_status")
        except Exception:
            verified = None

    resource = payload.get("resource") or payload
    order_id = resource.get("id")
    status_ = resource.get("status")
    payer = resource.get("payer") or {}
    payer_id = payer.get("payer_id") if isinstance(payer, dict) else None
    amount = None
    currency = None
    purchase_units = resource.get("purchase_units") or []
    if purchase_units:
        amt = purchase_units[0].get("amount")
        if amt:
            amount = amt.get("value")
            currency = amt.get("currency_code")

    with Session(engine) as session:
        tx = Transaction(
            order_id=order_id,
            payer_id=payer_id,
            amount=amount,
            currency=currency,
            status=status_,
        )
        # record verification status in status if present
        if verified:
            tx.status = f"{status_} (verified:{verified})"
        session.add(tx)
        session.commit()

    return Response(status_code=200)


@app.get("/premium/dashboard", response_class=HTMLResponse)
def premium_dashboard(request: Request, current_user: User = Depends(get_current_user)):
    if not current_user.is_premium:
        raise HTTPException(status_code=403, detail="Premium access only")
    with Session(engine) as session:
        rows = session.exec(
            select(Presentation).where(Presentation.owner_id == current_user.id)
        ).all()
        presentations = []
        for p in rows:
            presentations.append(SimpleNamespace(
                id=p.id,
                title=p.title,
                views=getattr(p, 'views', 0),
                created_at=getattr(p, 'created_at', None),
            ))
        # attach bookmark counts in batch
        pres_ids = [p.id for p in presentations if getattr(p, 'id', None)]
        if pres_ids:
            rows_bc = session.exec(
                select(Bookmark.presentation_id, func.count(Bookmark.id))
                .where(Bookmark.presentation_id.in_(list(pres_ids)))
                .group_by(Bookmark.presentation_id)
            ).all()
            bc = {int(r[0]): int(r[1]) for r in rows_bc}
        else:
            bc = {}
        for p in presentations:
            setattr(p, 'bookmarks_count', bc.get(getattr(p, 'id', None), 0))

        total_views = sum(p.views for p in presentations)
        total_presentations = len(presentations)
        txs = session.exec(
            select(Transaction).where(Transaction.user_id == current_user.id)
        ).all()
    return templates.TemplateResponse(
        "premium_dashboard.html",
        {
            "request": request,
            "presentations": presentations,
            "total_views": total_views,
            "total_presentations": total_presentations,
            "transactions": txs,
        },
    )


@app.get("/admin/transactions", response_class=HTMLResponse)
def admin_transactions(
    request: Request, current_user: User = Depends(get_current_user)
):
    # basic admin protection: require is_premium (for demo) - replace with proper admin check
    if not current_user.is_premium:
        raise HTTPException(status_code=403, detail="Admin access only")
    with Session(engine) as session:
        transactions = session.exec(
            select(Transaction).order_by(Transaction.created_at.desc())
        ).all()
    return templates.TemplateResponse(
        "admin_transactions.html", {"request": request, "transactions": transactions}
    )


@app.get("/admin/webhooks", response_class=HTMLResponse)
def admin_webhooks(request: Request, current_user: User = Depends(get_current_user)):
    if not current_user.is_premium:
        raise HTTPException(status_code=403, detail="Admin access only")
    with Session(engine) as session:
        events = session.exec(
            select(WebhookEvent).order_by(WebhookEvent.created_at.desc())
        ).all()
    return templates.TemplateResponse(
        "admin_webhooks.html", {"request": request, "events": events}
    )


@app.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = "", category: Optional[str] = Query(None)):
    with Session(engine) as session:
        # Base statement: search title/description by query and eager-load owner
        statement = (
            select(Presentation)
            .options(selectinload(Presentation.owner))
            .where((Presentation.title.contains(q)) | (Presentation.description.contains(q)))
        )

        # If a category name is supplied, filter by presentations that either
        # have a matching `category_id` OR whose title contains the category name.
        if category:
            cat = session.exec(select(Category).where(Category.name == category)).first()
            if cat:
                statement = statement.where(
                    (Presentation.category_id == cat.id)
                    | (func.lower(Presentation.title).contains(cat.name.lower()))
                )
            else:
                # No Category row exists for this name – fall back to title matching
                statement = statement.where(func.lower(Presentation.title).contains(category.lower()))

        results_raw = session.exec(statement).all()
        results = []
        for p in results_raw:
            owner = getattr(p, 'owner', None)
            results.append(SimpleNamespace(
                id=p.id,
                title=p.title,
                description=getattr(p, 'description', None),
                filename=p.filename,
                mimetype=p.mimetype,
                owner_id=p.owner_id,
                owner_username=getattr(owner, 'username', None) if owner else None,
                owner_email=getattr(owner, 'email', None) if owner else None,
                views=getattr(p, 'views', None),
                cover_url=getattr(p, 'cover_url', None) if hasattr(p, 'cover_url') else None,
                created_at=getattr(p, 'created_at', None),
            ))

        # attach bookmark counts for results (batch)
        res_ids = [r.id for r in results if getattr(r, 'id', None)]
        if res_ids:
            rows = session.exec(
                select(Bookmark.presentation_id, func.count(Bookmark.id))
                .where(Bookmark.presentation_id.in_(list(res_ids)))
                .group_by(Bookmark.presentation_id)
            ).all()
            bc = {int(r[0]): int(r[1]) for r in rows}
        else:
            bc = {}
        for r in results:
            setattr(r, 'bookmarks_count', bc.get(getattr(r, 'id', None), 0))

    return templates.TemplateResponse(
        "search.html", {"request": request, "q": q, "results": results}
    )


@app.get("/api/bookmarks")
def get_bookmarks(request: Request, current_user: User = Depends(get_current_user)):
    """Return a list of presentation IDs bookmarked by the current user."""
    if not current_user:
        return JSONResponse({"bookmarks": []})
    with Session(engine) as session:
        rows = session.exec(select(Bookmark.presentation_id).where(Bookmark.user_id == current_user.id)).all()
        # rows is list of tuples or values depending on driver
        ids = [r[0] if isinstance(r, (list, tuple)) else r for r in rows]
    return {"bookmarks": ids}


@app.post("/api/presentations/{presentation_id}/bookmark")
def toggle_bookmark(presentation_id: int, request: Request, current_user: User = Depends(get_current_user)):
    """Toggle bookmark for a presentation for the authenticated user.

    Returns JSON: {"bookmarked": bool}
    """
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    with Session(engine) as session:
        pres = session.get(Presentation, presentation_id)
        if not pres:
            raise HTTPException(status_code=404, detail="Presentation not found")
        existing = session.exec(select(Bookmark).where((Bookmark.user_id == current_user.id) & (Bookmark.presentation_id == presentation_id))).first()
        if existing:
            try:
                session.delete(existing)
                session.commit()
                # return updated count as well
                cnt = session.exec(select(Bookmark).where(Bookmark.presentation_id == presentation_id)).all()
                return {"bookmarked": False, "count": len(cnt)}
            except Exception:
                session.rollback()
                raise HTTPException(status_code=500, detail="Failed to remove bookmark")
        else:
            try:
                bm = Bookmark(user_id=current_user.id, presentation_id=presentation_id)
                session.add(bm)
                session.commit()
                session.refresh(bm)
                cnt = session.exec(select(Bookmark).where(Bookmark.presentation_id == presentation_id)).all()
                return {"bookmarked": True, "count": len(cnt)}
            except Exception:
                session.rollback()
                raise HTTPException(status_code=500, detail="Failed to create bookmark")



@app.get("/api/presentations/{presentation_id}/bookmarks")
def presentation_bookmarks(presentation_id: int, request: Request, current_user: User = Depends(get_current_user_optional)):
    """Return bookmark count for a presentation and whether current user bookmarked it."""
    with Session(engine) as session:
        rows = session.exec(select(Bookmark).where(Bookmark.presentation_id == presentation_id)).all()
        count = len(rows)
        bookmarked = False
        if current_user:
            exists = session.exec(
                select(Bookmark).where((Bookmark.presentation_id == presentation_id) & (Bookmark.user_id == current_user.id))
            ).first()
            bookmarked = bool(exists)
    return {"count": count, "bookmarked": bookmarked}


@app.post("/admin/backfill-categories")
def backfill_categories(request: Request, current_user: User = Depends(get_current_user)):
    """Classify existing presentations that don't have a category yet.

    This endpoint requires an authenticated user. It will inspect each
    presentation without a `category_id`, attempt to auto-classify it using
    `auto_classify_category`, and persist the category link when found.
    """
    updated = 0
    total = 0
    with Session(engine) as session:
        rows = session.exec(select(Presentation).where(Presentation.category_id == None)).all()
        total = len(rows)
        for p in rows:
            try:
                cat = auto_classify_category(session, p.title)
                if cat:
                    # reload the presentation and set category
                    pres = session.get(Presentation, p.id)
                    pres.category_id = cat.id
                    session.add(pres)
                    session.commit()
                    updated += 1
            except Exception:
                logger.exception("Failed to classify presentation %s", getattr(p, 'id', None))

    return {"total_unclassified": total, "updated": updated}


@app.get("/activity", response_class=HTMLResponse)
def activity_feed(request: Request, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        acts = session.exec(select(Activity).order_by(Activity.created_at.desc())).all()
        # Eager load user and presentation title where possible
        enriched = []
        for a in acts:
            user = session.get(User, a.user_id) if a.user_id else None
            pres = session.get(Presentation, a.target_id) if a.target_id else None
            pres_safe = None
            if pres:
                pres_safe = SimpleNamespace(id=pres.id, title=pres.title)
            enriched.append({"activity": a, "user": user, "presentation": pres_safe})
        # attach bookmark counts for presentations referenced in activity
        pres_ids = [it['presentation'].id for it in enriched if it.get('presentation')]
        if pres_ids:
            rows_bc = session.exec(
                select(Bookmark.presentation_id, func.count(Bookmark.id))
                .where(Bookmark.presentation_id.in_(list(pres_ids)))
                .group_by(Bookmark.presentation_id)
            ).all()
            bc = {int(r[0]): int(r[1]) for r in rows_bc}
            for it in enriched:
                p = it.get('presentation')
                if p:
                    setattr(p, 'bookmarks_count', bc.get(getattr(p, 'id', None), 0))
    return templates.TemplateResponse(
        "activity.html", {"request": request, "items": enriched}
    )


@app.get("/premium/subscribe", response_class=HTMLResponse)
async def premium_subscribe(request: Request, current_user: User = Depends(get_current_user)):
    # Render a Paystack-powered subscribe page
    error = None
    if not PAYSTACK_PUBLIC_KEY:
        error = "Paystack not configured. Set PAYSTACK_PUBLIC_KEY and PAYSTACK_SECRET_KEY."
    amount_major = PAYSTACK_AMOUNT_KOBO / 100
    return templates.TemplateResponse(
        "subscribe.html",
        {
            "request": request,
            "paystack_public_key": PAYSTACK_PUBLIC_KEY,
            "amount": amount_major,
            "amount_kobo": PAYSTACK_AMOUNT_KOBO,
            "currency": PAYSTACK_CURRENCY,
            "error": error,
        },
    )


@app.post("/api/paystack/initialize")
async def paystack_initialize(request: Request, current_user: User = Depends(get_current_user)):
    if not PAYSTACK_PUBLIC_KEY:
        raise HTTPException(status_code=400, detail="Paystack not configured")
    email = current_user.email or f"user{current_user.id}@example.com"
    callback_url = str(request.url_for("paystack_callback"))
    metadata = {"user_id": current_user.id, "username": current_user.username}
    try:
        init_res = await paystack_initialize_transaction(
            email=email,
            amount_kobo=PAYSTACK_AMOUNT_KOBO,
            callback_url=callback_url,
            metadata=metadata,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Paystack init failed: {e}")

    data = init_res.get("data", {})
    auth_url = data.get("authorization_url")
    reference = data.get("reference")
    if not auth_url or not reference:
        raise HTTPException(status_code=400, detail="Invalid Paystack response")

    # Record pending transaction
    with Session(engine) as session:
        tx = Transaction(
            order_id=reference,
            payer_id=email,
            amount=str(PAYSTACK_AMOUNT_KOBO / 100),
            currency=PAYSTACK_CURRENCY,
            status="initialized",
            user_id=current_user.id,
        )
        session.add(tx)
        session.commit()

    return {"authorization_url": auth_url, "reference": reference}


@app.get("/paystack/callback")
async def paystack_callback(reference: str = Query(...)):
    try:
        verify_res = await paystack_verify_transaction(reference)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Verification failed: {e}")

    data = verify_res.get("data", {}) if isinstance(verify_res, dict) else {}
    status_val = data.get("status")
    amount_kobo = data.get("amount")
    currency = data.get("currency")
    email = (data.get("customer") or {}).get("email")
    metadata = data.get("metadata") or {}
    user_id = metadata.get("user_id")

    metadata = metadata or {}
    action = metadata.get("action")
    with Session(engine) as session:
        tx = Transaction(
            order_id=reference,
            payer_id=email,
            amount=str(amount_kobo / 100) if amount_kobo else None,
            currency=currency,
            status=status_val,
            user_id=user_id,
        )
        session.add(tx)

        # Action-specific handling
        if status_val == "success":
            if action == "buy_presentation":
                # For purchases, we may notify the owner or record activity.
                pres_id = metadata.get("presentation_id")
                owner_id = metadata.get("owner_id")
                if pres_id and owner_id:
                    a = Activity(user_id=user_id, verb="bought_presentation", target_id=pres_id)
                    session.add(a)
            else:
                # default behavior: treat as premium subscription
                if user_id:
                    u = session.get(User, user_id)
                    if u:
                        u.is_premium = True
                        session.add(u)

        session.commit()

    # Redirect users to a sensible page after payment
    if action == "buy_presentation" and isinstance(metadata, dict) and metadata.get("presentation_id"):
        redirect_target = f"/presentations/{metadata.get('presentation_id')}"
    else:
        redirect_target = "/premium/dashboard" if status_val == "success" else "/premium/subscribe?error=Payment+failed"

    return RedirectResponse(url=redirect_target, status_code=status.HTTP_302_FOUND)


@app.post("/users/{user_id}/follow")
def follow_user(request: Request, user_id: int, current_user: User = Depends(get_current_user)):
    # create follow relationship if not exists
    if current_user.id == user_id:
        # cannot follow yourself
        accept = request.headers.get('accept', '')
        xreq = request.headers.get('x-requested-with', '')
        wants_json = ('application/json' in accept) or (xreq == 'XMLHttpRequest')
        if wants_json:
            return JSONResponse({'error': 'cannot_follow_self'}, status_code=400)
        return RedirectResponse(url=request.headers.get("Referer", "/"), status_code=status.HTTP_302_FOUND)
    with Session(engine) as session:
        exists = session.exec(select(Follow).where((Follow.follower_id == current_user.id) & (Follow.following_id == user_id))).first()
        if not exists:
            f = Follow(follower_id=current_user.id, following_id=user_id)
            session.add(f)
            session.commit()
        # compute follower count
        followers = session.exec(select(Follow).where(Follow.following_id == user_id)).all()
        follower_count = len(followers)
    accept = request.headers.get('accept', '')
    xreq = request.headers.get('x-requested-with', '')
    wants_json = ('application/json' in accept) or (xreq == 'XMLHttpRequest')
    if wants_json:
        return JSONResponse({'following': True, 'followers_count': follower_count})
    return RedirectResponse(url=request.headers.get("Referer", "/"), status_code=status.HTTP_302_FOUND)


@app.post("/users/{user_id}/unfollow")
def unfollow_user(request: Request, user_id: int, current_user: User = Depends(get_current_user)):
    if current_user.id == user_id:
        accept = request.headers.get('accept', '')
        xreq = request.headers.get('x-requested-with', '')
        wants_json = ('application/json' in accept) or (xreq == 'XMLHttpRequest')
        if wants_json:
            return JSONResponse({'error': 'cannot_unfollow_self'}, status_code=400)
        return RedirectResponse(url=request.headers.get("Referer", "/"), status_code=status.HTTP_302_FOUND)
    with Session(engine) as session:
        item = session.exec(select(Follow).where((Follow.follower_id == current_user.id) & (Follow.following_id == user_id))).first()
        if item:
            session.delete(item)
            session.commit()
        followers = session.exec(select(Follow).where(Follow.following_id == user_id)).all()
        follower_count = len(followers)
    accept = request.headers.get('accept', '')
    xreq = request.headers.get('x-requested-with', '')
    wants_json = ('application/json' in accept) or (xreq == 'XMLHttpRequest')
    if wants_json:
        return JSONResponse({'following': False, 'followers_count': follower_count})
    return RedirectResponse(url=request.headers.get("Referer", "/"), status_code=status.HTTP_302_FOUND)


@app.post("/presentations/{presentation_id}/buy")
async def buy_presentation(request: Request, presentation_id: int, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        p = session.get(Presentation, presentation_id)
        if not p:
            raise HTTPException(status_code=404, detail="Not found")

    # initialize a Paystack transaction and redirect user to Paystack
    try:
        email = current_user.email or f"user{current_user.id}@example.com"
        callback_url = str(request.url_for("paystack_callback"))
        metadata = {"user_id": current_user.id, "action": "buy_presentation", "presentation_id": presentation_id, "owner_id": p.owner_id}
        init_res = await paystack_initialize_transaction(email=email, amount_kobo=PAYSTACK_AMOUNT_KOBO, callback_url=callback_url, metadata=metadata)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Paystack init failed: {e}")

    data = init_res.get("data", {})
    auth_url = data.get("authorization_url")
    reference = data.get("reference")
    if not auth_url or not reference:
        raise HTTPException(status_code=400, detail="Invalid Paystack response")

    # Record pending transaction
    with Session(engine) as session:
        tx = Transaction(order_id=reference, payer_id=email, amount=str(PAYSTACK_AMOUNT_KOBO / 100), currency=PAYSTACK_CURRENCY, status="initialized", user_id=current_user.id)
        session.add(tx)
        session.commit()

    return RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)


@app.get("/auth/{provider}")
async def oauth_login(request: Request, provider: str, next: str = None):
    client = oauth.create_client(provider)
    if not client:
        # Redirect back to login with a helpful message instead of 404
        msg = "OAuth provider not configured. Set environment variables and restart."
        return RedirectResponse(
            url=str(request.url_for("login")) + f"?error={msg}",
            status_code=status.HTTP_302_FOUND,
        )
    redirect_uri = request.url_for("oauth_callback", provider=provider)
    # Pass the intended next target in the `state` param so it round-trips the provider
    state = next if next and next.startswith("/") else None
    return await client.authorize_redirect(request, str(redirect_uri), state=state)


@app.get("/auth/{provider}/callback")
async def oauth_callback(request: Request, provider: str):
    client = oauth.create_client(provider)
    if not client:
        msg = "OAuth provider not configured. Set environment variables and restart."
        return RedirectResponse(
            url=str(request.url_for("login")) + f"?error={msg}",
            status_code=status.HTTP_302_FOUND,
        )
    token = await client.authorize_access_token(request)
    profile = None
    if provider == "google":
        # For Google, token contains id_token; parse userinfo
        try:
            profile = await client.parse_id_token(request, token)
        except Exception:
            # fallback to userinfo endpoint
            resp = await client.get("userinfo", token=token)
            profile = resp.json()
    elif provider == "github":
        resp = await client.get("user", token=token)
        profile = resp.json()
        # get primary email if missing
        if not profile.get("email"):
            emails = await client.get("/user/emails", token=token)
            for e in emails.json():
                if e.get("primary"):
                    profile["email"] = e.get("email")
                    break
    elif provider == "linkedin":
        # LinkedIn requires two calls: basic profile and email
        # Basic profile
        prof_resp = await client.get(
            "me",
            token=token,
            params={"projection": "(id,localizedFirstName,localizedLastName)"},
        )
        prof_data = prof_resp.json() if prof_resp.ok else {}

        email_resp = await client.get(
            "emailAddress",
            token=token,
            params={"q": "members", "projection": "(elements*(handle~))"},
        )
        email_data = email_resp.json() if email_resp.ok else {}

        email_elements = email_data.get("elements", []) if isinstance(email_data, dict) else []
        primary_email = None
        for e in email_elements:
            handle = e.get("handle~") or {}
            if handle.get("emailAddress"):
                primary_email = handle.get("emailAddress")
                break

        full_name = " ".join(
            part
            for part in [
                prof_data.get("localizedFirstName"),
                prof_data.get("localizedLastName"),
            ]
            if part
        ).strip()

        profile = {
            "email": primary_email,
            "name": full_name or prof_data.get("id"),
            "id": prof_data.get("id"),
        }

    if not profile:
        raise HTTPException(status_code=400, detail="Failed to fetch user profile")

    email = profile.get("email") or profile.get("login")
    username_source = profile.get("name") or profile.get("login")
    username = username_source or (email.split("@", 1)[0] if email else profile.get("id"))

    with Session(engine) as session:
        user = (
            session.exec(select(User).where(User.email == email)).first()
            if email
            else None
        )
        if not user:
            # create new user
            user = User(
                username=username,
                email=email or f"{username}@local",
                hashed_password=get_password_hash(uuid.uuid4().hex),
            )
            session.add(user)
            session.commit()
            session.refresh(user)

    token_jwt = create_access_token({"sub": user.username})
    # Try to preserve state (next) passed through the OAuth round-trip
    next_target = request.query_params.get("state")
    dest = "/featured"
    if next_target and next_target.startswith("/"):
        dest = next_target
    response = RedirectResponse(url=dest, status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="access_token", value=f"Bearer {token_jwt}")
    return response


@app.get("/paypal/return")
async def paypal_return(
    token: str = None, current_user: User = Depends(get_current_user)
):
    # token is PayPal order id
    if not token:
        raise HTTPException(status_code=400, detail="Missing token")
    result = await capture_order(token)
    # extract amount/currency/status
    amount = None
    currency = None
    status_ = None
    try:
        status_ = result.get("status")
        # Look for captures
        pus = result.get("purchase_units") or []
        if pus:
            pu = pus[0]
            payments = pu.get("payments") or {}
            captures = payments.get("captures") or []
            if captures:
                cap = captures[0]
                amt = cap.get("amount") or {}
                amount = amt.get("value")
                currency = amt.get("currency_code")
        # fallback
        if not amount:
            amt = result.get("amount") or {}
            amount = amt.get("value")
            currency = amt.get("currency_code")
    except Exception:
        pass
    # Mark user premium and store transaction
    with Session(engine) as session:
        u = session.get(User, current_user.id)
        u.is_premium = True
        session.add(u)
        session.commit()
        tx = Transaction(
            order_id=token,
            payer_id=None,
            user_id=u.id,
            amount=amount,
            currency=currency,
            status=status_,
        )
        session.add(tx)
        session.commit()
    return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
