from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env", override=True)
from starlette.websockets import WebSocket, WebSocketDisconnect
try:
    import fitz
except ImportError:
    fitz = None
import subprocess
try:
    from PIL import Image
except ImportError:
    Image = None
from fastapi import status
from fastapi.responses import Response, StreamingResponse
import mimetypes


import os
import time
import secrets
from threading import Lock
from collections import OrderedDict, deque
from types import SimpleNamespace
from urllib.parse import quote, urlencode
import re
try:
    import boto3
except Exception:
    boto3 = None
from sqlalchemy import func, desc, or_, delete
from sqlalchemy.orm import selectinload
import redis as _redis
import shutil
import logging
from fastapi import FastAPI, Request, Depends, Form, Query, HTTPException, Body, File, UploadFile
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates

# create FastAPI app instance
app = FastAPI()
# Initialize Jinja2 templates directory (templates/ at repo root is used)
try:
    templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), '..', 'templates'))
except Exception:
    # fallback to a relative templates folder at workspace root
    templates = Jinja2Templates(directory=os.path.join(os.getcwd(), 'templates'))
# Register common template filters used across templates
try:
    templates.env.filters['url_encode'] = lambda s: quote(str(s) if s is not None else '')
except Exception:
    pass
try:
    templates.env.filters['humanize_comment_date'] = humanize_comment_date
except Exception:
    pass
from .auth import get_current_user, get_current_user_optional
from .models import User, Membership, Space, Classroom, Presentation, Category, Message
from .models import Bookmark, Notification, Activity, Follow, Transaction, LibraryItem
from .models import ConversionJob, AIResult, Comment, Like, ClassroomMessage, SpaceMessage, StudentAnalytics, WebhookEvent, Submission, Attendance, School, Assignment, AssignmentStatus, ConsentLog, Tag, PresentationTag, Collection, CollectionItem
from .database import engine, create_db_and_tables
from .auth import get_password_hash, create_access_token, create_refresh_token, authenticate_user
from . import oauth
from .payments import paystack_initialize_transaction, paystack_verify_transaction, capture_order
import uuid
import hmac
import hashlib
import base64
import io
import zipfile
import tempfile
import smtplib
import ssl
import httpx
from datetime import datetime
from .humanize import humanize_comment_date
from .ai_client import chat_completion, get_ai_provider

# Ensure humanize filter is registered after the function is imported
try:
    templates.env.filters['humanize_comment_date'] = humanize_comment_date
except Exception:
    pass

# basic logger for this module
logger = logging.getLogger('slideshare')

# AI request controls (in-memory; configure via env)
AI_RATE_LIMIT_PER_USER = int(os.getenv('AI_RATE_LIMIT_PER_USER', '20'))
AI_RATE_LIMIT_WINDOW_SEC = int(os.getenv('AI_RATE_LIMIT_WINDOW_SEC', '60'))
AI_GLOBAL_MAX_INFLIGHT = int(os.getenv('AI_GLOBAL_MAX_INFLIGHT', '4'))
AI_CACHE_TTL_SEC = int(os.getenv('AI_CACHE_TTL_SEC', '900'))
AI_CACHE_MAX = int(os.getenv('AI_CACHE_MAX', '200'))
_ai_lock = Lock()
_ai_user_requests: Dict[int, deque] = {}
_ai_inflight = 0
_ai_cache: OrderedDict = OrderedDict()


def _ai_rate_limit_check(user_id: int) -> Optional[int]:
    now = time.time()
    with _ai_lock:
        dq = _ai_user_requests.setdefault(int(user_id), deque())
        while dq and (now - dq[0]) > AI_RATE_LIMIT_WINDOW_SEC:
            dq.popleft()
        if len(dq) >= AI_RATE_LIMIT_PER_USER:
            retry_after = int(AI_RATE_LIMIT_WINDOW_SEC - (now - dq[0])) + 1
            return max(retry_after, 1)
        dq.append(now)
    return None


def _ai_cache_get(key: str) -> Optional[str]:
    now = time.time()
    with _ai_lock:
        item = _ai_cache.get(key)
        if not item:
            return None
        ts, val = item
        if (now - ts) > AI_CACHE_TTL_SEC:
            try:
                del _ai_cache[key]
            except Exception:
                pass
            return None
        _ai_cache.move_to_end(key)
        return val


def _ai_cache_set(key: str, val: str) -> None:
    with _ai_lock:
        _ai_cache[key] = (time.time(), val)
        _ai_cache.move_to_end(key)
        while len(_ai_cache) > AI_CACHE_MAX:
            _ai_cache.popitem(last=False)


def _compute_creator_badges(site_role: Optional[str], total_views: int, total_downloads: int, followers: int, recent_views: int = 0) -> list[str]:
    badges: list[str] = []
    if (site_role or "").lower() == "teacher":
        badges.append("Top Teacher")
    if total_views >= 2000 or recent_views >= 300:
        badges.append("Trending Creator")
    if total_downloads >= 300 or followers >= 150:
        badges.append("Exam Expert")
    return badges

# Paystack / payments defaults (override via env)
PAYSTACK_PUBLIC_KEY = os.getenv('PAYSTACK_PUBLIC_KEY')
PAYSTACK_SECRET_KEY = os.getenv('PAYSTACK_SECRET_KEY')
PAYSTACK_AMOUNT_KOBO = int(os.getenv('PAYSTACK_AMOUNT_KOBO', '0'))
COFFEE_AMOUNT_KOBO = int(os.getenv('COFFEE_AMOUNT_KOBO', str(PAYSTACK_AMOUNT_KOBO or 0)))
# Paystack / payments defaults (override via env)
PAYSTACK_CURRENCY = os.getenv('PAYSTACK_CURRENCY', 'NGN')

# Spotify / OAuth defaults
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')
SPOTIFY_REDIRECT = os.getenv('SPOTIFY_REDIRECT')

# Upload directory (module-level default so static analysis sees it)
UPLOAD_DIR = os.getenv('UPLOAD_DIR', './uploads')

def public_media_url(value: Optional[str]) -> Optional[str]:
    """Convert filesystem or relative upload paths into public /media URLs."""
    if not value:
        return None
    s = str(value)
    if s.startswith("http://") or s.startswith("https://") or s.startswith("/"):
        return s
    try:
        p = Path(s)
        if p.is_absolute():
            try:
                rel = p.resolve().relative_to(Path(UPLOAD_DIR).resolve())
                return f"/media/{rel.as_posix()}"
            except Exception:
                return f"/media/{p.name}"
    except Exception:
        pass
    cleaned = s.replace("\\", "/").lstrip("./").lstrip("/")
    return f"/media/{cleaned}"

try:
    templates.env.filters['public_media_url'] = public_media_url
except Exception:
    pass

from .tasks import enqueue_conversion, convert_presentation, enqueue_ai_summary, enqueue_ai_quiz, enqueue_ai_flashcards, enqueue_ai_mindmap, enqueue_autograde_submission, ai_autograde_submission
from .payments import verify_webhook_signature
from jose import jwt
from .auth import SECRET_KEY, ALGORITHM
from fastapi.responses import PlainTextResponse
from typing import Optional, List, Dict, Set, Any
from pathlib import Path
import json
from sqlmodel import Session, select
from .tasks import enqueue_email

@app.get('/my/teachers', response_class=HTMLResponse)
def my_teachers(request: Request, current_user: User = Depends(get_current_user)):
    """List teachers for the current user across their spaces."""
    with Session(engine) as session:
        # find spaces where current_user is a student
        cls_ids = [m.space_id for m in session.exec(select(Membership).where(Membership.user_id == current_user.id)).all() if m.role == 'student']
        if not cls_ids:
            teachers = []
        else:
            teachers_mem = session.exec(select(Membership).where((Membership.space_id.in_(cls_ids)) & (Membership.role.in_(['teacher','admin'])))).all()
            teacher_ids = sorted({m.user_id for m in teachers_mem})
            teachers = []
            for tid in teacher_ids:
                u = session.get(User, tid)
                if u:
                    teachers.append({'id': u.id, 'username': getattr(u, 'username', None), 'email': getattr(u, 'email', None)})
    return templates.TemplateResponse('my_teachers.html', {'request': request, 'teachers': teachers})


@app.get('/teachers/{teacher_id}/presentations', response_class=HTMLResponse)
def teacher_presentations(request: Request, teacher_id: int, current_user: Optional[User] = Depends(get_current_user)):
    """Show presentations authored by a teacher. Visible to all users.
    If the current_user is a student, this provides the teacher-only view."""
    with Session(engine) as session:
        teacher = session.get(User, teacher_id)
        if not teacher:
            raise HTTPException(status_code=404, detail='Teacher not found')
        pres = session.exec(select(Presentation).where(Presentation.owner_id == teacher_id).order_by(Presentation.created_at.desc())).all()
    return templates.TemplateResponse('teacher_presentations.html', {'request': request, 'teacher': teacher, 'presentations': pres})


@app.get('/spaces', response_class=HTMLResponse)
def student_spaces(request: Request, current_user: User = Depends(get_current_user)):
    """List spaces the current user is a member of."""
    with Session(engine) as session:
        # find memberships for this user
        mems = session.exec(select(Membership).where(Membership.user_id == current_user.id)).all()
        cls_ids = [mid for m in mems for mid in [(getattr(m, 'space_id', None) or getattr(m, 'classroom_id', None))] if mid]
        spaces = []
        for cid in cls_ids:
            s = session.get(Space, cid)
            if not s:
                continue
            # collect teachers for display
            teacher_mems = session.exec(select(Membership).where((Membership.space_id == cid) & (Membership.role.in_(['teacher','admin'])))).all()
            teachers = []
            for tm in teacher_mems:
                u = session.get(User, tm.user_id)
                if u:
                    teachers.append(u)
            # attach teachers list for template use
            try:
                s.teachers = teachers
            except Exception:
                pass
            spaces.append(s)
    return templates.TemplateResponse('student_spaces.html', {'request': request, 'spaces': spaces})


@app.get('/spaces/{space_id}')
def space_root(space_id: int):
    # Temporary compatibility: the full Space views are still classroom-backed.
    return RedirectResponse(f"/spaces/{space_id}/view", status_code=303)


@app.get('/spaces/{space_id}/view')
def space_view(space_id: int):
    # Temporary compatibility: serve the existing classroom view.
    return RedirectResponse(f"/classrooms/{space_id}/view", status_code=303)


@app.get('/spaces/{space_id}/library')
def space_library(space_id: int):
    # Temporary compatibility: serve the existing classroom library view.
    return RedirectResponse(f"/classrooms/{space_id}/library", status_code=303)


def _make_invite_token(payload: dict) -> str:
    """Create a signed token for invitation payload.
    Token format: base64url(json).base64url(hmac_sha256)
    """
    import json, hmac, hashlib, base64
    secret = os.getenv('SECRET_KEY', 'devsecret')
    raw = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8')
    sig = hmac.new(secret.encode('utf-8'), raw, hashlib.sha256).digest()
    braw = base64.urlsafe_b64encode(raw).decode('utf-8')
    bsig = base64.urlsafe_b64encode(sig).decode('utf-8')
    return f"{braw}.{bsig}"


def _verify_invite_token(token: str, max_age: int = 60 * 60 * 24 * 7):
    import json, hmac, hashlib, base64, time
    secret = os.getenv('SECRET_KEY', 'devsecret')
    try:
        braw, bsig = token.split('.')
        raw = base64.urlsafe_b64decode(braw.encode('utf-8'))
        sig = base64.urlsafe_b64decode(bsig.encode('utf-8'))
        expected = hmac.new(secret.encode('utf-8'), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, sig):
            return None
        payload = json.loads(raw.decode('utf-8'))
        ts = payload.get('ts') or 0
        if int(time.time()) - int(ts) > max_age:
            return None
        return payload
    except Exception:
        return None


@app.post('/invite-student')
def invite_student(request: Request, space_id: int = Form(...), email: str = Form(...), csrf_token: Optional[str] = Form(None), current_user: User = Depends(get_current_user)):
    """Teacher/admin invites a user by email to join a space. Sends accept/decline links."""
    validate_csrf(request, csrf_token)
    with Session(engine) as session:
        s = session.get(Space, space_id)
        if not s:
            raise HTTPException(status_code=404, detail='Space not found')
        # check current_user is teacher/admin in space
        mem = session.exec(select(Membership).where((Membership.space_id == space_id) & (Membership.user_id == current_user.id) & (Membership.role.in_(['teacher', 'admin'])))).first()
        if not mem:
            raise HTTPException(status_code=403, detail='Only teacher/admin can invite')
        # build token
        import time
        payload = {'inviter_id': current_user.id, 'space_id': space_id, 'email': email, 'ts': int(time.time())}
        token = _make_invite_token(payload)
        # build accept/decline links
        try:
            accept = request.url_for('invitations_respond') + f"?token={token}&action=accept"
            decline = request.url_for('invitations_respond') + f"?token={token}&action=decline"
        except Exception:
            base = str(request.base_url).rstrip('/')
            accept = f"{base}/invitations/respond?token={token}&action=accept"
            decline = f"{base}/invitations/respond?token={token}&action=decline"

        # send email via queue
        try:
            ctx = {'inviter_name': getattr(current_user, 'username', ''), 'space_name': getattr(s, 'name', ''), 'accept_url': accept, 'decline_url': decline}
            enqueue_email(email, 'Invitation to join space', None, 'emails/invite_student.html', ctx)
        except Exception:
            pass
    return RedirectResponse(f"/spaces/{space_id}/view", status_code=303)


@app.get('/invitations/respond', response_class=HTMLResponse)
def invitations_respond(request: Request, token: str = Query(...), action: str = Query(...)):
    p = _verify_invite_token(token)
    if not p:
        return HTMLResponse('<p>Invalid or expired invitation token.</p>', status_code=400)
    email = p.get('email')
    space_id = p.get('space_id')
    with Session(engine) as session:
        u = session.exec(select(User).where(User.email == email)).first()
        if action == 'accept':
            if not u:
                # redirect to register with invite token
                return RedirectResponse(f"/register?invite_token={token}")
            # add membership if not exists
            exists = session.exec(select(Membership).where((Membership.user_id == u.id) & (Membership.space_id == space_id))).first()
            if not exists:
                m = Membership(user_id=u.id, classroom_id=space_id, space_id=space_id, role='student')
                session.add(m)
                # system message in space chat
                try:
                    cm = SpaceMessage(space_id=space_id, sender_id=u.id, content=f"[system] {u.username} joined the space.")
                    session.add(cm)
                except Exception:
                    pass
                session.commit()
            return HTMLResponse('<p>Thanks — you have been added to the space. You can <a href="/">return to the site</a>.</p>')
        else:
            return HTMLResponse('<p>You declined the invitation. No changes made.</p>')


@app.post('/choose-role')
def choose_role(request: Request, role: str = Form(...), invite_token: Optional[str] = Form(None), current_user: Optional[User] = Depends(get_current_user_optional)):
    """Persist a visitor's role choice in a cookie and to the user profile when logged in.
    Also process an optional invite token (create Membership) and preserve invite flow."""
    # persist cookie for client-side convenience
    resp = RedirectResponse('/', status_code=303)
    resp.set_cookie('user_role', role, max_age=30*24*60*60, path='/')
    resp.set_cookie('role_selected', 'true', max_age=30*24*60*60, path='/')
    # persist to DB if logged-in
    try:
        if current_user:
            with Session(engine) as session:
                u = session.get(User, current_user.id)
                if u:
                    u.site_role = role
                    session.add(u)
                    session.commit()
    except Exception:
        pass

    # process invite token if present (add membership for this user)
    if invite_token and current_user:
        try:
            payload = _verify_invite_token(invite_token)
            if payload and payload.get('email') == current_user.email:
                space_id = payload.get('space_id')
                with Session(engine) as session:
                    exists = session.exec(select(Membership).where((Membership.user_id == current_user.id) & (Membership.space_id == space_id))).first()
                    if not exists:
                        m = Membership(user_id=current_user.id, classroom_id=space_id, space_id=space_id, role='student')
                        session.add(m)
                        try:
                            cm = SpaceMessage(space_id=space_id, sender_id=current_user.id, content=f"[system] {current_user.username} joined the space.")
                            session.add(cm)
                        except Exception:
                            pass
                        session.commit()
        except Exception:
            pass

    return resp


@app.get('/choose-role', response_class=HTMLResponse)
def choose_role_get(request: Request, current_user: Optional[User] = Depends(get_current_user)):
    """Render a server-side role selection page for new users."""
    csrf = None
    try:
        csrf = request.cookies.get('csrf_token')
    except Exception:
        csrf = None
    return templates.TemplateResponse('choose_role.html', {'request': request, 'csrf_token': csrf, 'current_user': current_user})


@app.get('/account/settings', response_class=HTMLResponse)
def account_settings_get(request: Request, current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse('account_settings.html', {'request': request, 'current_user': current_user})


@app.post('/account/settings')
def account_settings_post(request: Request, role: str = Form(...), current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        u = session.get(User, current_user.id)
        if not u:
            raise HTTPException(status_code=404, detail='User not found')
        u.site_role = role
        session.add(u)
        session.commit()
    resp = RedirectResponse('/account/settings', status_code=303)
    resp.set_cookie('user_role', role, max_age=30*24*60*60, path='/')
    return resp
    return resp


# In-memory + Redis-backed cache for category counts
_category_counts_cache = {}
_category_counts_cache_time = 0
_category_counts_cache_ttl = int(os.getenv('CATEGORY_COUNTS_TTL', '3600'))


def get_category_counts(force: bool = False):
    """Return a mapping category_name -> count of presentations.

    Uses an in-memory cache and optional Redis backing. If `force` is True,
    the counts are recalculated from the database.
    """
    global _category_counts_cache, _category_counts_cache_time
    now = int(time.time())
    redis_url = os.getenv('REDIS_URL')

    # fast path: in-memory cache
    try:
        if not force and _category_counts_cache and (now - int(_category_counts_cache_time) < int(_category_counts_cache_ttl)):
            return _category_counts_cache
    except Exception:
        pass

    # try redis
    try:
        if _redis and redis_url and not force:
            rc = _redis.from_url(redis_url)
            raw = rc.get('category_counts')
            if raw:
                try:
                    _category_counts_cache = json.loads(raw)
                    _category_counts_cache_time = now
                    return _category_counts_cache
                except Exception:
                    pass
    except Exception:
        pass

    # compute from DB
    out = {}
    try:
        with Session(engine) as session:
            pres = session.exec(select(Presentation)).all()
            for p in pres:
                try:
                    cname = getattr(getattr(p, 'category', None), 'name', None) or getattr(p, 'category_name', None)
                    if cname:
                        out[cname] = out.get(cname, 0) + 1
                except Exception:
                    continue
    except Exception:
        out = {}

    # populate caches
    try:
        _category_counts_cache = {k: int(v) for k, v in out.items()}
        _category_counts_cache_time = now
    except Exception:
        _category_counts_cache = out

    try:
        if _redis and redis_url:
            rc = _redis.from_url(redis_url)
            rc.set('category_counts', json.dumps(_category_counts_cache), ex=_category_counts_cache_ttl)
    except Exception:
        pass

    return _category_counts_cache or {}


def get_available_category_names() -> List[str]:
    """Return merged category names from DB + data/categories.json + builtin fallback."""
    db_cats: List[str] = []
    try:
        with Session(engine) as session:
            rows = session.exec(select(Category).order_by(Category.name)).all()
            for c in rows:
                name = getattr(c, 'name', None)
                if name:
                    db_cats.append(str(name).strip())
    except Exception:
        db_cats = []

    builtin: List[str] = []
    try:
        data_path = Path(__file__).parent.parent / 'data' / 'categories.json'
        if data_path.exists():
            with open(data_path, 'r', encoding='utf-8') as fh:
                candidates = json.load(fh)
                if isinstance(candidates, list):
                    builtin = [str(x).strip() for x in candidates if str(x).strip()]
    except Exception:
        builtin = []

    if not builtin:
        builtin = [
            'Business', 'Technology', 'Design', 'Marketing', 'Education', 'Science', 'Art', 'Finance',
            'Health', 'Politics', 'Society', 'Travel', 'Sports', 'Programming', 'Machine Learning',
            'Data Science', 'Startups', 'Product', 'Leadership', 'Psychology', 'History', 'Culture',
            'Photography', 'Film', 'Music', 'Environment', 'Law', 'Economics', 'Mathematics', 'Philosophy'
        ]

    seen = set()
    merged: List[str] = []
    for name in (db_cats + builtin):
        key = (name or '').strip()
        if not key:
            continue
        lowered = key.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        merged.append(key)

    try:
        merged.sort(key=lambda s: s.lower())
    except Exception:
        pass

    return merged



import asyncio
# Simple in-memory WebSocket connection manager
class WebSocketManager:
    def __init__(self):
        # Map user_id -> set of WebSocket connections
        self._conns: Dict[int, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, user_id: int, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self._conns.setdefault(int(user_id), set()).add(websocket)

    async def disconnect(self, user_id: int, websocket: WebSocket):
        async with self._lock:
            conns = self._conns.get(int(user_id))
            if not conns:
                return
            try:
                conns.discard(websocket)
            except Exception:
                pass
            if not conns:
                self._conns.pop(int(user_id), None)

    async def send_personal(self, user_id: int, payload: dict):
        conns = list(self._conns.get(int(user_id), []))
        for ws in conns:
            try:
                await ws.send_json(payload)
            except Exception:
                # best-effort: ignore failures
                try:
                    await self.disconnect(user_id, ws)
                except Exception:
                    pass

    async def broadcast_presence(self, user_id: int, online: bool):
        # Notify all connected users about user's presence change
        payload = {"type": "presence", "user_id": int(user_id), "online": bool(online)}
        # iterate over snapshot of connections
        for uid, conns in list(self._conns.items()):
            if uid == int(user_id):
                continue
            for ws in list(conns):
                try:
                    await ws.send_json(payload)
                except Exception:
                    try:
                        await self.disconnect(uid, ws)
                    except Exception:
                        pass

    def is_online(self, user_id: int) -> bool:
        return bool(self._conns.get(int(user_id)))


# global manager instance
manager = WebSocketManager()


class VideoSignalingState:
    def __init__(self):
        self.user_sockets: Dict[int, Set[WebSocket]] = {}
        self.socket_users: Dict[WebSocket, int] = {}
        self.room_users: Dict[int, Set[int]] = {}
        self.user_rooms: Dict[int, Set[int]] = {}
        self.meetings: Dict[int, Dict[str, Any]] = {}

    def register_socket(self, user_id: int, websocket: WebSocket) -> None:
        self.socket_users[websocket] = int(user_id)
        self.user_sockets.setdefault(int(user_id), set()).add(websocket)

    def unregister_socket(self, websocket: WebSocket) -> Optional[int]:
        user_id = self.socket_users.pop(websocket, None)
        if user_id is None:
            return None
        conns = self.user_sockets.get(int(user_id))
        if conns:
            conns.discard(websocket)
            if not conns:
                self.user_sockets.pop(int(user_id), None)
        return int(user_id)

    def join_room(self, user_id: int, space_id: int) -> None:
        self.room_users.setdefault(int(space_id), set()).add(int(user_id))
        self.user_rooms.setdefault(int(user_id), set()).add(int(space_id))

    def leave_room(self, user_id: int, space_id: int) -> None:
        room_set = self.room_users.get(int(space_id))
        if room_set:
            room_set.discard(int(user_id))
            if not room_set:
                self.room_users.pop(int(space_id), None)
        user_set = self.user_rooms.get(int(user_id))
        if user_set:
            user_set.discard(int(space_id))
            if not user_set:
                self.user_rooms.pop(int(user_id), None)

    def is_meeting_active(self, space_id: int) -> bool:
        return bool(self.meetings.get(int(space_id)))

    def start_meeting(self, space_id: int, host_id: int) -> None:
        self.meetings[int(space_id)] = {
            'host_id': int(host_id),
            'participants': set([int(host_id)]),
        }

    def end_meeting(self, space_id: int) -> None:
        self.meetings.pop(int(space_id), None)


video_state = VideoSignalingState()
# Allow CORS for dev; enables OPTIONS preflight responses and methods from the browser
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"],
    allow_headers=["*"],
)
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent.parent / "static")),
    name="static",
)
# Serve uploaded files under /media
app.mount(
    "/media",
    StaticFiles(directory=str(Path(UPLOAD_DIR).resolve())),
    name="media",
)
# Serve uploaded files under /uploads
@app.post('/api/presentations/{presentation_id}/ai/slide')
def ai_slide_action(presentation_id: int, payload: dict = Body(...), current_user: User = Depends(get_current_user)):
    """Handle slide-level AI actions. Expects JSON with:
       - action: rephrase|simplify|key_points|elaborate|adapt_for_audience
       - slide_id: opaque id (client provides)
       - slide_text: the text to operate on (required)
       - audience: optional (required only for adapt_for_audience)

    This endpoint MUST only operate on the provided slide_text and must not
    fetch or infer content from other slides or the presentation.
    """
    # basic validation
    with Session(engine) as session:
        p = session.get(Presentation, presentation_id)
        if not p:
            raise HTTPException(status_code=404, detail='presentation not found')
        presentation_title = (p.title or '').strip()

    action = (payload.get('action') or '').strip().lower()
    slide_id = payload.get('slide_id')
    slide_text = (payload.get('slide_text') or '').strip()
    audience = (payload.get('audience') or '').strip()
    user_input = (payload.get('user_input') or '').strip()
    history = payload.get('history') or []

    allowed = {
        'rephrase', 'simplify', 'key_points', 'elaborate', 'adapt_for_audience',
        'explain_like_12', 'real_world_example', 'analogy', 'check_understanding', 'glossary',
        'custom'
    }
    if action not in allowed:
        raise HTTPException(status_code=400, detail='invalid action')
    if action != 'custom' and not slide_text:
        raise HTTPException(status_code=400, detail='slide_text is required')
    if action == 'custom' and (not slide_text) and (not user_input):
        raise HTTPException(status_code=400, detail='user_input or slide_text is required')
    if action == 'adapt_for_audience' and not audience:
        raise HTTPException(status_code=400, detail='audience is required for adapt_for_audience')

    # in-memory cache (reduce duplicate calls)
    try:
        cache_key_base = f"{presentation_id}|{action}|{audience}|{user_input}|{slide_text}"
        cache_key = hashlib.sha256(cache_key_base.encode('utf-8')).hexdigest()
        cached = _ai_cache_get(cache_key)
        if cached:
            return JSONResponse({'result': cached, 'cached': True})
    except Exception:
        cache_key = None

    # per-user rate limiting
    retry_after = _ai_rate_limit_check(current_user.id)
    if retry_after:
        raise HTTPException(status_code=429, detail='Rate limit exceeded. Please try again shortly.', headers={'Retry-After': str(retry_after)})

    # global inflight cap to keep AI responsive under load
    global _ai_inflight
    with _ai_lock:
        if _ai_inflight >= AI_GLOBAL_MAX_INFLIGHT:
            raise HTTPException(status_code=429, detail='AI is busy. Please retry in a few seconds.', headers={'Retry-After': '2'})
        _ai_inflight += 1

    if action == 'custom' and (not slide_text):
        clean = (user_input or '').strip()
        if clean:
            low = clean.lower()
            if low in {'hi', 'hello', 'hey', 'yo', 'sup'}:
                return JSONResponse({'result': 'Hello! How can I help you? Feel free to ask anything about this presentation.'})
            words = [w for w in clean.split() if w]
            if len(words) <= 3:
                return JSONResponse({'result': "I'm not sure what you mean. Could you clarify or add a bit more detail?"})

    # If slide_text is too short, try to extract text from the PDF for better AI quality.
    image_b64 = None
    pdf_path = None
    if action != 'custom':
        try:
            if isinstance(slide_id, int) and (len(slide_text) < 40):
                # Prefer converted PDF if available
                try:
                    with Session(engine) as session:
                        job = session.exec(
                            select(ConversionJob)
                            .where(ConversionJob.presentation_id == presentation_id)
                            .order_by(ConversionJob.created_at.desc())
                        ).first()
                        if job and job.result:
                            cand = Path(UPLOAD_DIR) / job.result
                            if cand.exists():
                                pdf_path = cand
                except Exception:
                    pdf_path = None

                # Fallback to original PDF
                if not pdf_path:
                    try:
                        if getattr(p, 'filename', None):
                            src = Path(UPLOAD_DIR) / p.filename
                            if src.exists() and src.suffix.lower() == '.pdf':
                                pdf_path = src
                    except Exception:
                        pdf_path = None

                if pdf_path and fitz is not None:
                    try:
                        doc = fitz.open(str(pdf_path))
                        if slide_id < doc.page_count:
                            page = doc.load_page(slide_id)
                            extracted = page.get_text("text").strip()
                            if extracted and len(extracted) > len(slide_text):
                                slide_text = extracted
                            # also render an image for vision fallback
                            try:
                                mat = fitz.Matrix(2.0, 2.0)
                                pix = page.get_pixmap(matrix=mat)
                                import base64
                                image_b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")
                            except Exception:
                                image_b64 = None
                        doc.close()
                    except Exception:
                        pass
        except Exception:
            pass

    provider = get_ai_provider()

    # build a strict prompt that instructs the model to only use the provided slide text
    title_hint = f"\nPresentation title: {presentation_title}" if presentation_title else ""
    if action == 'rephrase':
        user_instruction = (
            "Rewrite the slide text with substantially different wording while preserving meaning. "
            "Do NOT copy phrases; restructure sentences and vocabulary. "
            "Keep it professional and clear. If the text is mostly contact details, summarize without listing URLs or phone numbers."
            f"{title_hint}\n\nSlide text:\n" + slide_text
        )
    elif action == 'simplify':
        user_instruction = (
            "Simplify the slide text for a general audience. Use plain language, short sentences, and remove jargon. "
            "Return 2–5 short bullet points. Reword significantly; do NOT mirror the original phrasing. "
            "If the text is mostly contact details, give a one-line summary without listing URLs or phone numbers."
            f"{title_hint}\n\nSlide text:\n" + slide_text
        )
    elif action == 'key_points':
        user_instruction = (
            "Extract the key points as concise bullet points (4–8 bullets). "
            "Use your own words; do not copy sentences. Return bullets only."
            f"{title_hint}\n\nSlide text:\n" + slide_text
        )
    elif action == 'elaborate':
        user_instruction = (
            "Expand the slide text with brief explanations and one practical example. "
            "Keep it tied to the input; do NOT add unrelated info."
            f"{title_hint}\n\nSlide text:\n" + slide_text
        )
    elif action == 'explain_like_12':
        user_instruction = (
            "Explain the slide text as if to a 12‑year‑old. Use simple words and short sentences. "
            "Avoid copying the original phrasing."
            f"{title_hint}\n\nSlide text:\n" + slide_text
        )
    elif action == 'real_world_example':
        user_instruction = (
            "Provide 1–2 concrete real‑world examples that illustrate the slide text. "
            "Keep it practical and brief."
            f"{title_hint}\n\nSlide text:\n" + slide_text
        )
    elif action == 'analogy':
        user_instruction = (
            "Explain the slide text using a clear analogy. "
            "Keep it short and easy to grasp."
            f"{title_hint}\n\nSlide text:\n" + slide_text
        )
    elif action == 'check_understanding':
        user_instruction = (
            "Ask 3–5 short questions to check understanding of the slide text. "
            "Return only the questions."
            f"{title_hint}\n\nSlide text:\n" + slide_text
        )
    elif action == 'glossary':
        user_instruction = (
            "List 4–8 key terms from the slide text with brief definitions. "
            "Format as bullets: term — definition."
            f"{title_hint}\n\nSlide text:\n" + slide_text
        )
    elif action == 'custom':
        if slide_text:
            user_instruction = (
                "Follow the user's instruction using only the provided slide text and title context. "
                "Do not add unrelated information; rephrase as needed."
                f"{title_hint}\n\nUser instruction:\n" + (user_input or '') + "\n\nSlide text:\n" + slide_text
            )
        else:
            user_instruction = (
                "Have a natural, helpful conversation. Respond directly to the user's message. "
                "Be concise but friendly."
                f"{title_hint}\n\nUser message:\n" + (user_input or '')
            )
    else:  # adapt_for_audience
        user_instruction = (
            f"Rewrite the provided slide text to suit the following audience: {audience}. "
            "Keep content consistent with the original slide and do NOT introduce unrelated information."
            f"{title_hint}\n\nSlide text:\n" + slide_text
        )

    system_instruction = (
        "You are a friendly, highly capable assistant like ChatGPT. "
        "Never echo or paraphrase the user's message as your entire answer. "
        "If the user message is unclear or too short, respond with a brief request for clarification (one short question). "
        "Always produce a transformed, higher‑quality response (not a copy). "
        "Use only the provided slide text and title context. Return only the answer with no labels, no meta commentary."
    )
    user_message = {"role": "user", "content": user_instruction}
    if provider == "openai" and image_b64 and len(slide_text) < 120:
        user_message = {
            "role": "user",
            "content": [
                {"type": "text", "text": user_instruction},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ],
        }

    messages = [
        {"role": "system", "content": system_instruction},
    ]
    if isinstance(history, list):
        for item in history[-16:]:
            try:
                role = (item.get('role') or '').strip().lower()
                content = (item.get('content') or '').strip()
                if role in ('user', 'assistant') and content:
                    messages.append({"role": role, "content": content[:1500]})
            except Exception:
                continue
    messages.append(user_message)

    result_text = ''
    try:
        max_attempts = 5
        last_err = None
        for attempt in range(max_attempts):
            try:
                model = os.getenv("OPENAI_MODEL", "gpt-4o") if provider == "openai" else os.getenv("OLLAMA_MODEL", "llama3.2:3b")
                result_text = chat_completion(messages, model=model, max_tokens=1400, temperature=0.7)
                if result_text:
                    break
            except Exception as e:
                last_err = str(e)
                if attempt < max_attempts - 1:
                    time.sleep(min(2 ** (attempt + 1), 10))
                    continue
        if not result_text:
            raise HTTPException(status_code=502, detail=f'AI provider error: {last_err or "no response"}')
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'AI request failed: {e}')
    finally:
        with _ai_lock:
            _ai_inflight = max(0, _ai_inflight - 1)

    # Return only the transformed text
    if cache_key and result_text:
        try:
            _ai_cache_set(cache_key, result_text)
        except Exception:
            pass
    return JSONResponse({'result': result_text})


@app.post('/api/presentations/{presentation_id}/ai/slide/replace')
def ai_slide_replace(presentation_id: int, payload: dict = Body(...), current_user: User = Depends(get_current_user)):
    """Replace a single slide image with provided text rendered as an image.
    Payload: { index: int, text: str }
    """
    index = payload.get('index')
    text = (payload.get('text') or '').strip()
    if index is None or not isinstance(index, int):
        raise HTTPException(status_code=400, detail='index (int) is required')
    if not text:
        raise HTTPException(status_code=400, detail='text is required')

    with Session(engine) as session:
        p = session.get(Presentation, presentation_id)
        if not p:
            raise HTTPException(status_code=404, detail='presentation not found')
        if p.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='only the presentation owner can modify slides')

    from pathlib import Path
    thumbs_dir = Path(UPLOAD_DIR) / 'thumbs' / str(presentation_id)
    if not thumbs_dir.exists():
        raise HTTPException(status_code=404, detail='slides not available')

    target = thumbs_dir / f'slide_{index}.png'
    if not target.exists():
        raise HTTPException(status_code=404, detail='slide not found')

    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        raise HTTPException(status_code=500, detail='Pillow is required for slide image updates')

    try:
        with Image.open(str(target)) as src:
            w, h = src.size
    except Exception:
        w, h = (1600, 900)

    img = Image.new('RGB', (w, h), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype('arial.ttf', 28)
    except Exception:
        font = ImageFont.load_default()

    margin = 40
    max_width = w - margin * 2
    lines = []
    cur = ''

    def _measure(text_val: str):
        try:
            bbox = draw.textbbox((0, 0), text_val, font=font)
            return bbox[2] - bbox[0], bbox[3] - bbox[1]
        except Exception:
            try:
                return draw.textsize(text_val, font=font)
            except Exception:
                return (len(text_val) * 10, 18)

    for word in text.split():
        test = (cur + ' ' + word).strip()
        if _measure(test)[0] <= max_width:
            cur = test
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)

    y = margin
    line_height = _measure('Ay')[1] + 6
    for line in lines:
        if y + line_height > h - margin:
            break
        draw.text((margin, y), line, fill=(0, 0, 0), font=font)
        y += line_height

    try:
        img.save(str(target), format='PNG')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'failed to save updated slide image: {e}')

    return JSONResponse({'ok': True, 'index': index, 'url': f'/media/thumbs/{presentation_id}/slide_{index}.png'})


@app.post('/api/presentations/{presentation_id}/ai/slide/insert')
def ai_slide_insert(presentation_id: int, payload: dict = Body(...), current_user: User = Depends(get_current_user)):
    """Insert a new slide after the given index. Payload: { after_index: int, text: str }"""
    after_index = payload.get('after_index')
    text = (payload.get('text') or '').strip()
    if after_index is None or not isinstance(after_index, int):
        raise HTTPException(status_code=400, detail='after_index (int) is required')
    if not text:
        raise HTTPException(status_code=400, detail='text is required')

    with Session(engine) as session:
        p = session.get(Presentation, presentation_id)
        if not p:
            raise HTTPException(status_code=404, detail='presentation not found')
        if p.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail='only the presentation owner can modify slides')

    from pathlib import Path
    thumbs_dir = Path(UPLOAD_DIR) / 'thumbs' / str(presentation_id)
    thumbs_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(thumbs_dir.glob('slide_*.png'))
    indices = sorted([int(f.stem.split('_')[1]) for f in files]) if files else []

    try:
        if indices:
            for i in range(max(indices), after_index, -1):
                src = thumbs_dir / f'slide_{i}.png'
                dst = thumbs_dir / f'slide_{i+1}.png'
                if src.exists():
                    src.rename(dst)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'failed to shift slide files: {e}')

    new_index = after_index + 1
    target = thumbs_dir / f'slide_{new_index}.png'

    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        raise HTTPException(status_code=500, detail='Pillow is required for slide image updates')

    # try to sample size from adjacent slide
    sample = thumbs_dir / f'slide_{new_index+1}.png'
    if not sample.exists():
        sample = thumbs_dir / f'slide_{new_index-1}.png'
    try:
        if sample.exists():
            with Image.open(str(sample)) as src:
                w, h = src.size
        else:
            w, h = (1600, 900)
    except Exception:
        w, h = (1600, 900)

    img = Image.new('RGB', (w, h), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype('arial.ttf', 28)
    except Exception:
        font = ImageFont.load_default()

    margin = 40
    max_width = w - margin * 2
    lines = []
    cur = ''

    def _measure(text_val: str):
        try:
            bbox = draw.textbbox((0, 0), text_val, font=font)
            return bbox[2] - bbox[0], bbox[3] - bbox[1]
        except Exception:
            try:
                return draw.textsize(text_val, font=font)
            except Exception:
                return (len(text_val) * 10, 18)

    for word in text.split():
        test = (cur + ' ' + word).strip()
        if _measure(test)[0] <= max_width:
            cur = test
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)

    y = margin
    line_height = _measure('Ay')[1] + 6
    for line in lines:
        if y + line_height > h - margin:
            break
        draw.text((margin, y), line, fill=(0, 0, 0), font=font)
        y += line_height

    try:
        img.save(str(target), format='PNG')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'failed to save inserted slide image: {e}')

    return JSONResponse({'ok': True, 'inserted_index': new_index, 'url': f'/media/thumbs/{presentation_id}/slide_{new_index}.png'})

    # Student flow: include both confirmed student memberships and pending classroom invitations
    with Session(engine) as session:
        mems = session.exec(
            select(Membership).where(
                (Membership.user_id == current_user.id)
                & (Membership.role == 'student')
            )
        ).all()
        membership_ids = {m.classroom_id for m in mems} if mems else set()

        # invitations (e.g., invite_by_username creates a Notification)
        invited = session.exec(
            select(Notification).where(
                (Notification.recipient_id == current_user.id)
                & (Notification.verb == 'classroom_invite')
            )
        ).all()
        invited_ids = {getattr(n, 'target_id', None) for n in invited if getattr(n, 'target_id', None) is not None}

        classroom_ids = sorted(list(membership_ids.union(invited_ids)))
        rows = session.exec(select(Classroom).where(Classroom.id.in_(classroom_ids))).all()
        # attach teacher names for each classroom
        classrooms = []
        for c in rows:
            teacher_mems = session.exec(
                select(Membership).where(
                    (Membership.classroom_id == c.id)
                    & (Membership.role.in_(['teacher', 'admin']))
                )
            ).all()
            teacher_ids = sorted({m.user_id for m in teacher_mems})
            teachers = []
            for tid in teacher_ids:
                u = session.get(User, tid)
                if u:
                    teachers.append({'id': u.id, 'username': getattr(u, 'username', None)})
            classrooms.append(
                {
                    'id': c.id,
                    'name': getattr(c, 'name', ''),
                    'school_id': getattr(c, 'school_id', None),
                    'teachers': teachers,
                }
            )
    return templates.TemplateResponse('student_classrooms.html', {'request': request, 'current_user': current_user, 'classrooms': classrooms})


@app.get('/my/materials', response_class=HTMLResponse)
def student_materials(request: Request, current_user: User = Depends(get_current_user)):
    if getattr(current_user, 'site_role', None) in ('teacher', 'individual'):
        return RedirectResponse('/teacher', status_code=303)

    with Session(engine) as session:
        memberships = session.exec(
            select(Membership).where(
                (Membership.user_id == current_user.id)
                & (Membership.role == 'student')
            )
        ).all()
        invited = session.exec(
            select(Notification).where(
                (Notification.recipient_id == current_user.id)
                & (Notification.verb == 'classroom_invite')
            )
        ).all()

        classroom_ids = {
            getattr(m, 'classroom_id', None)
            for m in memberships
            if getattr(m, 'classroom_id', None) is not None
        }
        invited_classroom_ids = {
            getattr(n, 'target_id', None)
            for n in invited
            if getattr(n, 'target_id', None) is not None
        }
        classroom_ids = sorted(classroom_ids.union(invited_classroom_ids))

        presentations = []
        library_items = []
        assignments = []
        assignment_status_by_id = {}
        classrooms_by_id = {}
        teachers_by_id = {}

        if classroom_ids:
            teacher_mems = session.exec(
                select(Membership).where(
                    (Membership.classroom_id.in_(classroom_ids))
                    & (Membership.role.in_(['teacher', 'admin']))
                )
            ).all()
            teacher_ids = sorted({m.user_id for m in teacher_mems if m.user_id != current_user.id})
            if teacher_ids:
                teachers = session.exec(select(User).where(User.id.in_(teacher_ids))).all()
                teachers_by_id = {u.id: u for u in teachers}

            library_items = session.exec(
                select(LibraryItem)
                .where(LibraryItem.classroom_id.in_(classroom_ids))
                .order_by(desc(LibraryItem.created_at))
            ).all()

            shared_pids = sorted({li.presentation_id for li in library_items if getattr(li, 'presentation_id', None)})
            if shared_pids:
                presentations = session.exec(
                    select(Presentation)
                    .where(Presentation.id.in_(shared_pids))
                    .order_by(desc(Presentation.created_at))
                ).all()

            classrooms = session.exec(select(Classroom).where(Classroom.id.in_(classroom_ids))).all()
            classrooms_by_id = {c.id: c for c in classrooms}

            assignments = session.exec(
                select(Assignment)
                .where(Assignment.classroom_id.in_(classroom_ids))
                .order_by(Assignment.due_date.is_(None), Assignment.due_date, desc(Assignment.created_at))
            ).all()

            if assignments:
                assignment_ids = [a.id for a in assignments if getattr(a, 'id', None) is not None]
                statuses = session.exec(
                    select(AssignmentStatus).where(
                        (AssignmentStatus.assignment_id.in_(assignment_ids))
                        & (AssignmentStatus.student_id == current_user.id)
                    )
                ).all()
                assignment_status_by_id = {s.assignment_id: s for s in statuses}

    return templates.TemplateResponse(
        'student_materials.html',
        {
            'request': request,
            'current_user': current_user,
            'presentations': presentations,
            'library_items': library_items,
            'teachers_by_id': teachers_by_id,
            'assignments': assignments,
            'assignment_status_by_id': assignment_status_by_id,
            'classrooms_by_id': classrooms_by_id,
        },
    )


@app.get('/teacher', response_class=HTMLResponse)
def teacher_dashboard(request: Request, current_user: User = Depends(get_current_user)):
    """Simple hub for teachers/admins: list their classrooms and key tools."""
    from sqlmodel import select
    # find classrooms where the user is a teacher or admin
    with Session(engine) as session:
        memberships = session.exec(
            select(Membership).where(
                (Membership.user_id == current_user.id)
                & (Membership.role.in_(['teacher', 'admin']))
            )
        ).all()
        classroom_ids = sorted({m.classroom_id for m in memberships})
        classrooms = []
        for cid in classroom_ids:
            c = session.get(Classroom, cid)
            if not c:
                continue
            # basic counts for quick overview
            student_count = session.exec(
                select(func.count(Membership.id)).where(
                    (Membership.classroom_id == cid)
                    & (Membership.role == 'student')
                )
            ).first() or 0
            assignment_count = session.exec(
                select(func.count(Assignment.id)).where(Assignment.classroom_id == cid)
            ).first() or 0
            submission_count = session.exec(
                select(func.count(Submission.id)).where(
                    Submission.assignment_id.in_(
                        select(Assignment.id).where(Assignment.classroom_id == cid)
                    )
                )
            ).first() or 0
            classrooms.append(
                {
                    'id': cid,
                    'name': getattr(c, 'name', ''),
                    'school_id': getattr(c, 'school_id', None),
                    'student_count': int(student_count),
                    'assignment_count': int(assignment_count),
                    'submission_count': int(submission_count),
                }
            )
    # restrict to users who actually teach somewhere or have site_role teacher
    if not classrooms and getattr(current_user, 'site_role', None) != 'teacher':
        # nothing to show; send them home
        return RedirectResponse('/', status_code=303)
    return templates.TemplateResponse(
        'teacher_dashboard.html',
        {
            'request': request,
            'current_user': current_user,
            'classrooms': classrooms,
        },
    )


@app.get('/teacher/classrooms/new', response_class=HTMLResponse)
def teacher_create_classroom_get(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Show a simple form for teachers to create a new classroom.

    Classrooms are lightweight groups (no school selection), useful as
    group spaces / group chats for a set of students.
    """
    if getattr(current_user, 'site_role', None) not in ('teacher', 'individual'):
        raise HTTPException(status_code=403, detail='Only teachers can create classrooms')
    csrf = None
    try:
        csrf = request.cookies.get('csrf_token') if hasattr(request, 'cookies') else None
    except Exception:
        csrf = None
    return templates.TemplateResponse(
        'teacher_create_classroom.html',
        {
            'request': request,
            'current_user': current_user,
            'csrf_token': csrf,
        },
    )


@app.post('/teacher/classrooms/new')
def teacher_create_classroom_post(
    request: Request,
    name: str = Form(...),
    code: Optional[str] = Form(None),
    csrf_token: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
):
    """Create a new classroom and enroll the teacher as its owner.

    School is optional; this behaves like creating a group space.
    """
    if getattr(current_user, 'site_role', None) not in ('teacher', 'individual'):
        raise HTTPException(status_code=403, detail='Only teachers can create classrooms')
    validate_csrf(request, csrf_token)
    name_clean = (name or '').strip()
    if not name_clean:
        raise HTTPException(status_code=400, detail='Classroom name is required')
    with Session(engine) as session:
        c = Classroom(school_id=None, name=name_clean, code=(code or None))
        session.add(c)
        session.commit()
        session.refresh(c)
        # Keep the new Space table in sync during the transition.
        try:
            if not session.get(Space, c.id):
                session.add(
                    Space(
                        id=c.id,
                        school_id=getattr(c, 'school_id', None),
                        name=getattr(c, 'name', None),
                        code=getattr(c, 'code', None),
                        created_at=getattr(c, 'created_at', None) or datetime.utcnow(),
                    )
                )
                session.commit()
        except Exception:
            session.rollback()
        membership = Membership(
            user_id=current_user.id,
            classroom_id=c.id,
            space_id=c.id,
            role='teacher',
        )
        session.add(membership)
        session.commit()


@app.get('/teacher/spaces/new', response_class=HTMLResponse)
def teacher_create_space_get(request: Request, current_user: User = Depends(get_current_user)):
    return teacher_create_classroom_get(request=request, current_user=current_user)


@app.post('/teacher/spaces/new')
def teacher_create_space_post(
    request: Request,
    name: str = Form(...),
    code: Optional[str] = Form(None),
    csrf_token: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
):
    return teacher_create_classroom_post(
        request=request,
        name=name,
        code=code,
        csrf_token=csrf_token,
        current_user=current_user,
    )
@app.post('/classrooms/new')
def create_classroom(request: Request, name: str = Form(...), current_user: User = Depends(get_current_user)):
    """Allow teachers to create a new classroom that can host multiple users.

    The creator is stored as a teacher membership for that classroom.
    """
    name_clean = (name or '').strip() or 'New classroom'
    with Session(engine) as session:
        # Only allow users with a teacher role (either site-wide or via any classroom membership)
        is_teacher = getattr(current_user, 'site_role', None) == 'teacher'
        if not is_teacher:
            mem = session.exec(
                select(Membership).where(
                    (Membership.user_id == current_user.id)
                    & (Membership.role.in_(['teacher', 'admin']))
                )
            ).first()
            if mem:
                is_teacher = True
        if not is_teacher:
            raise HTTPException(status_code=403, detail='Only teachers can create classrooms')

        cls = Classroom(name=name_clean)
        session.add(cls)
        session.commit()
        session.refresh(cls)

        # Keep Space table in sync during transition.
        try:
            if not session.get(Space, cls.id):
                session.add(
                    Space(
                        id=cls.id,
                        school_id=getattr(cls, 'school_id', None),
                        name=getattr(cls, 'name', None),
                        code=getattr(cls, 'code', None),
                        created_at=getattr(cls, 'created_at', None) or datetime.utcnow(),
                    )
                )
                session.commit()
        except Exception:
            session.rollback()

        # Ensure creator is recorded as a teacher in this classroom
        existing = session.exec(
            select(Membership).where(
                (Membership.user_id == current_user.id)
                & (Membership.classroom_id == cls.id)
            )
        ).first()
        if not existing:
            m = Membership(user_id=current_user.id, classroom_id=cls.id, space_id=cls.id, role='teacher')
            session.add(m)
            session.commit()

    return RedirectResponse('/teacher', status_code=303)


@app.get('/teachers/{teacher_id}/presentations', response_class=HTMLResponse)
def teacher_presentations(request: Request, teacher_id: int, current_user: Optional[User] = Depends(get_current_user)):
    """Show presentations authored by a teacher. Visible to all users.
    If the current_user is a student, this provides the teacher-only view."""
    with Session(engine) as session:
        teacher = session.get(User, teacher_id)
        if not teacher:
            raise HTTPException(status_code=404, detail='Teacher not found')
        pres = session.exec(select(Presentation).where(Presentation.owner_id == teacher_id).order_by(Presentation.created_at.desc())).all()
    return templates.TemplateResponse('teacher_presentations.html', {'request': request, 'teacher': teacher, 'presentations': pres})


def _make_invite_token(payload: dict) -> str:
    """Create a signed token for invitation payload.
    Token format: base64url(json).base64url(hmac_sha256)
    """
    import json, hmac, hashlib, base64
    secret = os.getenv('SECRET_KEY', 'devsecret')
    raw = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8')
    sig = hmac.new(secret.encode('utf-8'), raw, hashlib.sha256).digest()
    braw = base64.urlsafe_b64encode(raw).decode('utf-8')
    bsig = base64.urlsafe_b64encode(sig).decode('utf-8')
    return f"{braw}.{bsig}"


def _verify_invite_token(token: str, max_age: int = 60 * 60 * 24 * 7):
    import json, hmac, hashlib, base64, time
    secret = os.getenv('SECRET_KEY', 'devsecret')
    try:
        braw, bsig = token.split('.')
        raw = base64.urlsafe_b64decode(braw.encode('utf-8'))
        sig = base64.urlsafe_b64decode(bsig.encode('utf-8'))
        expected = hmac.new(secret.encode('utf-8'), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, sig):
            return None
        payload = json.loads(raw.decode('utf-8'))
        ts = payload.get('ts') or 0
        if int(time.time()) - int(ts) > max_age:
            return None
        return payload
    except Exception:
        return None


@app.post('/invite-student')
def invite_student(
    request: Request,
    classroom_id: int = Form(...),
    email: Optional[str] = Form(None),
    username: Optional[str] = Form(None),
    csrf_token: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
):
    """Teacher/admin invites or adds a student to a classroom.

    - If a username is provided, the user is looked up and added
      directly to the classroom as a student.
    - Otherwise, falls back to the existing email invite flow which
      sends an email with an accept/decline link.
    """
    validate_csrf(request, csrf_token)
    if not (username or email):
        raise HTTPException(status_code=400, detail='username or email is required')
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail='Classroom not found')
        mem = session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.user_id == current_user.id)
                & (Membership.role.in_(['teacher', 'admin']))
            )
        ).first()
        if not mem:
            raise HTTPException(status_code=403, detail='Only teacher/admin can invite')

        # Path 1: invite by username (immediately add to classroom)
        if username:
            target = session.exec(
                select(User).where(User.username == username)
            ).first()
            if not target:
                raise HTTPException(status_code=404, detail='User not found')
            exists = session.exec(
                select(Membership).where(
                    (Membership.classroom_id == classroom_id)
                    & (Membership.user_id == target.id)
                )
            ).first()
            if not exists:
                m = Membership(
                    user_id=target.id,
                    classroom_id=classroom_id,
                    role='student',
                )
                session.add(m)
                # optional: notify user
                try:
                    note = Notification(
                        recipient_id=target.id,
                        actor_id=current_user.id,
                        verb='classroom_invite',
                        target_type='classroom',
                        target_id=classroom_id,
                    )
                    session.add(note)
                except Exception:
                    pass
                session.commit()
            return RedirectResponse(
                f"/classrooms/{classroom_id}/members", status_code=303
            )

        # Path 2: legacy email invite with accept/decline links
        import time

        payload = {
            'inviter_id': current_user.id,
            'classroom_id': classroom_id,
            'email': email,
            'ts': int(time.time()),
        }
        token = _make_invite_token(payload)
        try:
            accept = request.url_for('invitations_respond') + f"?token={token}&action=accept"
            decline = request.url_for('invitations_respond') + f"?token={token}&action=decline"
        except Exception:
            base = str(request.base_url).rstrip('/')
            accept = f"{base}/invitations/respond?token={token}&action=accept"
            decline = f"{base}/invitations/respond?token={token}&action=decline"

        try:
            ctx = {
                'inviter_name': getattr(current_user, 'username', ''),
                'classroom_name': getattr(c, 'name', ''),
                'accept_url': accept,
                'decline_url': decline,
            }
            enqueue_email(
                email,
                'Invitation to join classroom',
                None,
                'emails/invite_student.html',
                ctx,
            )
        except Exception:
            pass
    return RedirectResponse(f"/classrooms/{classroom_id}", status_code=303)


@app.post('/classrooms/{classroom_id}/invite-by-username')
def invite_by_username(classroom_id: int, username: str = Form(...), current_user: User = Depends(get_current_user)):
    """Invite an existing user to a classroom by their username.

    If the user exists, a classroom_invite notification is sent to their
    notification center where they can accept or decline.
    """
    uname = (username or '').strip()
    if not uname:
        return RedirectResponse(f"/classrooms/{classroom_id}/performance", status_code=303)

    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail='Classroom not found')

        # ensure current user is a teacher/admin in this classroom
        mem = session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.user_id == current_user.id)
                & (Membership.role.in_(['teacher', 'admin']))
            )
        ).first()
        if not mem:
            raise HTTPException(status_code=403, detail='Only teacher/admin can invite')

        target = session.exec(select(User).where(User.username == uname)).first()
        if not target:
            # user not found
            return RedirectResponse(f"/classrooms/{classroom_id}/performance?error={quote(uname)}+not+found", status_code=303)
        if target.id == current_user.id:
            return RedirectResponse(f"/classrooms/{classroom_id}/performance?notice=cannot+invite+yourself", status_code=303)

        # avoid sending an invite if already a member
        existing = session.exec(
            select(Membership).where(
                (Membership.user_id == target.id)
                & (Membership.classroom_id == classroom_id)
            )
        ).first()
        if existing:
            return RedirectResponse(f"/classrooms/{classroom_id}/performance?notice={quote(uname)}+is+already+a+member", status_code=303)

        try:
            n = Notification(
                recipient_id=target.id,
                actor_id=current_user.id,
                verb='classroom_invite',
                target_type='classroom',
                target_id=classroom_id,
            )
            session.add(n)
            session.commit()
        except Exception:
            session.rollback()

    return RedirectResponse(f"/classrooms/{classroom_id}/performance?success=Invitation+sent+to+{quote(uname)}", status_code=303)


@app.post('/api/classrooms/{classroom_id}/invite')
def api_invite_by_username(classroom_id: int, payload: dict = Body(...), current_user: User = Depends(get_current_user)):
    """Invite an existing user to a classroom by username (JSON API)."""
    uname = (payload.get("username") or "").strip()
    if not uname:
        raise HTTPException(status_code=400, detail="username is required")

    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail='Classroom not found')

        mem = session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.user_id == current_user.id)
                & (Membership.role.in_(['teacher', 'admin']))
            )
        ).first()
        if not mem:
            raise HTTPException(status_code=403, detail='Only teacher/admin can invite')

        target = session.exec(select(User).where(User.username == uname)).first()
        if not target:
            raise HTTPException(status_code=404, detail='User not found')
        if target.id == current_user.id:
            raise HTTPException(status_code=400, detail='Cannot invite yourself')

        existing = session.exec(
            select(Membership).where(
                (Membership.user_id == target.id)
                & (Membership.classroom_id == classroom_id)
            )
        ).first()
        if existing:
            return JSONResponse({"ok": True, "message": "User is already a member"})

        try:
            n = Notification(
                recipient_id=target.id,
                actor_id=current_user.id,
                verb='classroom_invite',
                target_type='classroom',
                target_id=classroom_id,
            )
            session.add(n)
            session.commit()
        except Exception:
            session.rollback()
            raise HTTPException(status_code=500, detail='Failed to send invite')

    return JSONResponse({"ok": True, "message": f"Invitation sent to {uname}"})


@app.post('/api/classrooms/join')
def join_classroom_by_code(payload: dict = Body(...), current_user: User = Depends(get_current_user)):
    """Join a classroom using a join code."""
    code = (payload.get("code") or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="code is required")
    with Session(engine) as session:
        c = session.exec(select(Classroom).where(Classroom.code == code)).first()
        if not c:
            raise HTTPException(status_code=404, detail="Classroom not found")
        exists = session.exec(
            select(Membership).where(
                (Membership.user_id == current_user.id)
                & (Membership.classroom_id == c.id)
            )
        ).first()
        if exists:
            return JSONResponse({"ok": True, "classroom_id": c.id, "message": "Already a member"})
        m = Membership(user_id=current_user.id, classroom_id=c.id, role='student')
        session.add(m)
        try:
            cm = ClassroomMessage(classroom_id=c.id, sender_id=current_user.id, content=f"[system] {current_user.username} joined the classroom.")
            session.add(cm)
        except Exception:
            pass
        session.commit()
    return JSONResponse({"ok": True, "classroom_id": c.id, "message": "Joined classroom"})


@app.post('/api/spaces/join')
def join_space_by_code(payload: dict = Body(...), current_user: User = Depends(get_current_user)):
    """Join a space using a join code."""
    code = (payload.get("code") or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="code is required")
    with Session(engine) as session:
        s = session.exec(select(Space).where(Space.code == code)).first()
        if not s:
            # If the classroom->space sync hasn't happened yet, fall back to classroom.
            c = session.exec(select(Classroom).where(Classroom.code == code)).first()
            if not c:
                raise HTTPException(status_code=404, detail="Space not found")
            s = session.get(Space, c.id)
            if not s:
                s = Space(
                    id=c.id,
                    school_id=getattr(c, 'school_id', None),
                    name=getattr(c, 'name', None),
                    code=getattr(c, 'code', None),
                    created_at=getattr(c, 'created_at', None) or datetime.utcnow(),
                )
                session.add(s)
                session.commit()
                session.refresh(s)
        exists = session.exec(
            select(Membership).where(
                (Membership.user_id == current_user.id)
                & (Membership.space_id == s.id)
            )
        ).first()
        if exists:
            return JSONResponse({"ok": True, "space_id": s.id, "message": "Already a member"})

        # membership.space_id exists via migration bridge; set classroom_id only if it exists
        m = Membership(user_id=current_user.id, classroom_id=s.id, space_id=s.id, role='student')
        session.add(m)
        try:
            sm = SpaceMessage(space_id=s.id, sender_id=current_user.id, content=f"[system] {current_user.username} joined the space.")
            session.add(sm)
        except Exception:
            pass
        session.commit()
    return JSONResponse({"ok": True, "space_id": s.id, "message": "Joined space"})


def _generate_classroom_code() -> str:
    # 6-char alphanumeric code
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(6))


@app.get('/api/classrooms/{classroom_id}/code')
def get_classroom_code(classroom_id: int, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail='Classroom not found')
        mem = session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.user_id == current_user.id)
                & (Membership.role.in_(['teacher', 'admin']))
            )
        ).first()
        if not mem:
            raise HTTPException(status_code=403, detail='Only teacher/admin can view code')
        if not c.code:
            c.code = _generate_classroom_code()
            session.add(c)
            session.commit()
            session.refresh(c)
        return JSONResponse({"ok": True, "code": c.code})


@app.post('/api/classrooms/{classroom_id}/code/regenerate')
def regenerate_classroom_code(classroom_id: int, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail='Classroom not found')
        mem = session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.user_id == current_user.id)
                & (Membership.role.in_(['teacher', 'admin']))
            )
        ).first()
        if not mem:
            raise HTTPException(status_code=403, detail='Only teacher/admin can regenerate code')
        c.code = _generate_classroom_code()
        session.add(c)
        session.commit()
        session.refresh(c)
        return JSONResponse({"ok": True, "code": c.code})


@app.get('/api/spaces/{space_id}/code')
def get_space_code(space_id: int, current_user: User = Depends(get_current_user)):
    """Get (and lazily create) the join code for a space.

    Compatibility notes:
    - During migration, space ids mirror classroom ids.
    - We keep `classroom.code` in sync when possible so legacy clients still work.
    """
    with Session(engine) as session:
        s = session.get(Space, space_id)
        if not s:
            # fallback: create Space from legacy Classroom if present
            c = session.get(Classroom, space_id)
            if not c:
                raise HTTPException(status_code=404, detail='Space not found')
            s = Space(
                id=c.id,
                school_id=getattr(c, 'school_id', None),
                name=getattr(c, 'name', None),
                code=getattr(c, 'code', None),
                created_at=getattr(c, 'created_at', None) or datetime.utcnow(),
            )
            session.add(s)
            session.commit()
            session.refresh(s)

        mem = session.exec(
            select(Membership).where(
                (Membership.user_id == current_user.id)
                & (
                    (Membership.space_id == space_id)
                    | (Membership.classroom_id == space_id)
                )
                & (Membership.role.in_(['teacher', 'admin']))
            )
        ).first()
        if not mem:
            raise HTTPException(status_code=403, detail='Only teacher/admin can view code')

        if not getattr(s, 'code', None):
            s.code = _generate_classroom_code()
            session.add(s)
            # keep legacy classroom code in sync when possible
            try:
                c = session.get(Classroom, space_id)
                if c and not getattr(c, 'code', None):
                    c.code = s.code
                    session.add(c)
            except Exception:
                pass
            session.commit()
            session.refresh(s)

        return JSONResponse({"ok": True, "code": s.code})


@app.post('/api/spaces/{space_id}/code/regenerate')
def regenerate_space_code(space_id: int, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        s = session.get(Space, space_id)
        if not s:
            c = session.get(Classroom, space_id)
            if not c:
                raise HTTPException(status_code=404, detail='Space not found')
            s = Space(
                id=c.id,
                school_id=getattr(c, 'school_id', None),
                name=getattr(c, 'name', None),
                code=getattr(c, 'code', None),
                created_at=getattr(c, 'created_at', None) or datetime.utcnow(),
            )
            session.add(s)
            session.commit()
            session.refresh(s)

        mem = session.exec(
            select(Membership).where(
                (Membership.user_id == current_user.id)
                & (
                    (Membership.space_id == space_id)
                    | (Membership.classroom_id == space_id)
                )
                & (Membership.role.in_(['teacher', 'admin']))
            )
        ).first()
        if not mem:
            raise HTTPException(status_code=403, detail='Only teacher/admin can regenerate code')

        s.code = _generate_classroom_code()
        session.add(s)
        # keep legacy classroom code in sync when possible
        try:
            c = session.get(Classroom, space_id)
            if c:
                c.code = s.code
                session.add(c)
        except Exception:
            pass
        session.commit()
        session.refresh(s)
        return JSONResponse({"ok": True, "code": s.code})


@app.post('/api/spaces/{space_id}/invite')
def api_space_invite_student(space_id: int, payload: dict = Body(...), current_user: User = Depends(get_current_user)):
    """Invite an existing user to a space by username (JSON API).

    For compatibility with existing invitation acceptance flows, we keep
    using `verb='classroom_invite'` and `target_type='classroom'` while
    ids are shared.
    """
    uname = (payload.get('username') or '').strip()
    if not uname:
        raise HTTPException(status_code=400, detail='username required')

    with Session(engine) as session:
        s = session.get(Space, space_id)
        if not s:
            c = session.get(Classroom, space_id)
            if not c:
                raise HTTPException(status_code=404, detail='Space not found')
            s = Space(
                id=c.id,
                school_id=getattr(c, 'school_id', None),
                name=getattr(c, 'name', None),
                code=getattr(c, 'code', None),
                created_at=getattr(c, 'created_at', None) or datetime.utcnow(),
            )
            session.add(s)
            session.commit()
            session.refresh(s)

        mem = session.exec(
            select(Membership).where(
                (Membership.user_id == current_user.id)
                & (
                    (Membership.space_id == space_id)
                    | (Membership.classroom_id == space_id)
                )
                & (Membership.role.in_(['teacher', 'admin']))
            )
        ).first()
        if not mem:
            raise HTTPException(status_code=403, detail='Only teacher/admin can invite')

        target = session.exec(select(User).where(User.username == uname)).first()
        if not target:
            raise HTTPException(status_code=404, detail='User not found')
        if target.id == current_user.id:
            raise HTTPException(status_code=400, detail='Cannot invite yourself')

        existing = session.exec(
            select(Membership).where(
                (Membership.user_id == target.id)
                & (
                    (Membership.space_id == space_id)
                    | (Membership.classroom_id == space_id)
                )
            )
        ).first()
        if existing:
            return JSONResponse({'ok': True, 'already_member': True})

        try:
            n = Notification(
                recipient_id=target.id,
                actor_id=current_user.id,
                verb='classroom_invite',
                target_type='classroom',
                target_id=space_id,
            )
            session.add(n)
            session.commit()
        except Exception:
            session.rollback()
            raise HTTPException(status_code=500, detail='Failed to create invite')

    return JSONResponse({'ok': True, 'invited_username': uname})


@app.post('/api/classrooms/{classroom_id}/invite')
def api_invite_student(classroom_id: int, payload: dict = Body(...), current_user: User = Depends(get_current_user)):
    """JSON API to invite a student to a classroom by username.

    Mirrors the behaviour of the HTML form endpoint but returns JSON
    instead of redirecting. On success, a `classroom_invite` notification
    is created for the target user.
    """
    uname = (payload.get('username') or '').strip()
    if not uname:
        raise HTTPException(status_code=400, detail='username required')

    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail='Classroom not found')

        mem = session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.user_id == current_user.id)
                & (Membership.role.in_(['teacher', 'admin']))
            )
        ).first()
        if not mem:
            raise HTTPException(status_code=403, detail='Only teacher/admin can invite')

        target = session.exec(select(User).where(User.username == uname)).first()
        if not target or target.id == current_user.id:
            raise HTTPException(status_code=404, detail='User not found')

        existing = session.exec(
            select(Membership).where(
                (Membership.user_id == target.id)
                & (Membership.classroom_id == classroom_id)
            )
        ).first()
        if existing:
            return JSONResponse({'ok': True, 'already_member': True})

        try:
            n = Notification(
                recipient_id=target.id,
                actor_id=current_user.id,
                verb='classroom_invite',
                target_type='classroom',
                target_id=classroom_id,
            )
            session.add(n)
            session.commit()
        except Exception:
            session.rollback()
            raise HTTPException(status_code=500, detail='Failed to create invite')

    return JSONResponse({'ok': True, 'invited_username': uname})


@app.get('/invitations/respond', response_class=HTMLResponse)
def invitations_respond(request: Request, token: str = Query(...), action: str = Query(...)):
    p = _verify_invite_token(token)
    if not p:
        return HTMLResponse('<p>Invalid or expired invitation token.</p>', status_code=400)
    email = p.get('email')
    classroom_id = p.get('classroom_id')
    with Session(engine) as session:
        u = session.exec(select(User).where(User.email == email)).first()
        if action == 'accept':
            if not u:
                # redirect to register with invite token
                return RedirectResponse(f"/register?invite_token={token}")
            # add membership if not exists
            exists = session.exec(select(Membership).where((Membership.user_id == u.id) & (Membership.classroom_id == classroom_id))).first()
            if not exists:
                m = Membership(user_id=u.id, classroom_id=classroom_id, role='student')
                session.add(m)
                session.commit()
            return HTMLResponse('<p>Thanks — you have been added to the classroom. You can <a href="/">return to the site</a>.</p>')
        else:
            return HTMLResponse('<p>You declined the invitation. No changes made.</p>')


@app.post('/choose-role')
def choose_role(request: Request, role: str = Form(...), invite_token: Optional[str] = Form(None), current_user: Optional[User] = Depends(get_current_user_optional)):
    """Persist a visitor's role choice in a cookie and to the user profile when logged in.
    Also process an optional invite token (create Membership) and preserve invite flow."""
    # persist cookie for client-side convenience
    resp = RedirectResponse('/', status_code=303)
    resp.set_cookie('user_role', role, max_age=30*24*60*60, path='/')
    resp.set_cookie('role_selected', 'true', max_age=30*24*60*60, path='/')
    # persist to DB if logged-in
    try:
        if current_user:
            with Session(engine) as session:
                u = session.get(User, current_user.id)
                if u:
                    u.site_role = role
                    session.add(u)
                    session.commit()
    except Exception:
        pass

    # process invite token if present (add membership for this user)
    if invite_token and current_user:
        try:
            payload = _verify_invite_token(invite_token)
            if payload and payload.get('email') == current_user.email:
                classroom_id = payload.get('classroom_id')
                with Session(engine) as session:
                    exists = session.exec(select(Membership).where((Membership.user_id == current_user.id) & (Membership.classroom_id == classroom_id))).first()
                    if not exists:
                        m = Membership(user_id=current_user.id, classroom_id=classroom_id, role='student')
                        session.add(m)
                        session.commit()
        except Exception:
            pass

    return resp


@app.get('/choose-role', response_class=HTMLResponse)
def choose_role_get(request: Request, current_user: Optional[User] = Depends(get_current_user)):
    """Render a server-side role selection page for new users."""
    csrf = None
    try:
        csrf = request.cookies.get('csrf_token')
    except Exception:
        csrf = None
    return templates.TemplateResponse('choose_role.html', {'request': request, 'csrf_token': csrf, 'current_user': current_user})


@app.get('/account/settings', response_class=HTMLResponse)
def account_settings_get(request: Request, current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse('account_settings.html', {'request': request, 'current_user': current_user})


@app.post('/account/settings')
def account_settings_post(request: Request, role: str = Form(...), current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        u = session.get(User, current_user.id)
        if not u:
            raise HTTPException(status_code=404, detail='User not found')
        u.site_role = role
        session.add(u)
        session.commit()
    resp = RedirectResponse('/account/settings', status_code=303)
    resp.set_cookie('user_role', role, max_age=30*24*60*60, path='/')
    return resp


@app.get('/classrooms/{classroom_id}/invite', response_class=HTMLResponse)
def classroom_invite_get(request: Request, classroom_id: int, current_user: User = Depends(get_current_user)):
    """Simple form for teachers/admins to invite students by email."""
@app.get('/classrooms/{classroom_id}/performance', response_class=HTMLResponse)
def classroom_performance(request: Request, classroom_id: int, current_user: User = Depends(get_current_user)):
    """Teacher-facing analytics page for a single classroom.

    Backed by the same metrics as the Phase 1 API; shows per-teacher
    assignment and upload counts plus submissions for their work.
    """
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail='Classroom not found')
        stmt = select(Membership).where((Membership.user_id == current_user.id) & (Membership.classroom_id == classroom_id))
        m = session.exec(stmt).first()
        if not m or m.role not in ('teacher', 'admin'):
            raise HTTPException(status_code=403, detail='Only teacher/admin can view performance')
        # find teachers/admins in classroom
        trows = session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.role.in_(['teacher', 'admin']))
            )
        ).all()
        teachers = []
        for tr in trows:
            uid = tr.user_id
            user = session.get(User, uid)
            a_count = session.exec(
                select(func.count(Assignment.id)).where(
                    (Assignment.classroom_id == classroom_id)
                    & (Assignment.created_by == uid)
                )
            ).first() or 0
            u_count = session.exec(
                select(func.count(LibraryItem.id)).where(
                    (LibraryItem.classroom_id == classroom_id)
                    & (LibraryItem.uploaded_by == uid)
                )
            ).first() or 0
            # submissions received for assignments created by this teacher
            aids = session.exec(
                select(Assignment.id).where(
                    (Assignment.classroom_id == classroom_id)
                    & (Assignment.created_by == uid)
                )
            ).all()
            aid_list = [r[0] if isinstance(r, (list, tuple)) else r for r in aids]
            subs_count = 0
            if aid_list:
                subs_count = session.exec(
                    select(func.count(Submission.id)).where(Submission.assignment_id.in_(aid_list))
                ).first() or 0
            teachers.append(
                {
                    'user_id': uid,
                    'username': getattr(user, 'username', None),
                    'assignments_count': int(a_count),
                    'uploads_count': int(u_count),
                    'submissions_count': int(subs_count),
                }
            )
    return templates.TemplateResponse(
        'teacher_performance.html',
        {'request': request, 'classroom': c, 'teachers': teachers},
    )


@app.get('/api/classrooms/{classroom_id}/performance/students')
def classroom_performance_students(classroom_id: int, current_user: User = Depends(get_current_user)):
    """Return per-student aggregated metrics for a classroom as JSON."""
    with Session(engine) as session:
        # permission: teacher/admin in classroom
        mem = session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.user_id == current_user.id)
            )
        ).first()
        if not mem or mem.role not in ('teacher', 'admin'):
            raise HTTPException(status_code=403, detail='Only teacher/admin can view performance')
        # list students in classroom
        studs = session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.role == 'student')
            )
        ).all()
        out = []
        for s in studs:
            uid = s.user_id
            user = session.get(User, uid)
            sub_count = session.exec(
                select(func.count(Submission.id)).where(Submission.student_id == uid)
            ).first() or 0
            att_count = session.exec(
                select(func.count(Attendance.id)).where(
                    (Attendance.user_id == uid)
                    & (Attendance.classroom_id == classroom_id)
                )
            ).first() or 0
            events = session.exec(
                select(func.count(StudentAnalytics.id)).where(
                    (StudentAnalytics.user_id == uid)
                    & (StudentAnalytics.classroom_id == classroom_id)
                )
            ).first() or 0
            out.append(
                {
                    'user_id': uid,
                    'username': getattr(user, 'username', None),
                    'submissions_count': int(sub_count),
                    'attendance_count': int(att_count),
                    'events_count': int(events),
                }
            )
    return JSONResponse({'students': out})


@app.get('/classrooms/{classroom_id}/performance.csv')
def classroom_performance_csv(classroom_id: int, current_user: User = Depends(get_current_user)):
    """Download classroom performance metrics as CSV for export."""
    with Session(engine) as session:
        mem = session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.user_id == current_user.id)
            )
        ).first()
        if not mem or mem.role not in ('teacher', 'admin'):
            raise HTTPException(status_code=403, detail='Only teacher/admin can view performance')
        studs = session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.role == 'student')
            )
        ).all()
        lines = ['student_id,username,submissions,attendance,events']
        for s in studs:
            uid = s.user_id
            user = session.get(User, uid)
            sub_count = session.exec(
                select(func.count(Submission.id)).where(Submission.student_id == uid)
            ).first() or 0
            att_count = session.exec(
                select(func.count(Attendance.id)).where(
                    (Attendance.user_id == uid)
                    & (Attendance.classroom_id == classroom_id)
                )
            ).first() or 0
            events = session.exec(
                select(func.count(StudentAnalytics.id)).where(
                    (StudentAnalytics.user_id == uid)
                    & (StudentAnalytics.classroom_id == classroom_id)
                )
            ).first() or 0
            lines.append(
                f"{uid},{getattr(user,'username', '')},{int(sub_count)},{int(att_count)},{int(events)}"
            )
    csv_text = '\n'.join(lines)
    from fastapi.responses import Response
    return Response(content=csv_text, media_type='text/csv')


@app.get('/classrooms/{classroom_id}')
def get_classroom(request: Request, classroom_id: int, current_user: User = Depends(get_current_user_optional)):
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail='Classroom not found')
        mem = session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.user_id == current_user.id)
            )
        ).first()
        if not mem or mem.role not in ('teacher', 'admin'):
            raise HTTPException(status_code=403, detail='Only teacher/admin can invite')
    csrf = None
    try:
        csrf = request.cookies.get('csrf_token')
    except Exception:
        csrf = None
    return templates.TemplateResponse(
        'classroom_invite.html',
        {'request': request, 'classroom': c, 'csrf_token': csrf},
    )

@app.get('/classrooms/{classroom_id}')
def get_classroom(classroom_id: int, current_user: User = Depends(get_current_user_optional)):
    """Return basic JSON info about a classroom (public endpoint)."""
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail='Classroom not found')
        data = {
            'id': c.id,
            'name': c.name,
            'school_id': c.school_id,
            'code': c.code,
            'created_at': c.created_at.isoformat() if getattr(c, 'created_at', None) else None,
        }
    return JSONResponse({'ok': True, 'classroom': data})


@app.get('/classrooms/{classroom_id}/view', response_class=HTMLResponse)
def classroom_view(request: Request, classroom_id: int, current_user: User = Depends(get_current_user)):
    """Full classroom view for members: library + assignments + links."""
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail='Classroom not found')
        mem = session.exec(
            select(Membership).where(
                (Membership.user_id == current_user.id)
                & (Membership.classroom_id == classroom_id)
            )
        ).first()
        if not mem:
            raise HTTPException(status_code=403, detail='Not a classroom member')
        from .models import ClassroomMessage as CM

        rows = session.exec(
            select(CM, User.username, User.site_role)
            .where(CM.classroom_id == classroom_id)
            .join(User, CM.sender_id == User.id)
            .order_by(CM.created_at.asc())
        ).all()
        messages = []
        for cm, uname, role in rows:
            messages.append(
                SimpleNamespace(
                    id=cm.id,
                    classroom_id=cm.classroom_id,
                    sender_id=cm.sender_id,
                    content=cm.content,
                    created_at=cm.created_at,
                    sender_name=uname,
                    sender_role=role,
                )
            )
    return templates.TemplateResponse(
        'classroom.html',
        {
            'request': request,
            'classroom': c,
            'member': mem,
            'member_role': (getattr(mem, 'role', None) or '').lower(),
            'messages': messages,
        },
    )


@app.get('/debug/my_memberships')
def debug_my_memberships(current_user: User = Depends(get_current_user)):
    """Debug endpoint: list the current user's memberships and classroom names.

    Accessible only to authenticated users; useful for verifying student enrollments.
    """
    with Session(engine) as session:
        mems = session.exec(select(Membership).where(Membership.user_id == current_user.id)).all()
        out = []
        for m in mems:
            c = session.get(Classroom, m.classroom_id)
            out.append({
                'classroom_id': m.classroom_id,
                'role': m.role,
                'classroom_name': getattr(c, 'name', None) if c else None,
            })
    return JSONResponse({'ok': True, 'memberships': out})


@app.get('/classrooms/{classroom_id}/library', response_class=HTMLResponse)
def classroom_library_page(request: Request, classroom_id: int, current_user: User = Depends(get_current_user)):
    """Dedicated page for class library and uploads."""
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail='Classroom not found')
        mem = session.exec(
            select(Membership).where(
                (Membership.user_id == current_user.id)
                & (Membership.classroom_id == classroom_id)
            )
        ).first()
        if not mem:
            raise HTTPException(status_code=403, detail='Not a classroom member')
        teacher_ids = [m.user_id for m in session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.role.in_(['teacher', 'admin']))
            )
        ).all()]
        items = session.exec(
            select(LibraryItem)
            .where(
                (LibraryItem.classroom_id == classroom_id)
                & (LibraryItem.uploaded_by.in_(teacher_ids))
            )
            .order_by(LibraryItem.created_at.desc())
        ).all()
    return templates.TemplateResponse(
        'classroom_library.html',
        {'request': request, 'classroom': c, 'member': mem, 'items': items},
    )


@app.get('/classrooms/{classroom_id}/attendance', response_class=HTMLResponse)
def classroom_attendance_page(request: Request, classroom_id: int, current_user: User = Depends(get_current_user)):
    """Attendance page for teachers/admins."""
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail='Classroom not found')
        mem = session.exec(
            select(Membership).where(
                (Membership.user_id == current_user.id)
                & (Membership.classroom_id == classroom_id)
            )
        ).first()
        if not mem or mem.role not in ('teacher', 'admin'):
            raise HTTPException(status_code=403, detail='Only teacher/admin can mark attendance')
        rows = session.exec(
            select(Membership).where(Membership.classroom_id == classroom_id)
        ).all()
        members = []
        for m in rows:
            u = session.get(User, m.user_id)
            members.append({'user_id': m.user_id, 'username': getattr(u, 'username', None), 'role': m.role})
    return templates.TemplateResponse(
        'classroom_attendance.html',
        {'request': request, 'classroom': c, 'members': members},
    )


@app.get('/classrooms/{classroom_id}/assignments', response_class=HTMLResponse)
def classroom_assignments_page(request: Request, classroom_id: int, current_user: User = Depends(get_current_user)):
    """Dedicated page for classroom assignments."""
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail='Classroom not found')
        mem = session.exec(
            select(Membership).where(
                (Membership.user_id == current_user.id)
                & (Membership.classroom_id == classroom_id)
            )
        ).first()
        if not mem:
            raise HTTPException(status_code=403, detail='Not a classroom member')
        assignments = session.exec(
            select(Assignment)
            .where(Assignment.classroom_id == classroom_id)
            .order_by(Assignment.created_at.desc())
        ).all()
        done_counts = {}
        if assignments:
            aid_list = [a.id for a in assignments]
            statuses = session.exec(
                select(AssignmentStatus).where(AssignmentStatus.assignment_id.in_(aid_list))
            ).all()
            for st in statuses:
                if st.status == 'done':
                    done_counts[st.assignment_id] = done_counts.get(st.assignment_id, 0) + 1
    return templates.TemplateResponse(
        'classroom_assignments.html',
        {
            'request': request,
            'classroom': c,
            'member': mem,
            'assignments': assignments,
            'done_counts': done_counts,
        },
    )


@app.post('/classrooms/{classroom_id}/assignments')
def classroom_create_assignment(
    request: Request,
    classroom_id: int,
    title: str = Form(...),
    description: Optional[str] = Form(None),
    due_date: Optional[str] = Form(None),
    csrf_token: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
):
    """Create a new assignment from the classroom assignments page form."""
    validate_csrf(request, csrf_token)
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail='Classroom not found')
        stmt = select(Membership).where(
            (Membership.user_id == current_user.id)
            & (Membership.classroom_id == classroom_id)
        )
        m = session.exec(stmt).first()
        if not m or m.role not in ('teacher', 'admin'):
            raise HTTPException(status_code=403, detail='Only teacher/admin can create assignments')
        adt: Optional[datetime] = None
        if due_date:
            try:
                # Support both full ISO datetimes and simple YYYY-MM-DD values
                adt = datetime.fromisoformat(due_date)
            except Exception:
                try:
                    # If only a date was provided (from a date picker), assume end of that day
                    adt = datetime.fromisoformat(due_date + "T23:59:59")
                except Exception:
                    adt = None
        a = Assignment(
            classroom_id=classroom_id,
            title=title,
            description=description,
            due_date=adt,
            created_by=current_user.id,
        )
        session.add(a)
        session.commit()
        session.refresh(a)
        # Notify all students in this classroom about the new assignment
        student_mems = session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.role == 'student')
            )
        ).all()
        for sm in student_mems:
            n = Notification(
                recipient_id=sm.user_id,
                actor_id=current_user.id,
                verb='new_assignment',
                target_type='assignment',
                target_id=a.id,
            )
            session.add(n)
        session.commit()
    return RedirectResponse(f"/classrooms/{classroom_id}/assignments", status_code=303)


@app.get('/classrooms/{classroom_id}/live', response_class=HTMLResponse)
def classroom_live_page(request: Request, classroom_id: int, current_user: User = Depends(get_current_user)):
    """Dedicated page for live session helpers and attendance/performance links."""
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail='Classroom not found')
        mem = session.exec(
            select(Membership).where(
                (Membership.user_id == current_user.id)
                & (Membership.classroom_id == classroom_id)
            )
        ).first()
        if not mem or mem.role not in ('teacher', 'admin'):
            raise HTTPException(status_code=403, detail='Only teacher/admin can access live tools')
    return templates.TemplateResponse(
        'classroom_live.html',
        {'request': request, 'classroom': c, 'member': mem},
    )


@app.post('/classrooms/{classroom_id}/join')
def join_classroom(classroom_id: int, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail='Classroom not found')
        # if already member, return
        stmt = select(Membership).where(
            (Membership.user_id == current_user.id)
            & (Membership.classroom_id == classroom_id)
        )
        existing = session.exec(stmt).first()
        if existing:
            return JSONResponse({'ok': True, 'member': {'id': existing.id, 'role': existing.role}})
        m = Membership(user_id=current_user.id, classroom_id=classroom_id, role='student')
        session.add(m)
        session.commit()
        session.refresh(m)
        return JSONResponse({'ok': True, 'member': {'id': m.id, 'role': m.role}})


@app.post('/classrooms/{classroom_id}/leave')
def leave_classroom(classroom_id: int, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        stmt = select(Membership).where(
            (Membership.user_id == current_user.id)
            & (Membership.classroom_id == classroom_id)
        )
        m = session.exec(stmt).first()
        if not m:
            raise HTTPException(status_code=404, detail='Membership not found')
        session.delete(m)
        session.commit()
        return JSONResponse({'ok': True})


@app.post('/spaces/{space_id}/leave')
def leave_space(space_id: int, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        stmt = select(Membership).where(
            (Membership.user_id == current_user.id)
            & (
                (Membership.space_id == space_id)
                | (Membership.classroom_id == space_id)
            )
        )
        m = session.exec(stmt).first()
        if not m:
            raise HTTPException(status_code=404, detail='Membership not found')
        session.delete(m)
        session.commit()
        return JSONResponse({'ok': True})


@app.get('/classrooms/{classroom_id}/members', response_class=HTMLResponse)
def list_classroom_members(request: Request, classroom_id: int, current_user: User = Depends(get_current_user)):
    """View of classroom members for all users."""
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail='Classroom not found')
        # must be a member to view
        requester = session.exec(
            select(Membership).where(
                (Membership.user_id == current_user.id)
                & (Membership.classroom_id == classroom_id)
            )
        ).first()
        if not requester:
            raise HTTPException(status_code=403, detail='You must be a member to view members')
        rows = session.exec(
            select(Membership).where(Membership.classroom_id == classroom_id)
        ).all()
        members = []
        for m in rows:
            u = session.get(User, m.user_id)
            members.append(
                {
                    'user_id': m.user_id,
                    'username': getattr(u, 'username', None),
                    'role': m.role,
                }
            )
    ajax = request.headers.get('x-requested-with', '').lower() == 'xmlhttprequest'
    return templates.TemplateResponse(
        'classroom_members.html',
        {'request': request, 'classroom': c, 'members': members, 'ajax': ajax},
    )


@app.get('/classrooms/{classroom_id}/boot', response_class=HTMLResponse)
def classroom_boot_page(request: Request, classroom_id: int, current_user: User = Depends(get_current_user)):
    """Teacher/admin page to remove students from the classroom by username."""
    error = request.query_params.get('error')
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail='Classroom not found')
        mem = session.exec(
            select(Membership).where(
                (Membership.user_id == current_user.id)
                & (Membership.classroom_id == classroom_id)
            )
        ).first()
        if not mem or mem.role not in ('teacher', 'admin'):
            raise HTTPException(status_code=403, detail='Only teacher/admin can boot students')
        rows = session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.role == 'student')
            )
        ).all()
        students = []
        for m in rows:
            u = session.get(User, m.user_id)
            students.append({'user_id': m.user_id, 'username': getattr(u, 'username', None)})
    return templates.TemplateResponse(
        'classroom_boot.html',
        {'request': request, 'classroom': c, 'students': students, 'error': error},
    )


@app.post('/classrooms/{classroom_id}/boot')
async def classroom_boot_action(
    request: Request,
    classroom_id: int,
    usernames: str = Form(...),
    csrf_token: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
):
    """Remove one or more students from the classroom by username list."""
    validate_csrf(request, csrf_token)
    raw = usernames or ''
    # allow comma/space separated usernames
    parts = [p.strip() for p in raw.replace('\n', ',').split(',') if p.strip()]
    if not parts:
        return RedirectResponse(f"/classrooms/{classroom_id}/boot?error=empty", status_code=303)

    removed = []
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail='Classroom not found')
        mem = session.exec(
            select(Membership).where(
                (Membership.user_id == current_user.id)
                & (Membership.classroom_id == classroom_id)
            )
        ).first()
        if not mem or mem.role not in ('teacher', 'admin'):
            raise HTTPException(status_code=403, detail='Only teacher/admin can boot students')

        for uname in parts:
            u = session.exec(select(User).where(User.username == uname)).first()
            if not u:
                continue
            m = session.exec(
                select(Membership).where(
                    (Membership.classroom_id == classroom_id)
                    & (Membership.user_id == u.id)
                    & (Membership.role == 'student')
                )
            ).first()
            if not m:
                continue
            session.delete(m)
            removed.append(uname)
            try:
                cm = ClassroomMessage(classroom_id=classroom_id, sender_id=current_user.id, content=f"[system] {uname} was removed from the classroom.")
                session.add(cm)
            except Exception:
                pass
        if removed:
            session.commit()
    return RedirectResponse(f"/classrooms/{classroom_id}/boot", status_code=303)


@app.get('/classrooms/{classroom_id}/performance', response_class=HTMLResponse)
def classroom_performance(
    request: Request,
    classroom_id: int,
    current_user: User = Depends(get_current_user),
):
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail='Classroom not found')
        stmt = select(Membership).where(
            (Membership.user_id == current_user.id)
            & (Membership.classroom_id == classroom_id)
        )
        m = session.exec(stmt).first()
        if not m or m.role not in ('teacher', 'admin'):
            raise HTTPException(
                status_code=403, detail='Only teacher/admin can view performance'
            )
        trows = session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.role.in_(['teacher', 'admin']))
            )
        ).all()
        teachers = []
        for tr in trows:
            uid = tr.user_id
            user = session.get(User, uid)
            a_count = (
                session.exec(
                    select(func.count(Assignment.id)).where(
                        (Assignment.classroom_id == classroom_id)
                        & (Assignment.created_by == uid)
                    )
                ).first()
                or 0
            )
            u_count = (
                session.exec(
                    select(func.count(LibraryItem.id)).where(
                        (LibraryItem.classroom_id == classroom_id)
                        & (LibraryItem.uploaded_by == uid)
                    )
                ).first()
                or 0
            )
            aids = session.exec(
                select(Assignment.id).where(
                    (Assignment.classroom_id == classroom_id)
                    & (Assignment.created_by == uid)
                )
            ).all()
            aid_list = [r[0] if isinstance(r, (list, tuple)) else r for r in aids]
            subs_count = 0
            if aid_list:
                subs_count = (
                    session.exec(
                        select(func.count(Submission.id)).where(
                            Submission.assignment_id.in_(aid_list)
                        )
                    ).first()
                    or 0
                )
            teachers.append(
                {
                    'user_id': uid,
                    'username': getattr(user, 'username', None),
                    'site_role': getattr(user, 'site_role', None),
                    'assignments_count': int(a_count),
                    'uploads_count': int(u_count),
                    'submissions_count': int(subs_count),
                }
            )
    return templates.TemplateResponse(
        'teacher_performance.html',
        {'request': request, 'classroom': c, 'teachers': teachers},
    )


@app.get('/api/classrooms/{classroom_id}/performance/students')
def classroom_performance_students(
    classroom_id: int,
    current_user: User = Depends(get_current_user),
):
    """Return per-student aggregated metrics for a classroom."""
    with Session(engine) as session:
        mem = session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.user_id == current_user.id)
            )
        ).first()
        if not mem or mem.role not in ('teacher', 'admin'):
            raise HTTPException(
                status_code=403, detail='Only teacher/admin can view performance'
            )
        studs = session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.role == 'student')
            )
        ).all()
        out = []
        for s in studs:
            uid = s.user_id
            user = session.get(User, uid)
            sub_count = (
                session.exec(
                    select(func.count(Submission.id)).where(
                        Submission.student_id == uid
                    )
                ).first()
                or 0
            )
            att_count = (
                session.exec(
                    select(func.count(Attendance.id)).where(
                        (Attendance.user_id == uid)
                        & (Attendance.classroom_id == classroom_id)
                    )
                ).first()
                or 0
            )
            events = (
                session.exec(
                    select(func.count(StudentAnalytics.id)).where(
                        (StudentAnalytics.user_id == uid)
                        & (StudentAnalytics.classroom_id == classroom_id)
                    )
                ).first()
                or 0
            )
            out.append(
                {
                    'user_id': uid,
                    'username': getattr(user, 'username', None),
                    'submissions_count': int(sub_count),
                    'attendance_count': int(att_count),
                    'events_count': int(events),
                }
            )
    return JSONResponse({'students': out})


@app.get('/classrooms/{classroom_id}/performance.csv')
def classroom_performance_csv(
    classroom_id: int,
    current_user: User = Depends(get_current_user),
):
    with Session(engine) as session:
        mem = session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.user_id == current_user.id)
            )
        ).first()
        if not mem or mem.role not in ('teacher', 'admin'):
            raise HTTPException(
                status_code=403, detail='Only teacher/admin can view performance'
            )
        studs = session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.role == 'student')
            )
        ).all()
        lines = ['student_id,username,submissions,attendance,events']
        for s in studs:
            uid = s.user_id
            user = session.get(User, uid)
            sub_count = (
                session.exec(
                    select(func.count(Submission.id)).where(
                        Submission.student_id == uid
                    )
                ).first()
                or 0
            )
            att_count = (
                session.exec(
                    select(func.count(Attendance.id)).where(
                        (Attendance.user_id == uid)
                        & (Attendance.classroom_id == classroom_id)
                    )
                ).first()
                or 0
            )
            events = (
                session.exec(
                    select(func.count(StudentAnalytics.id)).where(
                        (StudentAnalytics.user_id == uid)
                        & (StudentAnalytics.classroom_id == classroom_id)
                    )
                ).first()
                or 0
            )
            lines.append(
                f"{uid},{getattr(user,'username', '')},{int(sub_count)},{int(att_count)},{int(events)}"
            )
    csv_text = '\n'.join(lines)
    return Response(content=csv_text, media_type='text/csv')


@app.get('/schools/{school_id}/admin', response_class=HTMLResponse)
def school_admin_page(
    request: Request,
    school_id: int,
    current_user: User = Depends(get_current_user),
):
    with Session(engine) as session:
        school = session.get(School, school_id)
        if not school:
            raise HTTPException(status_code=404, detail='School not found')
        ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', '')
        allowed = False
        if ADMIN_USERNAME and current_user.username == ADMIN_USERNAME:
            allowed = True
        else:
            cls = session.exec(
                select(Classroom).where(Classroom.school_id == school_id)
            ).all()
            cls_ids = [c.id for c in cls]
            if cls_ids:
                mem = session.exec(
                    select(Membership).where(
                        (Membership.classroom_id.in_(cls_ids))
                        & (Membership.user_id == current_user.id)
                        & (Membership.role == 'admin')
                    )
                ).first()
                if mem:
                    allowed = True
        if not allowed:
            raise HTTPException(status_code=403, detail='School admin access required')
        classrooms = []
        for c in session.exec(
            select(Classroom).where(Classroom.school_id == school_id)
        ).all():
            members = []
            for m in session.exec(
                select(Membership).where(Membership.classroom_id == c.id)
            ).all():
                u = session.get(User, m.user_id)
                members.append(
                    {
                        'user_id': m.user_id,
                        'username': getattr(u, 'username', None),
                        'role': m.role,
                    }
                )
            classrooms.append(
                {'id': c.id, 'name': c.name, 'members': members}
            )
    return templates.TemplateResponse(
        'school_admin.html',
        {'request': request, 'school': school, 'classrooms': classrooms},
    )


@app.post('/schools/{school_id}/admin/role')
def school_admin_change_role(
    request: Request,
    school_id: int,
    classroom_id: int = Form(...),
    user_id: int = Form(...),
    role: str = Form(...),
    csrf_token: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
):
    with Session(engine) as session:
        school = session.get(School, school_id)
        if not school:
            raise HTTPException(status_code=404, detail='School not found')
        ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', '')
        allowed = False
        if ADMIN_USERNAME and current_user.username == ADMIN_USERNAME:
            allowed = True
        else:
            mem = session.exec(
                select(Membership).where(
                    (Membership.classroom_id == classroom_id)
                    & (Membership.user_id == current_user.id)
                    & (Membership.role == 'admin')
                )
            ).first()
            if mem:
                allowed = True
        if not allowed:
            raise HTTPException(status_code=403, detail='School admin access required')
        try:
            cookie_token = None
            if hasattr(request, 'cookies'):
                cookie_token = request.cookies.get('csrf_token')
            if not csrf_token or not cookie_token or csrf_token != cookie_token:
                raise HTTPException(
                    status_code=403,
                    detail='CSRF token missing or invalid',
                )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=403, detail='CSRF validation failed')
        m = session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.user_id == user_id)
            )
        ).first()
        if not m:
            raise HTTPException(status_code=404, detail='Membership not found')
        if role not in ('student', 'teacher', 'admin'):
            raise HTTPException(status_code=400, detail='invalid role')
        m.role = role
        session.add(m)
        session.commit()
        try:
            u = session.get(User, user_id)
            if u and getattr(u, 'email', None):
                try:
                    cls = session.get(Classroom, classroom_id)
                    ctx = {
                        'user_name': getattr(u, 'username', None) or '',
                        'classroom_name': getattr(cls, 'name', '') if cls else '',
                        'role': role,
                        'actor_name': getattr(current_user, 'username', '')
                        if current_user
                        else '',
                        'time': __import__('datetime').datetime.utcnow().isoformat(),
                    }
                    enqueue_email(
                        u.email,
                        'Role changed',
                        None,
                        'emails/role_changed.html',
                        ctx,
                    )
                except Exception:
                    send_email(
                        u.email,
                        'Role changed',
                        f'Your role in classroom {classroom_id} was changed to {role} by {current_user.username}.',
                    )
        except Exception:
            logger.exception('failed to send role change email')
    return RedirectResponse(f'/schools/{school_id}/admin', status_code=303)


@app.get('/schools/{school_id}/audit', response_class=HTMLResponse)
def school_audit_log(
    request: Request,
    school_id: int,
    event_type: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
):
    with Session(engine) as session:
        school = session.get(School, school_id)
        if not school:
            raise HTTPException(status_code=404, detail='School not found')
        ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', '')
        allowed = False
        if ADMIN_USERNAME and current_user.username == ADMIN_USERNAME:
            allowed = True
        else:
            cls = session.exec(
                select(Classroom).where(Classroom.school_id == school_id)
            ).all()
            cls_ids = [c.id for c in cls]
            if cls_ids:
                mem = session.exec(
                    select(Membership).where(
                        (Membership.classroom_id.in_(cls_ids))
                        & (Membership.user_id == current_user.id)
                        & (Membership.role == 'admin')
                    )
                ).first()
                if mem:
                    allowed = True
        if not allowed:
            raise HTTPException(status_code=403, detail='School admin access required')
        cls = session.exec(
            select(Classroom).where(Classroom.school_id == school_id)
        ).all()
        cls_ids = [c.id for c in cls]
        q = select(StudentAnalytics).where(
            StudentAnalytics.classroom_id.in_(cls_ids)
        )
        from datetime import datetime

        if event_type:
            q = q.where(StudentAnalytics.event_type == event_type)
        if from_date:
            try:
                start = datetime.fromisoformat(from_date)
                q = q.where(StudentAnalytics.created_at >= start)
            except Exception:
                pass
        if to_date:
            try:
                end = datetime.fromisoformat(to_date)
                q = q.where(StudentAnalytics.created_at <= end)
            except Exception:
                pass
        try:
            page = int(request.query_params.get('page', 1))
        except Exception:
            page = 1
        try:
            per_page = int(request.query_params.get('per_page', 50))
        except Exception:
            per_page = 50
        per_page = max(1, min(per_page, 100))
        offset = (page - 1) * per_page
        total = session.exec(
            select(func.count(StudentAnalytics.id)).where(
                StudentAnalytics.classroom_id.in_(cls_ids)
            )
        ).first() or 0
        events = session.exec(
            q.order_by(StudentAnalytics.created_at.desc())
            .offset(offset)
            .limit(per_page)
        ).all()
    return templates.TemplateResponse(
        'audit_log.html',
        {
            'request': request,
            'school': school,
            'events': events,
            'page': page,
            'per_page': per_page,
            'total': int(total),
        },
    )


@app.get('/schools/{school_id}/audit.csv')
def school_audit_csv(
    school_id: int,
    current_user: User = Depends(get_current_user),
):
    with Session(engine) as session:
        school = session.get(School, school_id)
        if not school:
            raise HTTPException(status_code=404, detail='School not found')
        ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', '')
        allowed = False
        if ADMIN_USERNAME and current_user.username == ADMIN_USERNAME:
            allowed = True
        else:
            cls = session.exec(
                select(Classroom).where(Classroom.school_id == school_id)
            ).all()
            cls_ids = [c.id for c in cls]
            if cls_ids:
                mem = session.exec(
                    select(Membership).where(
                        (Membership.classroom_id.in_(cls_ids))
                        & (Membership.user_id == current_user.id)
                        & (Membership.role == 'admin')
                    )
                ).first()
                if mem:
                    allowed = True
        if not allowed:
            raise HTTPException(status_code=403, detail='School admin access required')
        cls = session.exec(
            select(Classroom).where(Classroom.school_id == school_id)
        ).all()
        cls_ids = [c.id for c in cls]
        rows = session.exec(
            select(StudentAnalytics)
            .where(StudentAnalytics.classroom_id.in_(cls_ids))
            .order_by(StudentAnalytics.created_at.desc())
            .limit(2000)
        ).all()
        lines = ['time,user_id,classroom_id,event_type,details']
        for r in rows:
            lines.append(
                f"{r.created_at},{r.user_id},{r.classroom_id},{r.event_type},{(r.details or '').replace(',',';')}"
            )
    return Response(content='\n'.join(lines), media_type='text/csv')


@app.post('/classrooms/{classroom_id}/library/upload')
async def upload_library_item(request: Request, classroom_id: int, file: UploadFile = File(...), title: Optional[str] = Form(None), csrf_token: Optional[str] = Form(None), current_user: User = Depends(get_current_user)):
    """Teachers/admins upload private classroom materials."""
    validate_csrf(request, csrf_token)
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail='Classroom not found')
        stmt = select(Membership).where((Membership.user_id == current_user.id) & (Membership.classroom_id == classroom_id))
        m = session.exec(stmt).first()
        if not m or m.role not in ('teacher', 'admin'):
            raise HTTPException(status_code=403, detail='Only teacher/admin can upload')
        dst_dir = Path(UPLOAD_DIR) / 'classroom' / str(classroom_id)
        dst_dir.mkdir(parents=True, exist_ok=True)
        safe_name = f"{uuid.uuid4().hex}_{Path(file.filename).name}"
        dst_path = dst_dir / safe_name
        try:
            with dst_path.open('wb') as out:
                shutil.copyfileobj(file.file, out)
        finally:
            try:
                file.file.close()
            except Exception:
                pass
        li = LibraryItem(classroom_id=classroom_id, presentation_id=None, title=title or Path(file.filename).name, filename=str(Path('classroom') / str(classroom_id) / safe_name), mimetype=(file.content_type or 'application/octet-stream'), uploaded_by=current_user.id)
        session.add(li)
        session.commit()
        session.refresh(li)
        return RedirectResponse(f"/classrooms/{classroom_id}/library", status_code=303)


@app.get('/classrooms/{classroom_id}/library/files')
def list_library_files(classroom_id: int, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        stmt = select(Membership).where((Membership.user_id == current_user.id) & (Membership.classroom_id == classroom_id))
        m = session.exec(stmt).first()
        if not m:
            raise HTTPException(status_code=403, detail='Not a classroom member')
        teacher_ids = [mem.user_id for mem in session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.role.in_(['teacher', 'admin']))
            )
        ).all()]
        items = session.exec(
            select(LibraryItem)
            .where(
                (LibraryItem.classroom_id == classroom_id)
                & (LibraryItem.uploaded_by.in_(teacher_ids))
            )
            .order_by(LibraryItem.created_at.desc())
        ).all()
        return JSONResponse({'ok': True, 'items': [{'id': i.id, 'title': i.title, 'filename': i.filename, 'mimetype': i.mimetype, 'uploaded_by': i.uploaded_by, 'created_at': i.created_at.isoformat()} for i in items]})


@app.get('/classrooms/{classroom_id}/library/download/{item_id}')
def download_library_item(classroom_id: int, item_id: int, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        item = session.get(LibraryItem, item_id)
        if not item or item.classroom_id != classroom_id:
            raise HTTPException(status_code=404, detail='File not found')
        stmt = select(Membership).where((Membership.user_id == current_user.id) & (Membership.classroom_id == classroom_id))
        m = session.exec(stmt).first()
        if not m:
            raise HTTPException(status_code=403, detail='Not a classroom member')
        disk_path = Path(UPLOAD_DIR) / item.filename
        if not disk_path.exists():
            raise HTTPException(status_code=404, detail='File missing on disk')
        return FileResponse(str(disk_path), media_type=item.mimetype, filename=Path(item.filename).name)


@app.get('/submissions/{submission_id}/download')
def download_submission(submission_id: int, current_user: User = Depends(get_current_user)):
            """Serve a submission file to authorized users (student who submitted or classroom teacher/admin)."""
            Submission = __import__("app.models").models.Submission
            Assignment = __import__("app.models").models.Assignment
            Membership = __import__("app.models").models.Membership
            with Session(engine) as session:
                s = session.get(Submission, submission_id)
                if not s:
                    raise HTTPException(status_code=404, detail='Submission not found')
                a = session.get(Assignment, s.assignment_id)
                if not a:
                    raise HTTPException(status_code=404, detail='Assignment not found')
                # allow if owner (student) or teacher/admin in classroom
                if current_user.id == s.student_id:
                    permitted = True
                else:
                    mem = session.exec(select(Membership).where((Membership.classroom_id == a.classroom_id) & (Membership.user_id == current_user.id))).first()
                    permitted = bool(mem and mem.role in ('teacher', 'admin'))
                if not permitted:
                    raise HTTPException(status_code=403, detail='forbidden')
                # build disk path
                disk_path = Path(UPLOAD_DIR) / s.filename if s.filename else None
                if not disk_path or not disk_path.exists():
                    raise HTTPException(status_code=404, detail='file missing on disk')
                # record audit event & notify student when teacher/admin downloads
                try:
                    if current_user.id != s.student_id:
                        sa = StudentAnalytics(user_id=current_user.id, classroom_id=a.classroom_id, event_type='submission_download', details=f'submission={submission_id};student={s.student_id}')
                        session.add(sa)
                        # notify student that their submission was accessed by teacher/admin
                        try:
                            n = Notification(recipient_id=s.student_id, actor_id=current_user.id, verb='download_submission', target_type='submission', target_id=submission_id)
                            session.add(n)
                            # attempt to email student
                            student_user = session.get(User, s.student_id)
                            if student_user and getattr(student_user, 'email', None):
                                try:
                                    # enqueue email to send asynchronously
                                    try:
                                        # render email with context
                                        class_name = ''
                                        try:
                                            cls = session.get(Classroom, a.classroom_id)
                                            class_name = getattr(cls, 'name', '') if cls else ''
                                        except Exception:
                                            class_name = ''
                                        ctx = {
                                            'user_name': getattr(student_user, 'username', '') or '',
                                            'submission_id': submission_id,
                                            'assignment_title': getattr(a, 'title', '') if a else '',
                                            'classroom_name': class_name,
                                            'actor_name': getattr(current_user, 'username', '') if current_user else '',
                                            'time': __import__('datetime').datetime.utcnow().isoformat(),
                                        }
                                        enqueue_email(student_user.email, 'Your submission was accessed', None, 'emails/submission_download.html', ctx)
                                    except Exception:
                                        # fall back to synchronous send
                                        send_email(student_user.email, 'Your submission was accessed', f"Your submission (id={submission_id}) was accessed by {current_user.username}.")
                                except Exception:
                                    pass
                        except Exception:
                            session.rollback()
                        session.commit()
                except Exception:
                    session.rollback()
                return FileResponse(str(disk_path), media_type=s.mimetype or 'application/octet-stream', filename=Path(s.filename).name)

    # Assignment creation endpoint
@app.post('/classrooms/{classroom_id}/assignments')
def create_assignment(request: Request, classroom_id: int, title: str = Form(...), description: Optional[str] = Form(None), due_date: Optional[str] = Form(None), csrf_token: Optional[str] = Form(None), current_user: User = Depends(get_current_user)):
    validate_csrf(request, csrf_token)
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail='Classroom not found')
        stmt = select(Membership).where((Membership.user_id == current_user.id) & (Membership.classroom_id == classroom_id))
        m = session.exec(stmt).first()
        if not m or m.role not in ('teacher', 'admin'):
            raise HTTPException(status_code=403, detail='Only teacher/admin can create assignments')
        adt = None
        if due_date:
            try:
                adt = datetime.fromisoformat(due_date)
            except Exception:
                adt = None
        a = Assignment(classroom_id=classroom_id, title=title, description=description, due_date=adt, created_by=current_user.id)
        session.add(a)
        session.commit()
        session.refresh(a)
        # analytics: assignment created
        try:
            sa = StudentAnalytics(user_id=current_user.id, classroom_id=classroom_id, event_type='assignment_create', details=f'assignment={a.id}')
            session.add(sa)
            session.commit()
        except Exception:
            session.rollback()






@app.post('/assignments/{assignment_id}/status')
def set_assignment_status(request: Request, assignment_id: int, status: str = Form(...), csrf_token: Optional[str] = Form(None), current_user: User = Depends(get_current_user)):
    """Allow a student to mark an assignment as done / almost / rebel."""
    validate_csrf(request, csrf_token)
    allowed = {'done', 'almost', 'rebel'}
    if status not in allowed:
        raise HTTPException(status_code=400, detail='invalid status')
    with Session(engine) as session:
        a = session.get(Assignment, assignment_id)
        if not a:
            raise HTTPException(status_code=404, detail='Assignment not found')
        # must be student in this classroom
        stmt = select(Membership).where(
            (Membership.user_id == current_user.id)
            & (Membership.classroom_id == a.classroom_id)
            & (Membership.role == 'student')
        )
        m = session.exec(stmt).first()
        if not m:
            raise HTTPException(status_code=403, detail='Only students in this classroom can set status')
        st = session.exec(
            select(AssignmentStatus).where(
                (AssignmentStatus.assignment_id == assignment_id)
                & (AssignmentStatus.student_id == current_user.id)
            )
        ).first()
        if not st:
            st = AssignmentStatus(assignment_id=assignment_id, student_id=current_user.id, status=status)
        else:
            st.status = status
        session.add(st)
        session.commit()
    # send them back to materials page by default
    return RedirectResponse('/my/materials', status_code=303)


@app.get('/assignments/{assignment_id}/submissions')
def list_submissions(request: Request, assignment_id: int, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        a = session.get(Assignment, assignment_id)
        if not a:
            raise HTTPException(status_code=404, detail='Assignment not found')
        stmt = select(Membership).where((Membership.user_id == current_user.id) & (Membership.classroom_id == a.classroom_id))
        m = session.exec(stmt).first()
        if not m or m.role not in ('teacher', 'admin'):
            raise HTTPException(status_code=403, detail='Only teacher/admin can view submissions')
        subs = session.exec(select(Submission).where(Submission.assignment_id == assignment_id).order_by(Submission.created_at.desc())).all()
        # load status records and user names for this assignment
        statuses = session.exec(
            select(AssignmentStatus).where(AssignmentStatus.assignment_id == assignment_id)
        ).all()
        user_ids = {s.student_id for s in statuses}
        # also include students who have file submissions but no status yet
        user_ids.update({s.student_id for s in subs})
        users_by_id = {}
        if user_ids:
            users = session.exec(select(User).where(User.id.in_(user_ids))).all()
            users_by_id = {u.id: u for u in users}
        done_statuses = [s for s in statuses if s.status == 'done']
        almost_statuses = [s for s in statuses if s.status == 'almost']
        rebel_statuses = [s for s in statuses if s.status == 'rebel']
        return templates.TemplateResponse(
            'assignment_submissions.html',
            {
                'request': request,
                'assignment': a,
                'submissions': subs,
                'classroom_id': a.classroom_id,
                'done_statuses': done_statuses,
                'almost_statuses': almost_statuses,
                'rebel_statuses': rebel_statuses,
                'users_by_id': users_by_id,
            },
        )


@app.post('/submissions/{submission_id}/grade')
def grade_submission(request: Request, submission_id: int, grade: float = Form(...), feedback: Optional[str] = Form(None), csrf_token: Optional[str] = Form(None), current_user: User = Depends(get_current_user)):
    validate_csrf(request, csrf_token)
    with Session(engine) as session:
        s = session.get(Submission, submission_id)
        if not s:
            raise HTTPException(status_code=404, detail='Submission not found')
        a = session.get(Assignment, s.assignment_id)
        stmt = select(Membership).where((Membership.user_id == current_user.id) & (Membership.classroom_id == a.classroom_id))
        m = session.exec(stmt).first()
        if not m or m.role not in ('teacher', 'admin'):
            raise HTTPException(status_code=403, detail='Only teacher/admin can grade')
        s.grade = float(grade)
        s.feedback = feedback
        session.add(s)
        session.commit()
    return RedirectResponse(f"/assignments/{a.id}/submissions", status_code=303)


@app.post('/submissions/{submission_id}/autograde')
def autograde_submission(request: Request, submission_id: int, csrf_token: Optional[str] = Form(None), current_user: User = Depends(get_current_user)):
    validate_csrf(request, csrf_token)
    """Trigger autograding for a submission (teacher/admin only)."""
    with Session(engine) as session:
        s = session.get(Submission, submission_id)
        if not s:
            raise HTTPException(status_code=404, detail='Submission not found')
        a = session.get(Assignment, s.assignment_id)
        if not a:
            raise HTTPException(status_code=404, detail='Assignment not found')
        # permission check
        stmt = select(Membership).where((Membership.user_id == current_user.id) & (Membership.classroom_id == a.classroom_id))
        m = session.exec(stmt).first()
        if not m or m.role not in ('teacher', 'admin'):
            raise HTTPException(status_code=403, detail='Only teacher/admin can autograde')

    # try to enqueue; fall back to synchronous
    try:
        jid = enqueue_autograde_submission(submission_id)
        # record analytics event: autograde requested
        with Session(engine) as session:
            from .models import StudentAnalytics
            sa = StudentAnalytics(user_id=s.student_id, classroom_id=a.classroom_id, event_type='autograde_requested', details=f'submission={submission_id};job={jid}')
            session.add(sa)
            session.commit()
        return JSONResponse({'ok': True, 'queued': True, 'job_id': jid})
    except Exception:
        # fallback: run synchronously
        try:
            ai_autograde_submission(submission_id)
            return JSONResponse({'ok': True, 'queued': False, 'message': 'Autograded synchronously'})
        except Exception as e:
            raise HTTPException(status_code=500, detail=f'Autograde failed: {e}')

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
            site_role=getattr(_u, "site_role", None),
        )
    else:
        request.state.current_user = None
    # load categories for the header/hamburger menu (merged from DB + fallback list)
    try:
        request.state.categories = get_available_category_names()
    except Exception:
        request.state.categories = []
    # expose cookie consent preferences to templates
    try:
        consent_raw = None
        if request.cookies:
            consent_raw = request.cookies.get('cookie_consent')
        request.state.cookie_consent = consent_raw
        # parse consent JSON for template use
        try:
            request.state.cookie_consent_parsed = json.loads(consent_raw) if consent_raw else None
        except Exception:
            request.state.cookie_consent_parsed = None
    except Exception:
        request.state.cookie_consent = None
        request.state.cookie_consent_parsed = None
    response = await call_next(request)
    # Ensure a CSRF token cookie is present for form POSTs (accessible to JS)
    try:
        if not request.cookies.get('csrf_token'):
            token = uuid.uuid4().hex
            response.set_cookie('csrf_token', token, samesite='Lax')
    except Exception:
        pass
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


# --- Phase 1: school/classroom/library/assignment endpoints and upload helpers ---
ALLOWED_MIMETYPES = [
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "audio/mpeg",
    "audio/mp3",
    "audio/*",
    "video/mp4",
    "video/*",
    "application/epub+zip",
    "image/png",
    "image/jpeg",
    "image/*",
    "text/plain",
]
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(1024 * 1024 * 1024)))


def safe_filename(name: str) -> str:
    name = os.path.basename(name)
    name = name.replace(" ", "_")
    return name


def validate_and_save_upload(file: UploadFile, dest_dir: str, max_bytes: int = MAX_UPLOAD_BYTES):
    os.makedirs(dest_dir, exist_ok=True)
    fn = safe_filename(file.filename or str(uuid.uuid4()))
    dest_path = os.path.join(dest_dir, fn)
    # basic mimetype check
    mtype = file.content_type or mimetypes.guess_type(fn)[0]
    if mtype:
        ok = any((mtype == allow or allow.endswith("/*") and mtype.split("/")[0] == allow.split("/")[0]) for allow in ALLOWED_MIMETYPES)
        if not ok:
            raise HTTPException(status_code=400, detail=f"disallowed file type: {mtype}")
    # stream to disk with size check
    total = 0
    try:
        with open(dest_path, "wb") as out:
            while True:
                chunk = file.file.read(1024 * 64)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    out.close()
                    try:
                        os.remove(dest_path)
                    except Exception:
                        pass
                    raise HTTPException(status_code=413, detail="file too large")
                out.write(chunk)
    finally:
        try:
            file.file.close()
        except Exception:
            pass
    return fn, mtype or "application/octet-stream", total


def make_signed_token(path: str, expires: int = 3600):
    exp = int(time.time()) + int(expires)
    payload = f"{path}|{exp}"
    sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return urlencode({"p": path, "e": exp, "s": sig})


def verify_signed_token(p: str, e: str, s: str) -> bool:
    try:
        exp = int(e)
        if time.time() > exp:
            return False
        payload = f"{p}|{exp}"
        expected = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, s)
    except Exception:
        return False


def auto_classify_category(session: Session, text: str):
    """Attempt to find or create a Category for the given text.
    Simple heuristic: prefer an existing Category whose name appears in the text
    (case-insensitive). If none found, match a small keyword map and create the
    Category if necessary. Returns a `Category` instance or `None`.
    """
    try:
        if not text:
            return None
        t = (text or '').lower()
        # prefer exact match from existing categories
        try:
            rows = session.exec(select(Category)).all()
        except Exception:
            rows = []
        for c in rows:
            if c and getattr(c, 'name', None) and c.name.lower() in t:
                return c

        # keyword -> canonical category mapping
        keywords = {
            'business': 'Business',
            'technology': 'Technology',
            'tech': 'Technology',
            'design': 'Design',
            'marketing': 'Marketing',
            'education': 'Education',
            'science': 'Science',
            'art': 'Art',
            'finance': 'Finance',
            'health': 'Health',
            'politics': 'Politics',
            'travel': 'Travel',
            'sports': 'Sports',
            'programming': 'Programming',
            'machine learning': 'Machine Learning',
            'data science': 'Data Science',
            'product': 'Product',
            'photography': 'Photography',
            'film': 'Film',
            'music': 'Music',
        }
        for k, canon in keywords.items():
            if k in t:
                cat = session.exec(select(Category).where(Category.name == canon)).first()
                if cat:
                    return cat
                try:
                    newc = Category(name=canon)
                    session.add(newc)
                    session.commit()
                    session.refresh(newc)
                    return newc
                except Exception:
                    try:
                        session.rollback()
                    except Exception:
                        pass
                    cat = session.exec(select(Category).where(Category.name == canon)).first()
                    if cat:
                        return cat
        return None
    except Exception:
        return None


def send_email(to_address: str, subject: str, body: str) -> bool:
    """Send a simple plaintext email using SMTP settings from environment.
    Returns True on success, False otherwise. Non-fatal.
    """
    smtp_host = os.getenv('SMTP_HOST')
    smtp_port = int(os.getenv('SMTP_PORT', '587'))
    smtp_user = os.getenv('SMTP_USER')
    smtp_pass = os.getenv('SMTP_PASS')
    from_addr = os.getenv('EMAIL_FROM', smtp_user or 'noreply@example.com')
    if not smtp_host:
        return False
    msg = f"From: {from_addr}\r\nTo: {to_address}\r\nSubject: {subject}\r\n\r\n{body}"
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.starttls(context=context)
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.sendmail(from_addr, [to_address], msg)
        return True
    except Exception:
        logger.exception('Failed to send email to %s', to_address)
        return False


def validate_csrf(request: Request, token: Optional[str]) -> None:
    """Raise HTTPException 403 if csrf token missing or mismatched."""
    try:
        cookie_token = request.cookies.get('csrf_token')
        header_token = request.headers.get('X-CSRF-Token')
        # prefer explicit form token, fall back to header token for AJAX/API calls
        effective = token or header_token
        if not effective or not cookie_token or effective != cookie_token:
            raise HTTPException(status_code=403, detail='CSRF token missing or invalid')
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=403, detail='CSRF validation failed')


@app.get("/api/presentations/{presentation_id}/signed_url")
def get_presentation_signed_url(presentation_id: int, expires: int = 3600, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        p = session.get(Presentation, presentation_id)
        if not p:
            raise HTTPException(status_code=404, detail="presentation not found")
        # Only owner or teachers can generate signed URLs for class-owned files
        if p.owner_id != current_user.id:
            # allow teachers to generate if this presentation is linked to a LibraryItem in a classroom where they are teacher
            li = session.exec(select(__import__("app.models").models.LibraryItem).where(__import__("app.models").models.LibraryItem.presentation_id == p.id)).first()
            if not li:
                raise HTTPException(status_code=403, detail="forbidden")
            mem = session.exec(select(__import__("app.models").models.Membership).where((__import__("app.models").models.Membership.classroom_id == li.classroom_id) & (__import__("app.models").models.Membership.user_id == current_user.id))).first()
            if not mem or mem.role not in ("teacher", "admin"):
                raise HTTPException(status_code=403, detail="forbidden")
        # prefer any background conversion/transcode result (may be local or S3)
        job = session.exec(
            select(ConversionJob)
            .where(ConversionJob.presentation_id == p.id)
            .order_by(ConversionJob.created_at.desc())
        ).first()
        if job and getattr(job, 'result', None):
            res = job.result
            if isinstance(res, str) and res.startswith("s3://") and boto3 is not None:
                # s3://bucket/key
                try:
                    parts = res[len("s3://"):].split("/", 1)
                    bucket = parts[0]
                    key = parts[1] if len(parts) > 1 else ""
                    s3 = boto3.client(
                        "s3",
                        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                        region_name=os.getenv("AWS_REGION"),
                    )
                    url = s3.generate_presigned_url(
                        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=int(expires)
                    )
                    return JSONResponse({"url": url})
                except Exception:
                    pass
            else:
                # local relative path under UPLOAD_DIR (e.g., "hls/123/index.m3u8" or "123_web.mp4")
                path = f"/uploads/{res}"
                token = make_signed_token(path, expires)
                return JSONResponse({"url": f"/download_signed?{token}"})

        # fallback to original filename
        if not p.filename:
            raise HTTPException(status_code=404, detail="file missing")
        path = f"/uploads/{p.filename}"
        token = make_signed_token(path, expires)
        return JSONResponse({"url": f"/download_signed?{token}"})
    
    @app.get('/my/teachers', response_class=HTMLResponse)
    def my_teachers(request: Request, current_user: User = Depends(get_current_user)):
        """List teachers for the current student across their classrooms."""
        with Session(engine) as session:
            # find classrooms where current_user is a student
            cls_ids = [m.classroom_id for m in session.exec(select(Membership).where(Membership.user_id == current_user.id)).all() if m.role == 'student']
            if not cls_ids:
                teachers = []
            else:
                teachers_mem = session.exec(select(Membership).where((Membership.classroom_id.in_(cls_ids)) & (Membership.role.in_(['teacher','admin'])))).all()
                teacher_ids = sorted({m.user_id for m in teachers_mem})
                teachers = []
                for tid in teacher_ids:
                    u = session.get(User, tid)
                    if u:
                        teachers.append({'id': u.id, 'username': getattr(u, 'username', None), 'email': getattr(u, 'email', None)})
        return templates.TemplateResponse('my_teachers.html', {'request': request, 'teachers': teachers})

    @app.get('/teachers/{teacher_id}/presentations', response_class=HTMLResponse)
    def teacher_presentations(request: Request, teacher_id: int, current_user: Optional[User] = Depends(get_current_user)):
        """Show presentations authored by a teacher. Visible to all users.
        If the current_user is a student, this provides the teacher-only view."""
        with Session(engine) as session:
            teacher = session.get(User, teacher_id)
            if not teacher:
                raise HTTPException(status_code=404, detail='Teacher not found')
            pres = session.exec(select(Presentation).where(Presentation.owner_id == teacher_id).order_by(Presentation.created_at.desc())).all()
        return templates.TemplateResponse('teacher_presentations.html', {'request': request, 'teacher': teacher, 'presentations': pres})


    def _make_invite_token(payload: dict) -> str:
        """Create a signed token for invitation payload.
        Token format: base64url(json).base64url(hmac_sha256)
        """
        import json, hmac, hashlib, base64
        secret = os.getenv('SECRET_KEY', 'devsecret')
        raw = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8')
        sig = hmac.new(secret.encode('utf-8'), raw, hashlib.sha256).digest()
        braw = base64.urlsafe_b64encode(raw).decode('utf-8')
        bsig = base64.urlsafe_b64encode(sig).decode('utf-8')
        return f"{braw}.{bsig}"


    def _verify_invite_token(token: str, max_age: int = 60 * 60 * 24 * 7):
        import json, hmac, hashlib, base64, time
        secret = os.getenv('SECRET_KEY', 'devsecret')
        try:
            braw, bsig = token.split('.')
            raw = base64.urlsafe_b64decode(braw.encode('utf-8'))
            sig = base64.urlsafe_b64decode(bsig.encode('utf-8'))
            expected = hmac.new(secret.encode('utf-8'), raw, hashlib.sha256).digest()
            if not hmac.compare_digest(expected, sig):
                return None
            payload = json.loads(raw.decode('utf-8'))
            ts = payload.get('ts') or 0
            if int(time.time()) - int(ts) > max_age:
                return None
            return payload
        except Exception:
            return None


    @app.post('/invite-student')
    def invite_student(request: Request, classroom_id: int = Form(...), email: str = Form(...), csrf_token: Optional[str] = Form(None), current_user: User = Depends(get_current_user)):
        """Teacher/admin invites a student by email to join a classroom. Sends accept/decline links."""
        validate_csrf(request, csrf_token)
        with Session(engine) as session:
            c = session.get(Classroom, classroom_id)
            if not c:
                raise HTTPException(status_code=404, detail='Classroom not found')
            # check current_user is teacher/admin in classroom
            mem = session.exec(select(Membership).where((Membership.classroom_id == classroom_id) & (Membership.user_id == current_user.id) & (Membership.role.in_(['teacher', 'admin'])))).first()
            if not mem:
                raise HTTPException(status_code=403, detail='Only teacher/admin can invite')
            # build token
            import time
            payload = {'inviter_id': current_user.id, 'classroom_id': classroom_id, 'email': email, 'ts': int(time.time())}
            token = _make_invite_token(payload)
            # build accept/decline links
            try:
                accept = request.url_for('invitations_respond') + f"?token={token}&action=accept"
                decline = request.url_for('invitations_respond') + f"?token={token}&action=decline"
            except Exception:
                base = str(request.base_url).rstrip('/')
                accept = f"{base}/invitations/respond?token={token}&action=accept"
                decline = f"{base}/invitations/respond?token={token}&action=decline"

            # send email via queue
            try:
                ctx = {'inviter_name': getattr(current_user, 'username', ''), 'classroom_name': getattr(c, 'name', ''), 'accept_url': accept, 'decline_url': decline}
                enqueue_email(email, 'Invitation to join classroom', None, 'emails/invite_student.html', ctx)
            except Exception:
                pass
        return RedirectResponse(f"/classrooms/{classroom_id}", status_code=303)


    @app.get('/invitations/respond', response_class=HTMLResponse)
    def invitations_respond(request: Request, token: str = Query(...), action: str = Query(...)):
        p = _verify_invite_token(token)
        if not p:
            return HTMLResponse('<p>Invalid or expired invitation token.</p>', status_code=400)
        email = p.get('email')
        classroom_id = p.get('classroom_id')
        with Session(engine) as session:
            u = session.exec(select(User).where(User.email == email)).first()
            if action == 'accept':
                if not u:
                    # redirect to register with invite token
                    return RedirectResponse(f"/register?invite_token={token}")
                # add membership if not exists
                exists = session.exec(select(Membership).where((Membership.user_id == u.id) & (Membership.classroom_id == classroom_id))).first()
                if not exists:
                    m = Membership(user_id=u.id, classroom_id=classroom_id, role='student')
                    session.add(m)
                    session.commit()
                return HTMLResponse('<p>Thanks — you have been added to the classroom. You can <a href="/">return to the site</a>.</p>')
            else:
                return HTMLResponse('<p>You declined the invitation. No changes made.</p>')



        @app.post('/choose-role')
        def choose_role(request: Request, role: str = Form(...), invite_token: Optional[str] = Form(None), current_user: Optional[User] = Depends(get_current_user_optional)):
            """Persist a visitor's role choice in a cookie and to the user profile when logged in.
            Also process an optional invite token (create Membership) and preserve invite flow."""
            # persist cookie for client-side convenience
            resp = RedirectResponse('/', status_code=303)
            resp.set_cookie('user_role', role, max_age=30*24*60*60, path='/')
            resp.set_cookie('role_selected', 'true', max_age=30*24*60*60, path='/')
            # persist to DB if logged-in
            try:
                if current_user:
                    with Session(engine) as session:
                        u = session.get(User, current_user.id)
                        if u:
                            u.site_role = role
                            session.add(u)
                            session.commit()
            except Exception:
                pass

            # process invite token if present (add membership for this user)
            if invite_token and current_user:
                try:
                    payload = _verify_invite_token(invite_token)
                    if payload and payload.get('email') == current_user.email:
                        classroom_id = payload.get('classroom_id')
                        with Session(engine) as session:
                            exists = session.exec(select(Membership).where((Membership.user_id == current_user.id) & (Membership.classroom_id == classroom_id))).first()
                            if not exists:
                                m = Membership(user_id=current_user.id, classroom_id=classroom_id, role='student')
                                session.add(m)
                                session.commit()
                except Exception:
                    pass

            return resp


        @app.get('/choose-role', response_class=HTMLResponse)
        def choose_role_get(request: Request, current_user: Optional[User] = Depends(get_current_user)):
            """Render a server-side role selection page for new users."""
            csrf = None
            try:
                csrf = request.cookies.get('csrf_token')
            except Exception:
                csrf = None
            return templates.TemplateResponse('choose_role.html', {'request': request, 'csrf_token': csrf, 'current_user': current_user})


        @app.get('/account/settings', response_class=HTMLResponse)
        def account_settings_get(request: Request, current_user: User = Depends(get_current_user)):
            return templates.TemplateResponse('account_settings.html', {'request': request, 'current_user': current_user})


        @app.post('/account/settings')
        def account_settings_post(request: Request, role: str = Form(...), current_user: User = Depends(get_current_user)):
            with Session(engine) as session:
                u = session.get(User, current_user.id)
                if not u:
                    raise HTTPException(status_code=404, detail='User not found')
                u.site_role = role
                session.add(u)
                session.commit()
            resp = RedirectResponse('/account/settings', status_code=303)
            resp.set_cookie('user_role', role, max_age=30*24*60*60, path='/')
            return resp


@app.get("/download_signed")
def download_signed(request: Request, p: str = Query(...), e: str = Query(...), s: str = Query(...)):
    # verify signature
    ok = verify_signed_token(p, e, s)
    if not ok:
        raise HTTPException(status_code=403, detail="invalid or expired token")
    if not p.startswith("/uploads/"):
        raise HTTPException(status_code=400, detail="invalid path")
    rel = p[len("/uploads/"):]
    disk = os.path.join(UPLOAD_DIR, rel)
    if not os.path.exists(disk):
        raise HTTPException(status_code=404, detail="file not found")

    file_size = os.path.getsize(disk)
    range_header = request.headers.get("range")
    if not range_header:
        # no range requested — serve full file
        return FileResponse(disk, media_type=mimetypes.guess_type(disk)[0] or "application/octet-stream")

    # parse range: bytes=start-end
    try:
        units, rng = range_header.split("=")
        if units != "bytes":
            raise ValueError()
        start_str, end_str = rng.split("-")
        start = int(start_str) if start_str else 0
        end = int(end_str) if end_str else file_size - 1
        if start >= file_size:
            raise HTTPException(status_code=416, detail="Range Not Satisfiable")
        end = min(end, file_size - 1)
        length = end - start + 1
    except Exception:
        raise HTTPException(status_code=400, detail="invalid Range header")

    def iterfile(path, start, length):
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            chunk_size = 1024 * 64
            while remaining > 0:
                read_size = min(chunk_size, remaining)
                data = f.read(read_size)
                if not data:
                    break
                remaining -= len(data)
                yield data

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
    }
    media_type = mimetypes.guess_type(disk)[0] or "application/octet-stream"
    return StreamingResponse(iterfile(disk, start, length), status_code=206, media_type=media_type, headers=headers)


@app.post("/api/schools")
def create_school(payload: dict = Body(...), current_user: User = Depends(get_current_user)):
    name = (payload.get("name") or "").strip()
    slug = (payload.get("slug") or "").strip() or None
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    with Session(engine) as session:
        s = session.exec(select(Category).where(False)).first()  # noop to ensure session import
        school = session.exec(select(__import__("app.models").models.School).where(__import__("app.models").models.School.name == name)).first()
        if school:
            raise HTTPException(status_code=409, detail="school exists")
        School = __import__("app.models").models.School
        school = School(name=name, slug=slug)
        session.add(school)
        session.commit()
        session.refresh(school)
        return JSONResponse({"ok": True, "school": {"id": school.id, "name": school.name}})


@app.get("/api/schools")
def list_schools():
    School = __import__("app.models").models.School
    with Session(engine) as session:
        rows = session.exec(select(School).order_by(School.name)).all()
        out = [{"id": r.id, "name": r.name, "slug": r.slug} for r in rows]
        return JSONResponse({"schools": out})


@app.post("/api/classrooms")
def create_classroom(payload: dict = Body(...), current_user: User = Depends(get_current_user)):
    School = __import__("app.models").models.School
    Classroom = __import__("app.models").models.Classroom
    Membership = __import__("app.models").models.Membership
    name = (payload.get("name") or "").strip()
    school_id = payload.get("school_id")
    if not name or not school_id:
        raise HTTPException(status_code=400, detail="name and school_id required")
    with Session(engine) as session:
        school = session.get(School, school_id)
        if not school:
            raise HTTPException(status_code=404, detail="school not found")
        c = Classroom(school_id=school_id, name=name, code=payload.get("code"))
        session.add(c)
        session.commit()
        session.refresh(c)
        # add creator as teacher
        m = Membership(user_id=current_user.id, classroom_id=c.id, role="teacher")
        session.add(m)
        session.commit()
        return JSONResponse({"ok": True, "classroom": {"id": c.id, "name": c.name}})


@app.post("/api/classrooms/{classroom_id}/join")
def join_classroom(classroom_id: int, payload: dict = Body(...), current_user: User = Depends(get_current_user)):
    Membership = __import__("app.models").models.Membership
    Classroom = __import__("app.models").models.Classroom
    role = payload.get("role") or "student"
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail="classroom not found")
        exists = session.exec(select(Membership).where((Membership.classroom_id == classroom_id) & (Membership.user_id == current_user.id))).first()
        if exists:
            return JSONResponse({"ok": True, "membership_id": exists.id})
        m = Membership(user_id=current_user.id, classroom_id=classroom_id, role=role)
        session.add(m)
        session.commit()
        session.refresh(m)
        return JSONResponse({"ok": True, "membership_id": m.id})


@app.post("/api/classrooms/{classroom_id}/library")
def upload_library_item(classroom_id: int, title: str = Form(None), file: UploadFile = File(...), current_user: User = Depends(get_current_user)):
    # save file under uploads/classrooms/<id>/library
    LibraryItem = __import__("app.models").models.LibraryItem
    PresentationModel = __import__("app.models").models.Presentation
    Classroom = __import__("app.models").models.Classroom
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail="classroom not found")
        # basic membership check: must be teacher to upload to class library
        mem = session.exec(select(__import__("app.models").models.Membership).where((__import__("app.models").models.Membership.classroom_id == classroom_id) & (__import__("app.models").models.Membership.user_id == current_user.id))).first()
        if not mem or mem.role not in ("teacher", "admin"):
            raise HTTPException(status_code=403, detail="only teachers can upload to class library")
        dest_dir = os.path.join(UPLOAD_DIR, "classrooms", str(classroom_id), "library")
        fn, mtype, size = validate_and_save_upload(file, dest_dir)
        # create Presentation so it can be previewed in the app
        p = PresentationModel(title=title or file.filename or "Library File", filename=os.path.join("classrooms", str(classroom_id), "library", fn), mimetype=mtype, file_size=size, owner_id=current_user.id)
        session.add(p)
        session.commit()
        session.refresh(p)
        li = LibraryItem(classroom_id=classroom_id, presentation_id=p.id, title=title or p.title, filename=p.filename, mimetype=mtype, uploaded_by=current_user.id)
        session.add(li)
        session.commit()
        session.refresh(li)
        # analytics: classroom material upload (API flow)
        try:
            sa = StudentAnalytics(user_id=current_user.id, classroom_id=classroom_id, event_type="upload", details=f"library_item={li.id}")
            session.add(sa)
            session.commit()
        except Exception:
            session.rollback()
        # also emit a classroom chat message so the upload shows up
        # in the group conversation
        try:
            msg_text = f"uploaded new classroom material: {li.title or (file.filename or 'File')}"
            cm = ClassroomMessage(classroom_id=classroom_id, sender_id=current_user.id, content=msg_text)
            session.add(cm)
            session.commit()
        except Exception:
            session.rollback()
        return JSONResponse({"ok": True, "library_item": {"id": li.id, "presentation_id": p.id}})


@app.post("/api/classrooms/{classroom_id}/assignments")
def create_assignment(classroom_id: int, payload: dict = Body(...), current_user: User = Depends(get_current_user)):
    Assignment = __import__("app.models").models.Assignment
    Membership = __import__("app.models").models.Membership
    Classroom = __import__("app.models").models.Classroom
    NotificationModel = __import__("app.models").models.Notification
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title required")
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail="classroom not found")
        mem = session.exec(select(Membership).where((Membership.classroom_id == classroom_id) & (Membership.user_id == current_user.id))).first()
        if not mem or mem.role not in ("teacher", "admin"):
            raise HTTPException(status_code=403, detail="only teachers can create assignments")
        a = Assignment(classroom_id=classroom_id, title=title, description=payload.get("description"), due_date=payload.get("due_date"), created_by=current_user.id)
        session.add(a)
        session.commit()
        session.refresh(a)
        # Notify classroom students about the new assignment
        student_mems = session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.role == 'student')
            )
        ).all()
        for sm in student_mems:
            n = NotificationModel(
                recipient_id=sm.user_id,
                actor_id=current_user.id,
                verb='new_assignment',
                target_type='assignment',
                target_id=a.id,
            )
            session.add(n)
        session.commit()
        return JSONResponse({"ok": True, "assignment": {"id": a.id, "title": a.title}})


@app.post("/api/assignments/{assignment_id}/submit")
def submit_assignment(assignment_id: int, file: UploadFile = File(...), current_user: User = Depends(get_current_user)):
    Submission = __import__("app.models").models.Submission
    Assignment = __import__("app.models").models.Assignment
    with Session(engine) as session:
        a = session.get(Assignment, assignment_id)
        if not a:
            raise HTTPException(status_code=404, detail="assignment not found")
        dest_dir = os.path.join(UPLOAD_DIR, "submissions", str(assignment_id), str(current_user.id))
        fn, mtype, size = validate_and_save_upload(file, dest_dir)
        s = Submission(assignment_id=assignment_id, student_id=current_user.id, filename=os.path.join("submissions", str(assignment_id), str(current_user.id), fn), mimetype=mtype)
        session.add(s)
        session.commit()
        session.refresh(s)
        return JSONResponse({"ok": True, "submission_id": s.id})


@app.post("/api/submissions/{submission_id}/grade")
def grade_submission(submission_id: int, payload: dict = Body(...), current_user: User = Depends(get_current_user)):
    Submission = __import__("app.models").models.Submission
    Assignment = __import__("app.models").models.Assignment
    Membership = __import__("app.models").models.Membership
    with Session(engine) as session:
        s = session.get(Submission, submission_id)
        if not s:
            raise HTTPException(status_code=404, detail="submission not found")
        a = session.get(Assignment, s.assignment_id)
        if not a:
            raise HTTPException(status_code=404, detail="assignment not found")
        # check grader is a teacher in the assignment's classroom
        mem = session.exec(select(Membership).where((Membership.classroom_id == a.classroom_id) & (Membership.user_id == current_user.id))).first()
        if not mem or mem.role not in ("teacher", "admin"):
            raise HTTPException(status_code=403, detail="only teachers can grade")
        s.grade = float(payload.get("grade")) if payload.get("grade") is not None else None
        s.feedback = payload.get("feedback")
        session.add(s)
        session.commit()
        return JSONResponse({"ok": True, "submission_id": s.id, "grade": s.grade})


@app.post("/api/classrooms/{classroom_id}/attendance")
def mark_attendance(classroom_id: int, payload: dict = Body(...), current_user: User = Depends(get_current_user)):
    Attendance = __import__("app.models").models.Attendance
    Membership = __import__("app.models").models.Membership
    Classroom = __import__("app.models").models.Classroom
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail="classroom not found")
        # only teachers/admins can mark attendance
        mem = session.exec(select(Membership).where((Membership.classroom_id == classroom_id) & (Membership.user_id == current_user.id))).first()
        if not mem or mem.role not in ("teacher", "admin"):
            raise HTTPException(status_code=403, detail="only teachers can mark attendance")
        entries = payload.get("entries") or []  # list of {user_id: int, status: str}
        out = []
        for e in entries:
            uid = e.get("user_id")
            status = e.get("status") or "present"
            at = Attendance(classroom_id=classroom_id, user_id=uid, status=status, date=datetime.utcnow())
            session.add(at)
            session.commit()
            session.refresh(at)
            out.append({"id": at.id, "user_id": uid, "status": status})
        return JSONResponse({"ok": True, "marked": out})


@app.get("/api/classrooms/{classroom_id}/members")
def list_classroom_members(classroom_id: int, current_user: User = Depends(get_current_user)):
    Membership = __import__("app.models").models.Membership
    UserModel = __import__("app.models").models.User
    Classroom = __import__("app.models").models.Classroom
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail="classroom not found")
        rows = session.exec(select(Membership).where(Membership.classroom_id == classroom_id)).all()
        out = []
        for m in rows:
            u = session.get(UserModel, m.user_id)
            out.append({"user_id": m.user_id, "role": m.role, "username": getattr(u, "username", None)})
        return JSONResponse({"members": out})


@app.get("/api/spaces/{space_id}/members")
def list_space_members(space_id: int, current_user: User = Depends(get_current_user)):
    MembershipModel = __import__("app.models").models.Membership
    UserModel = __import__("app.models").models.User
    SpaceModel = __import__("app.models").models.Space
    ClassroomModel = __import__("app.models").models.Classroom
    with Session(engine) as session:
        s = session.get(SpaceModel, space_id)
        if not s:
            # fallback: allow spaces to resolve from legacy classroom ids during transition
            c = session.get(ClassroomModel, space_id)
            if not c:
                raise HTTPException(status_code=404, detail="space not found")
        rows = session.exec(
            select(MembershipModel).where(
                (MembershipModel.space_id == space_id)
                | (MembershipModel.classroom_id == space_id)
            )
        ).all()
        out = []
        for m in rows:
            u = session.get(UserModel, m.user_id)
            out.append({"user_id": m.user_id, "role": m.role, "username": getattr(u, "username", None)})
        return JSONResponse({"members": out})


@app.get("/api/classrooms/{classroom_id}/library")
def list_classroom_library(classroom_id: int, current_user: User = Depends(get_current_user)):
    LibraryItem = __import__("app.models").models.LibraryItem
    PresentationModel = __import__("app.models").models.Presentation
    Classroom = __import__("app.models").models.Classroom
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail="classroom not found")
        teacher_ids = [mem.user_id for mem in session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.role.in_(['teacher', 'admin']))
            )
        ).all()]
        items = session.exec(
            select(LibraryItem)
            .where(
                (LibraryItem.classroom_id == classroom_id)
                & (LibraryItem.uploaded_by.in_(teacher_ids))
            )
            .order_by(LibraryItem.created_at.desc())
        ).all()
        out = []
        for it in items:
            pres = session.get(PresentationModel, it.presentation_id) if it.presentation_id else None
            out.append({"id": it.id, "title": it.title, "filename": it.filename, "mimetype": it.mimetype, "presentation": {"id": pres.id, "title": pres.title} if pres else None})
        return JSONResponse({"library": out})


@app.get("/api/classrooms/{classroom_id}/assignments")
def list_classroom_assignments(classroom_id: int, current_user: User = Depends(get_current_user)):
    Assignment = __import__("app.models").models.Assignment
    Classroom = __import__("app.models").models.Classroom
    Membership = __import__("app.models").models.Membership
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail="classroom not found")
        # only members can view assignments
        mem = session.exec(select(Membership).where((Membership.classroom_id == classroom_id) & (Membership.user_id == current_user.id))).first()
        if not mem:
            raise HTTPException(status_code=403, detail="not a member")
        rows = session.exec(select(Assignment).where(Assignment.classroom_id == classroom_id).order_by(Assignment.created_at.desc())).all()
        out = [{"id": r.id, "title": r.title, "description": r.description, "due_date": getattr(r, "due_date", None)} for r in rows]
        return JSONResponse({"assignments": out})


@app.get("/api/assignments/{assignment_id}/submissions")
def list_submissions_for_assignment(assignment_id: int, current_user: User = Depends(get_current_user)):
    Submission = __import__("app.models").models.Submission
    Assignment = __import__("app.models").models.Assignment
    Membership = __import__("app.models").models.Membership
    with Session(engine) as session:
        a = session.get(Assignment, assignment_id)
        if not a:
            raise HTTPException(status_code=404, detail="assignment not found")
        # teacher/admins can view all submissions; students can view their own
        mem = session.exec(select(Membership).where((Membership.classroom_id == a.classroom_id) & (Membership.user_id == current_user.id))).first()
        if not mem:
            raise HTTPException(status_code=403, detail="not a member")
        if mem.role in ("teacher", "admin"):
            subs = session.exec(select(Submission).where(Submission.assignment_id == assignment_id).order_by(Submission.created_at.desc())).all()
        else:
            subs = session.exec(select(Submission).where((Submission.assignment_id == assignment_id) & (Submission.student_id == current_user.id)).order_by(Submission.created_at.desc())).all()
        out = [{"id": s.id, "student_id": s.student_id, "filename": s.filename, "mimetype": s.mimetype, "grade": s.grade, "feedback": s.feedback} for s in subs]
        return JSONResponse({"submissions": out})


    @app.post('/api/submissions/{submission_id}/autograde')
    def autograde_submission(submission_id: int, current_user: User = Depends(get_current_user)):
        Submission = __import__("app.models").models.Submission
        Assignment = __import__("app.models").models.Assignment
        Membership = __import__("app.models").models.Membership
        with Session(engine) as session:
            s = session.get(Submission, submission_id)
            if not s:
                raise HTTPException(status_code=404, detail='submission not found')
            a = session.get(Assignment, s.assignment_id)
            if not a:
                raise HTTPException(status_code=404, detail='assignment not found')
            # only teachers/admins can autograde
            mem = session.exec(select(Membership).where((Membership.classroom_id == a.classroom_id) & (Membership.user_id == current_user.id))).first()
            if not mem or mem.role not in ('teacher', 'admin'):
                raise HTTPException(status_code=403, detail='only teachers can autograde')
            # attempt AI-based grading if key present
            grade = None
            feedback = None
            from pathlib import Path
            UPLOAD_DIR = os.getenv('UPLOAD_DIR', './uploads')
            file_path = Path(UPLOAD_DIR) / s.filename if s.filename else None
            try:
                if file_path and file_path.exists():
                    content = ''
                    if s.mimetype and s.mimetype.startswith('text'):
                        content = file_path.read_text(encoding='utf-8', errors='ignore')[:4000]
                    else:
                        content = f"Student submission file: {file_path.name}, type={s.mimetype}"
                    out = chat_completion(
                        [{"role": "user", "content": f"Grade this student submission on a scale 0-100 and provide brief feedback. Submission content:\n{content}"}],
                        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                        max_tokens=200,
                        temperature=0.3,
                    )
                    import re
                    m = re.search(r'([0-9]{1,3})', out)
                    if m:
                        grade = float(m.group(1))
                    feedback = out
                if grade is None:
                    if file_path and file_path.exists():
                        size = file_path.stat().st_size
                        grade = 80.0 if size > 1000 else 60.0
                        feedback = 'Auto-graded: basic heuristic based on file size.'
                    else:
                        grade = 0.0
                        feedback = 'No file to grade.'
            except Exception as e:
                grade = 0.0
                feedback = f'Autograde failed: {e}'
            s.grade = float(grade)
            s.feedback = feedback
            session.add(s)
            session.commit()
            return JSONResponse({'ok': True, 'submission_id': s.id, 'grade': s.grade, 'feedback': s.feedback})


@app.get("/api/classrooms/{classroom_id}/attendance")
def list_attendance(classroom_id: int, date: Optional[str] = Query(None), current_user: User = Depends(get_current_user)):
    Attendance = __import__("app.models").models.Attendance
    Membership = __import__("app.models").models.Membership
    Classroom = __import__("app.models").models.Classroom
    from datetime import datetime
    with Session(engine) as session:
        c = session.get(Classroom, classroom_id)
        if not c:
            raise HTTPException(status_code=404, detail="classroom not found")
        mem = session.exec(select(Membership).where((Membership.classroom_id == classroom_id) & (Membership.user_id == current_user.id))).first()
        if not mem:
            raise HTTPException(status_code=403, detail="not a member")
        q = select(Attendance).where(Attendance.classroom_id == classroom_id)
        if date:
            try:
                dt = datetime.fromisoformat(date)
                # match date only
                start = datetime(dt.year, dt.month, dt.day)
                end = start.replace(hour=23, minute=59, second=59)
                q = q.where((Attendance.date >= start) & (Attendance.date <= end))
            except Exception:
                raise HTTPException(status_code=400, detail="invalid date")
        rows = session.exec(q.order_by(Attendance.date.desc())).all()
        out = [{"id": r.id, "user_id": r.user_id, "status": r.status, "date": r.date.isoformat()} for r in rows]
        return JSONResponse({"attendance": out})


@app.post('/api/presentations/{presentation_id}/ai/summary')
def enqueue_presentation_summary(presentation_id: int, current_user: User = Depends(get_current_user)):
    # any authenticated user can request AI summary for a presentation that exists
    with Session(engine) as session:
        p = session.get(Presentation, presentation_id)
        if not p:
            raise HTTPException(status_code=404, detail='presentation not found')
    try:
        job_id = enqueue_ai_summary(presentation_id)
        return JSONResponse({'ok': True, 'job_id': job_id})
    except Exception:
        # fallback: if queue/redis isn't available, run synchronously so users still get a result
        try:
            from .tasks import ai_summarize_presentation
            ai_summarize_presentation(presentation_id)
            return JSONResponse({'ok': True, 'job_id': None, 'note': 'ran synchronously'})
        except Exception:
            raise HTTPException(status_code=500, detail='failed to enqueue or run AI summary')


@app.post('/api/presentations/{presentation_id}/ai/quiz')
def enqueue_presentation_quiz(presentation_id: int, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        p = session.get(Presentation, presentation_id)
        if not p:
            raise HTTPException(status_code=404, detail='presentation not found')
    try:
        job_id = enqueue_ai_quiz(presentation_id)
        return JSONResponse({'ok': True, 'job_id': job_id})
    except Exception:
        try:
            from .tasks import ai_generate_quiz
            ai_generate_quiz(presentation_id)
            return JSONResponse({'ok': True, 'job_id': None, 'note': 'ran synchronously'})
        except Exception:
            raise HTTPException(status_code=500, detail='failed to enqueue or run AI quiz')


@app.post('/api/presentations/{presentation_id}/ai/flashcards')
def enqueue_presentation_flashcards(presentation_id: int, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        p = session.get(Presentation, presentation_id)
        if not p:
            raise HTTPException(status_code=404, detail='presentation not found')
    try:
        job_id = enqueue_ai_flashcards(presentation_id)
        return JSONResponse({'ok': True, 'job_id': job_id})
    except Exception:
        try:
            from .tasks import ai_generate_flashcards
            ai_generate_flashcards(presentation_id)
            return JSONResponse({'ok': True, 'job_id': None, 'note': 'ran synchronously'})
        except Exception:
            raise HTTPException(status_code=500, detail='failed to enqueue or run AI flashcards')


@app.post('/api/presentations/{presentation_id}/ai/mindmap')
def enqueue_presentation_mindmap(presentation_id: int, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        p = session.get(Presentation, presentation_id)
        if not p:
            raise HTTPException(status_code=404, detail='presentation not found')
    try:
        job_id = enqueue_ai_mindmap(presentation_id)
        return JSONResponse({'ok': True, 'job_id': job_id})
    except Exception:
        try:
            from .tasks import ai_generate_mindmap
            ai_generate_mindmap(presentation_id)
            return JSONResponse({'ok': True, 'job_id': None, 'note': 'ran synchronously'})
        except Exception:
            raise HTTPException(status_code=500, detail='failed to enqueue or run AI mindmap')


@app.get('/api/presentations/{presentation_id}/ai/results')
def list_presentation_ai_results(presentation_id: int, current_user: User = Depends(get_current_user)):
    AIResult = __import__("app.models").models.AIResult
    with Session(engine) as session:
        rows = session.exec(select(AIResult).where(AIResult.presentation_id == presentation_id).order_by(AIResult.created_at.desc())).all()
        out = [{"id": r.id, "task_type": r.task_type, "result": r.result, "created_at": r.created_at.isoformat()} for r in rows]
        return JSONResponse({'results': out})


@app.post('/api/presentations/{presentation_id}/convert')
def trigger_presentation_conversion(presentation_id: int, current_user: User = Depends(get_current_user)):
    """Trigger conversion/preview generation for a presentation.
    Allows owner or classroom teachers to request conversion. Attempts to enqueue a background job
    and falls back to synchronous conversion if the queue is unavailable.
    """
    PresentationModel = __import__("app.models").models.Presentation
    LibraryItem = __import__("app.models").models.LibraryItem
    Membership = __import__("app.models").models.Membership
    with Session(engine) as session:
        p = session.get(PresentationModel, presentation_id)
        if not p:
            raise HTTPException(status_code=404, detail='presentation not found')
        # permission: owner or teacher in a classroom that contains this presentation
        if p.owner_id != current_user.id:
            li = session.exec(select(LibraryItem).where(LibraryItem.presentation_id == p.id)).first()
            if not li:
                raise HTTPException(status_code=403, detail='forbidden')
            mem = session.exec(select(Membership).where((Membership.classroom_id == li.classroom_id) & (Membership.user_id == current_user.id))).first()
            if not mem or mem.role not in ("teacher", "admin"):
                raise HTTPException(status_code=403, detail='forbidden')
    if not getattr(p, 'filename', None):
        raise HTTPException(status_code=404, detail='file missing')
    # attempt to enqueue
    try:
        job_id = enqueue_conversion(presentation_id, p.filename)
        return JSONResponse({'ok': True, 'job_id': job_id})
    except Exception:
        # fallback to synchronous conversion for environments without Redis/workers
        try:
            from .tasks import convert_presentation
            convert_presentation(presentation_id, p.filename)
            return JSONResponse({'ok': True, 'job_id': None, 'note': 'ran synchronously'})
        except Exception:
            raise HTTPException(status_code=500, detail='failed to enqueue or run conversion')


    @app.post('/api/cookie_consent')
    def log_cookie_consent(request: Request, payload: dict = Body(...), current_user: Optional[User] = Depends(get_current_user_optional)):
        # payload expected: { consent: { ... } }
        consent = payload.get('consent')
        if consent is None:
            return JSONResponse({'error': 'missing consent'}, status_code=400)
        try:
            ip = request.client.host if request.client else None
        except Exception:
            ip = None
        ua = request.headers.get('user-agent')
        with Session(engine) as session:
            cl = ConsentLog(user_id=getattr(current_user, 'id', None) if current_user else None, consent=json.dumps(consent), ip=ip, ua=ua)
            session.add(cl)
            session.commit()
            session.refresh(cl)
        return JSONResponse({'ok': True, 'id': cl.id})


@app.get("/", response_class=HTMLResponse)
def index(request: Request, q: str = ""):
    current_user = get_current_user_optional(request)

    # Role-checker: for first-time visitors without a stored role, show the
    # role picker instead of the main feed.
    try:
        cookie_role = request.cookies.get("user_role") if hasattr(request, "cookies") else None
    except Exception:
        cookie_role = None
    user_role = getattr(current_user, "site_role", None) if current_user else None
    if not cookie_role and not user_role:
        csrf = None
        try:
            csrf = request.cookies.get("csrf_token") if hasattr(request, "cookies") else None
        except Exception:
            csrf = None
        return templates.TemplateResponse(
            "choose_role.html",
            {"request": request, "csrf_token": csrf, "current_user": current_user},
        )

    # If the user is signed in and already has a role, send them to Featured
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
        # batch owner's total presentation counts to display under their name
        owner_presentation_counts = {}
        if owner_ids:
            rows_oc = session.exec(
                select(Presentation.owner_id, func.count(Presentation.id)).where(Presentation.owner_id.in_(list(owner_ids))).group_by(Presentation.owner_id)
            ).all()
            for r in rows_oc:
                owner_presentation_counts[int(r[0])] = int(r[1])
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
                    owner_site_role=getattr(owner, "site_role", None) if owner else None,
                    owner_email=getattr(owner, "email", None) if owner else None,
                    views=getattr(p, "views", None),
                    downloads=getattr(p, "downloads", 0),
                    cover_url=getattr(p, "cover_url", None) if hasattr(p, "cover_url") else None,
                    created_at=getattr(p, "created_at", None),
                    followers_count=follower_counts.get(oid, 0) if oid else 0,
                    is_following=(oid in following_set) if oid else False,
                    owner_presentation_count=owner_presentation_counts.get(oid, 0) if oid else 0,
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
                        owner_site_role=getattr(owner, "site_role", None) if owner else None,
                        owner_email=getattr(owner, "email", None) if owner else None,
                        views=getattr(p, "views", None),
                        downloads=getattr(p, "downloads", 0),
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
            most_viewed.append(SimpleNamespace(id=p.id, title=p.title, owner_username=getattr(owner, 'username', None) if owner else None, owner_site_role=getattr(owner, 'site_role', None) if owner else None, views=p.views, downloads=getattr(p, 'downloads', 0), filename=p.filename, owner_presentation_count=owner_presentation_counts.get(p.owner_id, 0) if getattr(p, 'owner_id', None) else 0))

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
                owner_site_role=getattr(owner, 'site_role', None) if owner else None,
                owner_email=getattr(owner, 'email', None) if owner else None,
                views=getattr(p, 'views', 0),
                downloads=getattr(p, 'downloads', 0),
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
        names = get_available_category_names()
        return JSONResponse(names)
    except Exception:
        return JSONResponse([], status_code=200)


@app.get("/featured", response_class=HTMLResponse, name="featured")
def featured_page(request: Request):
    current_user = get_current_user_optional(request)
    try:
        request.state.categories = get_available_category_names()
    except Exception:
        if not getattr(request.state, 'categories', None):
            request.state.categories = []

    def _map_p(p: Presentation, owner: Optional[User], cat: Optional[Category]):
        return SimpleNamespace(
            id=p.id,
            title=p.title,
            description=getattr(p, "description", None),
            filename=p.filename,
            mimetype=p.mimetype,
            owner_id=p.owner_id,
            owner_username=getattr(owner, "username", None) if owner else None,
            owner_site_role=getattr(owner, "site_role", None) if owner else None,
            owner_email=getattr(owner, "email", None) if owner else None,
            views=getattr(p, "views", 0),
            downloads=getattr(p, "downloads", 0),
            cover_url=getattr(p, "cover_url", None) if hasattr(p, "cover_url") else None,
            category=SimpleNamespace(name=cat.name) if cat else None,
            created_at=getattr(p, "created_at", None),
        )

    with Session(engine) as session:
        visibility = (Presentation.privacy == "public")
        if current_user:
            visibility = or_(Presentation.privacy == "public", Presentation.owner_id == current_user.id)
        trending_rows = session.exec(
            select(Presentation)
            .where(visibility)
            .options(selectinload(Presentation.owner), selectinload(Presentation.category))
            .order_by(Presentation.views.desc())
            .limit(12)
        ).all()
        trending = [_map_p(p, getattr(p, "owner", None), getattr(p, "category", None)) for p in trending_rows]

        # Because you viewed X
        because_viewed = []
        because_title = None
        if current_user:
            last_view = session.exec(
                select(Activity)
                .where((Activity.user_id == current_user.id) & (Activity.verb == "view"))
                .order_by(Activity.created_at.desc())
            ).first()
            if last_view and last_view.target_id:
                ref = session.get(Presentation, last_view.target_id)
                if ref:
                    because_title = ref.title
                    cat_id = ref.category_id
                    if cat_id:
                        rows = session.exec(
                            select(Presentation)
                            .where((Presentation.category_id == cat_id) & (Presentation.id != ref.id) & visibility)
                            .options(selectinload(Presentation.owner), selectinload(Presentation.category))
                            .order_by(Presentation.views.desc())
                            .limit(10)
                        ).all()
                        because_viewed = [_map_p(p, getattr(p, "owner", None), getattr(p, "category", None)) for p in rows]
        if not because_viewed:
            rows = session.exec(
                select(Presentation)
                .where(visibility)
                .options(selectinload(Presentation.owner), selectinload(Presentation.category))
                .order_by(Presentation.created_at.desc())
                .limit(10)
            ).all()
            because_viewed = [_map_p(p, getattr(p, "owner", None), getattr(p, "category", None)) for p in rows]

        # Popular in Category
        popular_in_category = []
        cat_rows = session.exec(
            select(Category, func.count(Presentation.id))
            .join(Presentation, Presentation.category_id == Category.id)
            .where(visibility)
            .group_by(Category.id)
            .order_by(desc(func.count(Presentation.id)))
            .limit(3)
        ).all()
        for cat, _count in cat_rows:
            rows = session.exec(
                select(Presentation)
                .where((Presentation.category_id == cat.id) & visibility)
                .options(selectinload(Presentation.owner), selectinload(Presentation.category))
                .order_by(Presentation.views.desc())
                .limit(8)
            ).all()
            popular_in_category.append({
                "category": cat,
                "items": [_map_p(p, getattr(p, "owner", None), getattr(p, "category", None)) for p in rows],
            })

        # From followed creators
        from_followed = []
        if current_user:
            following_ids = session.exec(
                select(Follow.following_id).where(Follow.follower_id == current_user.id)
            ).all()
            fids = [r[0] if isinstance(r, (list, tuple)) else r for r in following_ids]
            if fids:
                rows = session.exec(
                    select(Presentation)
                    .where(Presentation.owner_id.in_(list(fids)) & visibility)
                    .options(selectinload(Presentation.owner), selectinload(Presentation.category))
                    .order_by(Presentation.created_at.desc())
                    .limit(12)
                ).all()
                from_followed = [_map_p(p, getattr(p, "owner", None), getattr(p, "category", None)) for p in rows]

        # Attach bookmark + like counts across all sections
        all_ids = {p.id for p in trending + because_viewed + from_followed}
        for block in popular_in_category:
            for item in block["items"]:
                all_ids.add(item.id)
        if all_ids:
            rows = session.exec(
                select(Bookmark.presentation_id, func.count(Bookmark.id))
                .where(Bookmark.presentation_id.in_(list(all_ids)))
                .group_by(Bookmark.presentation_id)
            ).all()
            bc = {int(r[0]): int(r[1]) for r in rows}
            rows_likes = session.exec(
                select(Like.presentation_id, func.count(Like.id))
                .where(Like.presentation_id.in_(list(all_ids)))
                .group_by(Like.presentation_id)
            ).all()
            lc = {int(r[0]): int(r[1]) for r in rows_likes}
        else:
            bc = {}
            lc = {}
        for item in list(trending) + list(because_viewed) + list(from_followed):
            setattr(item, "bookmarks_count", bc.get(item.id, 0))
            setattr(item, "likes_count", lc.get(item.id, 0))
        for block in popular_in_category:
            for item in block["items"]:
                setattr(item, "bookmarks_count", bc.get(item.id, 0))
                setattr(item, "likes_count", lc.get(item.id, 0))

        # Attach owner presentation counts for creator badges on cards
        owner_ids = {p.owner_id for p in trending + because_viewed + from_followed if getattr(p, "owner_id", None)}
        for block in popular_in_category:
            for item in block["items"]:
                if getattr(item, "owner_id", None):
                    owner_ids.add(item.owner_id)
        if owner_ids:
            rows = session.exec(
                select(Presentation.owner_id, func.count(Presentation.id))
                .where(Presentation.owner_id.in_(list(owner_ids)))
                .group_by(Presentation.owner_id)
            ).all()
            owner_counts = {int(r[0]): int(r[1]) for r in rows}
        else:
            owner_counts = {}
        for item in list(trending) + list(because_viewed) + list(from_followed):
            setattr(item, "owner_presentation_count", owner_counts.get(getattr(item, "owner_id", None), 0))
        for block in popular_in_category:
            for item in block["items"]:
                setattr(item, "owner_presentation_count", owner_counts.get(getattr(item, "owner_id", None), 0))

    return templates.TemplateResponse(
        "feed.html",
        {
            "request": request,
            "current_user": current_user,
            "trending": trending,
            "because_viewed": because_viewed,
            "because_title": because_title,
            "popular_in_category": popular_in_category,
            "from_followed": from_followed,
        },
    )
    
@app.get("/set-language")
def set_language(request: Request, lang: str = "en"):
    """Set a simple UI language preference via cookie and redirect back.

    Supported values: en, fr, es, pt, de, ar, hi, zh, ja. Anything else falls back to en.
    """
    lang = (lang or "en").lower()
    if lang not in {"en", "fr", "es", "pt", "de", "ar", "hi", "zh", "ja"}:
        lang = "en"
    referer = request.headers.get("referer") or "/"
    resp = RedirectResponse(url=referer, status_code=status.HTTP_302_FOUND)
    resp.set_cookie("ui_lang", lang, max_age=60 * 60 * 24 * 365, httponly=False)
    return resp


@app.get("/register", response_class=HTMLResponse, name="register")
def register_get(request: Request):
    invite_token = request.query_params.get('invite_token')
    return templates.TemplateResponse("register.html", {"request": request, 'invite_token': invite_token})


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

    # preserve invite_token across redirect to role chooser when coming from invite flow
    invite_token = request.query_params.get('invite_token')
    if invite_token:
        response = RedirectResponse(url=f"/choose-role?invite_token={invite_token}", status_code=status.HTTP_302_FOUND)
    else:
        response = RedirectResponse(url="/choose-role", status_code=status.HTTP_302_FOUND)

    # After registration, set auth cookies and redirect user to the role chooser
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
                # only allow messaging if current_user follows the recipient
                follows = session.exec(select(Follow).where((Follow.follower_id == current_user.id) & (Follow.following_id == recipient.id))).first()
                if follows:
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
                        import asyncio

                        asyncio.create_task(manager.send_personal(recipient.id, out))
                    except Exception:
                        pass
                else:
                    # do not persist message if not following; fall through
                    pass
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

    # Always email site owner with the contact message so nothing is missed.
    try:
        sender_label = f"{name} <{email}>" if email else name
        subject = f"New contact message from {sender_label}"
        meta_lines = []
        if current_user and getattr(current_user, 'id', None):
            meta_lines.append(f"Signed-in user id: {current_user.id}, username: {current_user.username}")
        if owner_id or owner_username:
            meta_lines.append(f"Owner context - id: {owner_id}, username: {owner_username}")
        meta_block = ("\n\n" + "\n".join(meta_lines)) if meta_lines else ""
        body = f"From: {sender_label}\nEmail: {email}\n\nMessage:\n{message}{meta_block}"
        # Hard-code destination so all contact form submissions land in one inbox.
        send_email("shammahbadman@gmail.com", subject, body)
    except Exception:
        # Email failures should not break the contact form UX.
        pass

    # If this was an AJAX/JSON request, return JSON (clean for client JS). Otherwise render template
    accept = request.headers.get('accept', '')
    xreq = request.headers.get('x-requested-with', '')
    wants_json = ('application/json' in accept) or (xreq == 'XMLHttpRequest')
    if wants_json:
        return JSONResponse({'success': True})

    return templates.TemplateResponse('contact.html', {'request': request, 'success': 'Thanks — we received your message.'})


@app.websocket('/ws/chat/{user_id}')
async def websocket_chat(websocket: WebSocket, user_id: int):
    """Simple WebSocket chat: client must send a join message first:
    {"type": "join", "user_id": <id>} and then messages of form:
    {"type": "message", "from": <sender>, "to": <recipient>, "content": "..."}
    """
    await websocket.accept()
    try:
        from .database import engine
        from sqlmodel import Session, select
        from .models import Message as MessageModel, User as UserModel

        # try to identify the connected user from the access_token cookie; fall back to path param
        connected_user_id = user_id
        try:
            cookie_val = websocket.cookies.get('access_token')
            if cookie_val:
                raw = cookie_val.split(' ', 1)[-1] if ' ' in cookie_val else cookie_val
                from .auth import SECRET_KEY, ALGORITHM
                from jose import jwt
                payload = jwt.decode(raw, SECRET_KEY, algorithms=[ALGORITHM])
                uname = payload.get('sub')
                if uname:
                    with Session(engine) as s:
                        u = s.exec(select(UserModel).where(UserModel.username == uname)).first()
                        if u:
                            connected_user_id = u.id
        except Exception:
            connected_user_id = connected_user_id

        # register this connection under the resolved connected_user_id
        try:
            await manager.connect(connected_user_id, websocket)
        except Exception:
            pass

        while True:
            msg = await websocket.receive_json()
            # support both {type:'message', from:, to:, content:...} and {action:'message', to:, content:...}
            if msg.get('type') == 'message' or msg.get('action') == 'message':
                sender_id = int(msg.get('from') or msg.get('sender') or connected_user_id)
                recipient_id = int(msg.get('to'))
                content = msg.get('content')
                with Session(engine) as session:
                    m = MessageModel(sender_id=sender_id, recipient_id=recipient_id, content=content)
                    session.add(m)
                    session.commit()
                    session.refresh(m)
                payload = {
                    'type': 'message',
                    'message': {
                        'id': m.id,
                        'from': sender_id,
                        'to': recipient_id,
                        'content': content,
                        'created_at': m.created_at.isoformat(),
                    },
                }
                # notify recipient and sender
                await manager.send_personal(recipient_id, payload)
                await manager.send_personal(sender_id, payload)
    except Exception:
        pass
    finally:
        try:
            await manager.disconnect(user_id, websocket)
        except Exception:
            pass


@app.websocket('/ws/classrooms/{classroom_id}')
async def classroom_websocket(websocket: WebSocket, classroom_id: int):
    """Group chat WebSocket for a specific classroom.

    All connected classroom members receive broadcast messages.
    Client sends payloads like:
    {"type": "message", "content": "Hello"}
    """
    await websocket.accept()
    from sqlmodel import select as _select

    # resolve current user from access_token cookie
    current_user: Optional[User] = None
    try:
        cookie_val = websocket.cookies.get('access_token')
        if cookie_val:
            raw = cookie_val.split(' ', 1)[-1] if ' ' in cookie_val else cookie_val
            payload = jwt.decode(raw, SECRET_KEY, algorithms=[ALGORITHM])
            uname = payload.get('sub')
            if uname:
                with Session(engine) as session:
                    u = session.exec(_select(User).where(User.username == uname)).first()
                    current_user = u
    except Exception:
        current_user = None

    if not current_user:
        await websocket.close(code=1008)
        return

    # verify classroom membership
    with Session(engine) as session:
        mem = session.exec(
            _select(Membership).where(
                (Membership.user_id == current_user.id)
                & (Membership.classroom_id == classroom_id)
            )
        ).first()
        if not mem:
            await websocket.close(code=1008)
            return

    # simple in-memory set of connections per classroom
    if not hasattr(classroom_websocket, '_room_conns'):
        classroom_websocket._room_conns = {}
    room_conns: Dict[int, Set[WebSocket]] = classroom_websocket._room_conns
    room_set = room_conns.setdefault(int(classroom_id), set())
    room_set.add(websocket)

    try:
        while True:
            data = await websocket.receive_json()
            dtype = data.get('type')
            if dtype == 'typing':
                payload = {
                    'type': 'typing',
                    'user_id': current_user.id,
                    'username': current_user.username,
                    'status': data.get('status') or 'start',
                }
                # broadcast typing to all connected clients in this classroom
                dead: list[WebSocket] = []
                for ws in list(room_conns.get(int(classroom_id), set())):
                    try:
                        await ws.send_json(payload)
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    try:
                        room_conns.get(int(classroom_id), set()).discard(ws)
                    except Exception:
                        pass
                continue
            if dtype == 'seen':
                payload = {
                    'type': 'seen',
                    'user_id': current_user.id,
                    'username': current_user.username,
                    'message_id': data.get('message_id'),
                }
                dead: list[WebSocket] = []
                for ws in list(room_conns.get(int(classroom_id), set())):
                    try:
                        await ws.send_json(payload)
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    try:
                        room_conns.get(int(classroom_id), set()).discard(ws)
                    except Exception:
                        pass
                continue
            if dtype != 'message':
                continue
            content = (data.get('content') or '').strip()
            if not content:
                continue
            with Session(engine) as session:
                msg = ClassroomMessage(
                    classroom_id=classroom_id,
                    sender_id=current_user.id,
                    content=content,
                )
                session.add(msg)
                session.commit()
                session.refresh(msg)
            payload = {
                'type': 'message',
                'message': {
                    'id': msg.id,
                    'classroom_id': classroom_id,
                    'sender_id': current_user.id,
                    'sender_name': current_user.username,
                    'content': msg.content,
                    'created_at': msg.created_at.isoformat(),
                },
            }
            # broadcast to all connected clients in this classroom
            dead: list[WebSocket] = []
            for ws in list(room_conns.get(int(classroom_id), set())):
                try:
                    await ws.send_json(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                try:
                    room_conns.get(int(classroom_id), set()).discard(ws)
                except Exception:
                    pass
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        try:
            room_conns.get(int(classroom_id), set()).discard(websocket)
        except Exception:
            pass


@app.websocket('/ws/spaces/{space_id}')
async def space_websocket(websocket: WebSocket, space_id: int):
    """Group chat WebSocket for a specific space.

    All connected space members receive broadcast messages.
    Client sends payloads like:
    {"type": "message", "content": "Hello"}
    """
    await websocket.accept()
    from sqlmodel import select as _select

    # resolve current user from access_token cookie
    current_user: Optional[User] = None
    try:
        cookie_val = websocket.cookies.get('access_token')
        if cookie_val:
            raw = cookie_val.split(' ', 1)[-1] if ' ' in cookie_val else cookie_val
            payload = jwt.decode(raw, SECRET_KEY, algorithms=[ALGORITHM])
            uname = payload.get('sub')
            if uname:
                with Session(engine) as session:
                    u = session.exec(_select(User).where(User.username == uname)).first()
                    current_user = u
    except Exception:
        current_user = None

    if not current_user:
        await websocket.close(code=1008)
        return

    # verify space membership (compat: accept classroom_id during transition)
    with Session(engine) as session:
        mem = session.exec(
            _select(Membership).where(
                (Membership.user_id == current_user.id)
                & (
                    (Membership.space_id == space_id)
                    | (Membership.classroom_id == space_id)
                )
            )
        ).first()
        if not mem:
            await websocket.close(code=1008)
            return

    # simple in-memory set of connections per space
    if not hasattr(space_websocket, '_room_conns'):
        space_websocket._room_conns = {}
    room_conns: Dict[int, Set[WebSocket]] = space_websocket._room_conns
    room_set = room_conns.setdefault(int(space_id), set())
    room_set.add(websocket)

    try:
        while True:
            data = await websocket.receive_json()
            dtype = data.get('type')
            if dtype == 'typing':
                payload = {
                    'type': 'typing',
                    'user_id': current_user.id,
                    'username': current_user.username,
                    'status': data.get('status') or 'start',
                }
                dead: list[WebSocket] = []
                for ws in list(room_conns.get(int(space_id), set())):
                    try:
                        await ws.send_json(payload)
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    try:
                        room_conns.get(int(space_id), set()).discard(ws)
                    except Exception:
                        pass
                continue
            if dtype == 'seen':
                payload = {
                    'type': 'seen',
                    'user_id': current_user.id,
                    'username': current_user.username,
                    'message_id': data.get('message_id'),
                }
                dead: list[WebSocket] = []
                for ws in list(room_conns.get(int(space_id), set())):
                    try:
                        await ws.send_json(payload)
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    try:
                        room_conns.get(int(space_id), set()).discard(ws)
                    except Exception:
                        pass
                continue
            if dtype != 'message':
                continue
            content = (data.get('content') or '').strip()
            if not content:
                continue
            with Session(engine) as session:
                msg = SpaceMessage(
                    space_id=space_id,
                    sender_id=current_user.id,
                    content=content,
                )
                session.add(msg)
                session.commit()
                session.refresh(msg)
            payload = {
                'type': 'message',
                'message': {
                    'id': msg.id,
                    'space_id': space_id,
                    'sender_id': current_user.id,
                    'sender_name': current_user.username,
                    'content': msg.content,
                    'created_at': msg.created_at.isoformat(),
                },
            }
            dead: list[WebSocket] = []
            for ws in list(room_conns.get(int(space_id), set())):
                try:
                    await ws.send_json(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                try:
                    room_conns.get(int(space_id), set()).discard(ws)
                except Exception:
                    pass
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        try:
            room_conns.get(int(space_id), set()).discard(websocket)
        except Exception:
            pass


def _video_parse_space_id(room_id: Any) -> Optional[int]:
    try:
        if isinstance(room_id, str) and room_id.startswith('space:'):
            return int(room_id.split(':', 1)[1])
        return int(room_id)
    except Exception:
        return None


def _video_get_ice_servers() -> list[dict]:
    raw = os.getenv('VIDEO_ICE_SERVERS')
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
    stun = os.getenv('VIDEO_STUN_SERVER', 'stun:stun.l.google.com:19302')
    servers = [{"urls": [stun]}]
    turn = os.getenv('VIDEO_TURN_SERVER')
    if turn:
        entry: dict[str, Any] = {"urls": [turn]}
        username = os.getenv('VIDEO_TURN_USERNAME')
        credential = os.getenv('VIDEO_TURN_CREDENTIAL')
        if username and credential:
            entry["username"] = username
            entry["credential"] = credential
        servers.append(entry)
    return servers


def _video_get_ws_user(websocket: WebSocket) -> Optional[User]:
    try:
        cookie_val = websocket.cookies.get('access_token')
        if not cookie_val:
            return None
        raw = cookie_val.split(' ', 1)[-1] if ' ' in cookie_val else cookie_val
        payload = jwt.decode(raw, SECRET_KEY, algorithms=[ALGORITHM])
        uname = payload.get('sub')
        if not uname:
            return None
        with Session(engine) as session:
            return session.exec(select(User).where(User.username == uname)).first()
    except Exception:
        return None


async def _video_send_to_user(user_id: int, payload: dict) -> None:
    conns = list(video_state.user_sockets.get(int(user_id), set()))
    for ws in conns:
        try:
            await ws.send_json(payload)
        except Exception:
            try:
                video_state.unregister_socket(ws)
            except Exception:
                pass


async def _video_broadcast_room(space_id: int, payload: dict, exclude_user_id: Optional[int] = None) -> None:
    user_ids = list(video_state.room_users.get(int(space_id), set()))
    for uid in user_ids:
        if exclude_user_id is not None and int(uid) == int(exclude_user_id):
            continue
        await _video_send_to_user(uid, payload)


@app.get('/api/video/config')
def video_config(current_user: User = Depends(get_current_user)):
    return {"iceServers": _video_get_ice_servers()}


@app.get('/api/spaces/{space_id}/meeting')
def space_meeting_status(space_id: int, current_user: User = Depends(get_current_user)):
    return {
        "space_id": int(space_id),
        "active": video_state.is_meeting_active(space_id),
    }


@app.websocket('/ws/video')
async def video_signaling(websocket: WebSocket):
    await websocket.accept()
    current_user = _video_get_ws_user(websocket)
    if not current_user:
        await websocket.close(code=1008)
        return
    video_state.register_socket(current_user.id, websocket)

    try:
        while True:
            data = await websocket.receive_json()
            event = data.get('event') or data.get('type')
            payload = data.get('payload') or {}

            if event == 'join-room':
                room_id = payload.get('room_id')
                space_id = _video_parse_space_id(room_id)
                if not space_id:
                    await websocket.send_json({"event": "error", "payload": {"message": "Invalid room"}})
                    continue

                with Session(engine) as session:
                    mem = session.exec(
                        select(Membership).where(
                            (Membership.user_id == current_user.id)
                            & ((Membership.space_id == space_id) | (Membership.classroom_id == space_id))
                        )
                    ).first()
                if not mem:
                    await websocket.send_json({"event": "error", "payload": {"message": "Not a member of this space"}})
                    continue

                if not video_state.is_meeting_active(space_id):
                    if mem.role not in ['teacher', 'admin']:
                        await websocket.send_json({"event": "meeting-inactive", "payload": {"space_id": space_id}})
                        continue
                    video_state.start_meeting(space_id, current_user.id)

                video_state.join_room(current_user.id, space_id)
                if video_state.meetings.get(space_id):
                    video_state.meetings[space_id]['participants'].add(current_user.id)

                existing_users = [uid for uid in video_state.room_users.get(space_id, set()) if uid != current_user.id]
                await websocket.send_json({
                    "event": "room-users",
                    "payload": {"space_id": space_id, "users": existing_users},
                })
                await _video_broadcast_room(space_id, {
                    "event": "user-joined",
                    "payload": {"space_id": space_id, "user_id": current_user.id, "username": current_user.username},
                }, exclude_user_id=current_user.id)
                continue

            if event == 'leave-room':
                room_id = payload.get('room_id')
                space_id = _video_parse_space_id(room_id)
                if not space_id:
                    continue
                video_state.leave_room(current_user.id, space_id)
                await _video_broadcast_room(space_id, {
                    "event": "user-left",
                    "payload": {"space_id": space_id, "user_id": current_user.id},
                })
                meeting = video_state.meetings.get(space_id)
                if meeting and meeting.get('host_id') == current_user.id:
                    video_state.end_meeting(space_id)
                    await _video_broadcast_room(space_id, {
                        "event": "meeting-ended",
                        "payload": {"space_id": space_id},
                    })
                continue

            if event in ['offer', 'answer', 'ice-candidate']:
                target_id = payload.get('target_id')
                if not target_id:
                    continue
                forward_payload = dict(payload)
                forward_payload.update({
                    "sender_id": current_user.id,
                    "sender_username": current_user.username,
                })
                forward = {"event": event, "payload": forward_payload}
                await _video_send_to_user(int(target_id), forward)
                continue

            if event == 'call-user':
                target_id = payload.get('target_id')
                if not target_id:
                    continue
                await _video_send_to_user(int(target_id), {
                    "event": "incoming-call",
                    "payload": {
                        "call_id": payload.get('call_id'),
                        "from_id": current_user.id,
                        "from_username": current_user.username,
                        "media": payload.get('media', 'video'),
                    },
                })
                continue

            if event in ['accept-call', 'reject-call', 'end-call']:
                target_id = payload.get('target_id')
                if not target_id:
                    continue
                forward_payload = dict(payload)
                forward_payload.update({
                    "sender_id": current_user.id,
                    "sender_username": current_user.username,
                })
                await _video_send_to_user(int(target_id), {
                    "event": event,
                    "payload": forward_payload,
                })
                continue

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        user_id = video_state.unregister_socket(websocket)
        if user_id is not None:
            for space_id in list(video_state.user_rooms.get(int(user_id), set())):
                video_state.leave_room(int(user_id), int(space_id))
                await _video_broadcast_room(int(space_id), {
                    "event": "user-left",
                    "payload": {"space_id": int(space_id), "user_id": int(user_id)},
                })
                meeting = video_state.meetings.get(int(space_id))
                if meeting and meeting.get('host_id') == int(user_id):
                    video_state.end_meeting(int(space_id))
                    await _video_broadcast_room(int(space_id), {
                        "event": "meeting-ended",
                        "payload": {"space_id": int(space_id)},
                    })


@app.post('/api/chat/send')
def api_chat_send(to: int = Body(...), content: str = Body('', embed=True), file: UploadFile | None = None, current: User = Depends(get_current_user)):
    """Send message via REST; saves to DB and notifies recipient."""
    from .database import engine
    from sqlmodel import Session
    from .models import Message as MessageModel
    import shutil
    from pathlib import Path

    file_url = None
    if file is not None:
        upload_dir = Path(UPLOAD_DIR) / 'chat' / str(current.id)
        upload_dir.mkdir(parents=True, exist_ok=True)
        dest = upload_dir / file.filename
        with dest.open('wb') as f:
            shutil.copyfileobj(file.file, f)
        # store a web-accessible path (served at /uploads/...)
        file_url = f"/uploads/chat/{current.id}/{quote(file.filename)}"
        # generate thumbnail for images if Pillow is available
        thumbnail_url = None
        if Image is not None:
            try:
                img = Image.open(dest)
                img.thumbnail((300, 300))
                thumb_dir = upload_dir / 'thumbs'
                thumb_dir.mkdir(parents=True, exist_ok=True)
                thumb_name = f"thumb_{file.filename}"
                thumb_path = thumb_dir / thumb_name
                img.save(thumb_path)
                thumbnail_url = f"/uploads/chat/{current.id}/thumbs/{quote(thumb_name)}"
            except Exception:
                thumbnail_url = None
        else:
            thumbnail_url = None

    with Session(engine) as session:
        # Allow any authenticated user to send chat messages; tighten this
        # policy later if needed.
        m = MessageModel(sender_id=current.id, recipient_id=int(to), content=content, file_url=file_url, thumbnail_url=thumbnail_url)
        session.add(m)
        session.commit()
        session.refresh(m)

    import asyncio
    asyncio.create_task(manager.send_personal(int(to), {'type': 'message', 'message': {'id': m.id, 'from': current.id, 'to': int(to), 'content': content, 'file': file_url, 'created_at': m.created_at.isoformat()}}))

    return JSONResponse({'status': 'ok', 'message': {'id': m.id, 'from': current.id, 'to': int(to), 'content': content, 'file': file_url, 'created_at': m.created_at.isoformat()}})


@app.get('/api/messages/unread_counts')
def api_unread_counts_early(request: Request):
    current = getattr(request.state, 'current_user', None)
    if not current:
        return JSONResponse({})
    from .database import engine
    from sqlmodel import Session, select, func
    from .models import Message as MessageModel
    with Session(engine) as session:
        rows = session.exec(select(MessageModel.sender_id, func.count(MessageModel.id)).where((MessageModel.recipient_id == current.id) & (MessageModel.read == False)).group_by(MessageModel.sender_id)).all()
        res = {str(r[0]): int(r[1]) for r in rows}
    return JSONResponse(res)


@app.get('/api/notifications')
def api_get_notifications(request: Request, limit: int = Query(50)):
    current = getattr(request.state, 'current_user', None)
    if not current:
        raise HTTPException(status_code=401, detail='Authentication required')
    from .models import Notification as NotificationModel
    notif_filter = request.query_params.get('filter') or ''
    with Session(engine) as session:
        stmt = select(NotificationModel).where(NotificationModel.recipient_id == current.id)
        if notif_filter == 'unread':
            stmt = stmt.where(NotificationModel.read == False)  # noqa: E712
        elif notif_filter == 'messages':
            stmt = stmt.where(NotificationModel.verb == 'message')
        elif notif_filter == 'uploads':
            stmt = stmt.where(NotificationModel.verb == 'new_upload')
        elif notif_filter == 'classrooms':
            stmt = stmt.where(NotificationModel.verb == 'classroom_invite')

        stmt = stmt.order_by(NotificationModel.created_at.desc()).limit(limit)
        rows = session.exec(stmt).all()
        out = []
        for n in rows:
            actor_username = None
            actor_avatar = None
            actor_site_role = None
            target_title = None
            try:
                if n.actor_id:
                    a = session.get(User, n.actor_id)
                    if a:
                        actor_username = a.username
                        actor_avatar = a.avatar
                        actor_site_role = getattr(a, 'site_role', None)
                if n.target_type == 'presentation' and n.target_id:
                    p = session.get(Presentation, n.target_id)
                    if p:
                        target_title = p.title
            except Exception:
                pass
            out.append({
                'id': n.id,
                'actor_id': n.actor_id,
                'actor_username': actor_username,
                'actor_avatar': actor_avatar,
                'actor_site_role': actor_site_role,
                'verb': n.verb,
                'target_type': n.target_type,
                'target_id': n.target_id,
                'target_title': target_title,
                'read': bool(n.read),
                'created_at': n.created_at.isoformat(),
            })
    return JSONResponse(out)


@app.post('/api/notifications/{nid}/read')
def api_mark_notification_read(nid: int, request: Request):
    current = getattr(request.state, 'current_user', None)
    if not current:
        raise HTTPException(status_code=401, detail='Authentication required')
    from .models import Notification as NotificationModel
    with Session(engine) as session:
        n = session.get(NotificationModel, nid)
        if not n or n.recipient_id != current.id:
            raise HTTPException(status_code=404, detail='Not found')
        n.read = True
        session.add(n)
        session.commit()
    return JSONResponse({'ok': True})


@app.post('/api/notifications/clear')
def api_clear_notifications(request: Request):
    """
    Delete all notifications for the current user.
    Used by the "Clear" / "Clear feed" buttons to completely
    empty the notification center for the signed-in user.
    """
    current = getattr(request.state, 'current_user', None)
    if not current:
        raise HTTPException(status_code=401, detail='Authentication required')
    from .models import Notification as NotificationModel
    with Session(engine) as session:
        rows = session.exec(
            select(NotificationModel).where(NotificationModel.recipient_id == current.id)
        ).all()
        for n in rows:
            session.delete(n)
        session.commit()
    return JSONResponse({'ok': True})

@app.post('/notifications/classroom-invite/{nid}/accept')
def accept_classroom_invite(nid: int, current_user: User = Depends(get_current_user)):
    """Accept a classroom invite delivered via notifications.

    Creates a student membership for the current user in the target classroom.
    """
    from .models import Notification as NotificationModel

    with Session(engine) as session:
        n = session.get(NotificationModel, nid)
        if (not n or n.recipient_id != current_user.id or
                n.verb != 'classroom_invite' or n.target_type != 'classroom'):
            raise HTTPException(status_code=404, detail='Invitation not found')

        classroom = session.get(Classroom, n.target_id)
        if not classroom:
            raise HTTPException(status_code=404, detail='Classroom not found')

        # add membership if missing
        exists = session.exec(
            select(Membership).where(
                (Membership.user_id == current_user.id)
                & (Membership.classroom_id == classroom.id)
            )
        ).first()
        if not exists:
            m = Membership(user_id=current_user.id, classroom_id=classroom.id, role='student')
            session.add(m)
            try:
                cm = ClassroomMessage(classroom_id=classroom.id, sender_id=current_user.id, content=f"[system] {current_user.username} joined the classroom.")
                session.add(cm)
            except Exception:
                pass

        n.read = True
        session.add(n)
        session.commit()

    return RedirectResponse('/notifications', status_code=303)


@app.post('/notifications/classroom-invite/{nid}/decline')
def decline_classroom_invite(nid: int, current_user: User = Depends(get_current_user)):
    """Decline a classroom invite delivered via notifications."""
    from .models import Notification as NotificationModel

    with Session(engine) as session:
        n = session.get(NotificationModel, nid)
        if (not n or n.recipient_id != current_user.id or
                n.verb != 'classroom_invite' or n.target_type != 'classroom'):
            raise HTTPException(status_code=404, detail='Invitation not found')

        n.read = True
        session.add(n)
        session.commit()

    return RedirectResponse('/notifications', status_code=303)


@app.get("/notifications", response_class=HTMLResponse)
def notifications_page(request: Request, current_user: User = Depends(get_current_user)):
    """Full notifications page showing all notifications for the signed-in user.

    Supports an optional `filter` query parameter that mirrors the small
    notifications panel:
      - filter=unread -> only unread notifications
      - filter=messages -> direct message notifications
      - filter=uploads -> new upload notifications
      - filter=classrooms -> classroom invite notifications
    """
    active_filter = request.query_params.get("filter") if request else None
    with Session(engine) as session:
        from .models import Notification as NotificationModel

        query = select(NotificationModel).where(
            NotificationModel.recipient_id == current_user.id
        )
        if active_filter == "unread":
            query = query.where(NotificationModel.read == False)  # noqa: E712
        elif active_filter == "messages":
            query = query.where(NotificationModel.verb == "message")
        elif active_filter == "uploads":
            query = query.where(NotificationModel.verb == "new_upload")
        elif active_filter == "classrooms":
            query = query.where(NotificationModel.verb == "classroom_invite")

        rows = session.exec(
            query.order_by(NotificationModel.created_at.desc())
        ).all()

        notif_items = []
        for n in rows:
            actor_username = None
            actor_avatar = None
            actor_site_role = None
            target_title = None
            link = None
            try:
                if n.actor_id:
                    a = session.get(User, n.actor_id)
                    if a:
                        actor_username = a.username
                        actor_avatar = a.avatar
                        actor_site_role = getattr(a, 'site_role', None)
                if n.target_type == "presentation" and n.target_id:
                    p = session.get(Presentation, n.target_id)
                    if p:
                        target_title = p.title
                        link = f"/presentations/{n.target_id}"
                elif n.target_type == "user" and n.target_id:
                    u = session.get(User, n.target_id)
                    if u:
                        actor_username = actor_username or u.username
                        link = f"/users/{u.username}"
                elif n.target_type == "classroom" and n.target_id:
                    c = session.get(Classroom, n.target_id)
                    if c:
                        target_title = c.name
                        # send teachers to the performance view for that classroom
                        link = f"/classrooms/{n.target_id}/performance"
            except Exception:
                pass

            # Build human-readable message suffix; actor is rendered separately so their
            # name can be clickable in the template.
            actor = actor_username or (f"User {n.actor_id}" if n.actor_id else "Someone")
            if n.verb == "follow":
                message = "followed you"
            elif n.verb == "like":
                if target_title:
                    message = f"liked \"{target_title}\""
                else:
                    message = "liked your presentation"
            elif n.verb == "save":
                if target_title:
                    message = f"saved \"{target_title}\""
                else:
                    message = "saved your presentation"
            elif n.verb == "message":
                message = "sent you a message"
                if n.target_id:
                    link = f"/messages/{n.actor_id or ''}".rstrip("/")
            elif n.verb == "new_upload":
                if target_title:
                    message = f"uploaded a new presentation \"{target_title}\""
                else:
                    message = "uploaded a new presentation"
            elif n.verb == "classroom_invite":
                if target_title:
                    message = f"invited you to join \"{target_title}\""
                else:
                    message = "invited you to join a classroom"
            else:
                message = n.verb

            accept_url = None
            decline_url = None
            if (
                n.verb == "classroom_invite"
                and n.target_type == "classroom"
                and not n.read
            ):
                accept_url = f"/notifications/classroom-invite/{n.id}/accept"
                decline_url = f"/notifications/classroom-invite/{n.id}/decline"

            notif_items.append(
                SimpleNamespace(
                    id=n.id,
                    message=message,
                    actor_username=actor_username,
                    actor_avatar=actor_avatar,
                    actor_site_role=actor_site_role,
                    link=link,
                    created_at=n.created_at,
                    read=n.read,
                    accept_url=accept_url,
                    decline_url=decline_url,
                )
            )

        return templates.TemplateResponse(
            "notifications.html",
            {
                "request": request,
                "notifications": notif_items,
                "current_user": current_user,
                "active_filter": active_filter,
            },
        )


@app.post('/api/messages/{other_id}')
async def api_post_message(other_id: int, request: Request, file: UploadFile | None = None, bypass: bool = Query(False), current: User = Depends(get_current_user)):
    from .database import engine
    from sqlmodel import Session
    from .models import Message as MessageModel
    import shutil
    from pathlib import Path

    # Determine message content from JSON (application/json) or form data.
    content = ""
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("application/json"):
        try:
            data = await request.json()
            if isinstance(data, dict):
                content = str(data.get("content") or "")
            elif isinstance(data, str):
                content = data
        except Exception:
            content = ""
    else:
        try:
            form = await request.form()
            val = form.get("content")
            if val is not None:
                content = str(val)
        except Exception:
            content = ""

    file_url = None
    thumbnail_url = None
    if file is not None:
        upload_dir = Path(UPLOAD_DIR) / 'chat' / str(current.id)
        upload_dir.mkdir(parents=True, exist_ok=True)
        dest = upload_dir / file.filename
        with dest.open('wb') as f:
            shutil.copyfileobj(file.file, f)
        # store a web-accessible path (served at /uploads/...)
        file_url = f"/uploads/chat/{current.id}/{quote(file.filename)}"
        # generate thumbnail for images when Pillow is available
        if Image is not None:
            try:
                img = Image.open(dest)
                img.thumbnail((300, 300))
                thumb_dir = upload_dir / 'thumbs'
                thumb_dir.mkdir(parents=True, exist_ok=True)
                thumb_name = f"thumb_{file.filename}"
                thumb_path = thumb_dir / thumb_name
                img.save(thumb_path)
                thumbnail_url = f"/uploads/chat/{current.id}/thumbs/{quote(thumb_name)}"
            except Exception:
                thumbnail_url = None

    with Session(engine) as session:
        # Optionally enforce that the sender follows the recipient; for now
        # we allow all authenticated users to message each other unless a
        # stricter policy is added back.
        m = MessageModel(
            sender_id=current.id,
            recipient_id=other_id,
            content=content,
            file_url=file_url,
            thumbnail_url=thumbnail_url,
        )
        session.add(m)
        session.commit()
        session.refresh(m)

        # Snapshot the data we need before the session is closed so we don't
        # access attributes on a detached instance later.
        message_dict = {
            'id': m.id,
            'from': current.id,
            'to': other_id,
            'content': content,
            'file': file_url,
            'thumbnail': thumbnail_url,
            'created_at': m.created_at.isoformat(),
            # sender metadata for richer chat UI (avatar, badges, names)
            'username': getattr(current, 'username', None),
            'full_name': getattr(current, 'full_name', None),
            'avatar': getattr(current, 'avatar', None),
            'site_role': getattr(current, 'site_role', None),
        }

        # no Notification row for direct messages; unread state is tracked
        # via Message.read and exposed separately to the UI.

    # push a real-time update to the recipient if they are online, using the
    # same top-level shape as WebSocket chat messages so chat.js can render it.
    msg_payload = {
        "type": "message",
        **message_dict,
    }
    try:
        import asyncio

        asyncio.create_task(manager.send_personal(other_id, msg_payload))
    except Exception:
        # If we're in a thread without an event loop, just skip the
        # websocket notification; the message is still stored and will
        # appear when the recipient opens the conversation.
        pass

    return JSONResponse({
        'status': 'ok',
        'message': message_dict,
    })



@app.get('/api/contacts/following')
def api_contacts_following(current_user: User = Depends(get_current_user)):
    """Return users that the current user is following."""
    with Session(engine) as session:
        rows = session.exec(select(User).join(Follow, Follow.following_id == User.id).where(Follow.follower_id == current_user.id)).all()
        out = [{'id': u.id, 'username': u.username} for u in rows]
    return JSONResponse(out)


@app.get('/api/contacts/mutuals')
def api_contacts_mutuals(current_user: User = Depends(get_current_user)):
    """Return people the current user follows (used as "Mutuals" in UI)."""
    with Session(engine) as session:
        rows = session.exec(
            select(User)
            .join(Follow, Follow.following_id == User.id)
            .where(Follow.follower_id == current_user.id)
        ).all()
        out = [{'id': u.id, 'username': u.username} for u in rows]
    return JSONResponse(out)


@app.get('/api/me')
def api_me(request: Request):
    cur = getattr(request.state, 'current_user', None)
    if not cur:
        return JSONResponse({}, status_code=401)
    return JSONResponse({'id': cur.id, 'username': cur.username})


@app.get('/api/resolve-username')
def api_resolve_username(username: str = Query(None)):
    if not username:
        return JSONResponse({}, status_code=400)
    with Session(engine) as session:
        u = session.exec(select(User).where(User.username == username)).first()
        if not u:
            return JSONResponse({}, status_code=404)
        return JSONResponse({'id': u.id, 'username': u.username})


@app.get('/api/online/{user_id}')
def api_online(user_id: int):
    return JSONResponse({'online': manager.is_online(user_id)})


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
    # If client requests cookie-based auth (e.g., set_cookie=true), set HttpOnly cookies
    if payload.get('set_cookie'):
        response = JSONResponse({'access_token': token, 'token_type': 'bearer'})
        cookie_secure = os.getenv("COOKIE_SECURE", "false").lower() == "true"
        cookie_samesite = os.getenv("COOKIE_SAMESITE", "lax")
        refresh = create_refresh_token({'sub': user.username})
        response.set_cookie(key='access_token', value=f'Bearer {token}', httponly=True, secure=cookie_secure, samesite=cookie_samesite)
        response.set_cookie(key='refresh_token', value=f'Bearer {refresh}', httponly=True, secure=cookie_secure, samesite=cookie_samesite)
        return response
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

    # Block upload UI entirely for passersby (role chosen as "just browsing")
    try:
        cookie_role = request.cookies.get("user_role") if hasattr(request, "cookies") else None
        effective_role = cookie_role or getattr(current_user, "site_role", None)
        if effective_role == "passerby":
            return templates.TemplateResponse(
                "upload_blocked.html",
                {"request": request, "current_user": current_user},
            )
    except Exception:
        pass

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
                    owner_site_role=getattr(owner, 'site_role', None) if owner else None,
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
    privacy: str = Form("public"),
    license: str = Form("all_rights_reserved"),
    allow_download: Optional[str] = Form(None),
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

    # server-side: disallow users who chose 'passerby' from uploading
    try:
        # current_user is provided via dependency
        if getattr(current_user, 'site_role', None) == 'passerby' or request.cookies.get('user_role') == 'passerby':
            return render_error("Passerby users cannot upload. Please choose a different role or sign in with a full account.")
    except Exception:
        pass

    if not file or not getattr(file, "filename", None):
        return render_error("Please choose a file to upload.")

    title_clean = (title or "").strip()
    if not title_clean:
        title_clean = Path(file.filename).stem

    # Ensure description is a string
    desc_clean = (description or "").strip()
    ai_title = None
    ai_description = None

    try:
        # Allowed extensions for presentations (include common video types)
        allowed_exts = {".pdf", ".ppt", ".pptx", ".pptm", ".mp4", ".mov", ".m4v", ".webm"}
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in allowed_exts:
            return render_error("Unsupported file type")

        unique_name = f"{uuid.uuid4().hex}{file_ext}"
        save_path = Path(UPLOAD_DIR) / unique_name
        # Stream upload with size limit (larger default to support high-quality videos)
        max_mb = int(os.getenv("UPLOAD_MAX_MB", str(int(MAX_UPLOAD_BYTES / (1024*1024)))))
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

        # AI auto title/description (best-effort) when missing or short
        try:
            needs_title = len(title_clean.strip()) < 6
            needs_desc = len(desc_clean.strip()) < 12
            if needs_title or needs_desc:
                sample_text = ""
                if file_ext == ".pdf" and fitz is not None:
                    try:
                        doc = fitz.open(str(save_path))
                        sample_text = "\n".join([doc[i].get_text() for i in range(min(len(doc), 3))])
                        doc.close()
                    except Exception:
                        sample_text = ""
                prompt = (
                    "Generate a clean title and a 1-2 sentence description for this presentation. "
                    "Return JSON with keys 'title' and 'description' only.\n\n"
                    f"Original title: {title_clean}\n"
                    f"Existing description: {desc_clean}\n"
                    f"Extracted text: {sample_text[:2000]}"
                )
                ai_raw = chat_completion(
                    [{"role": "user", "content": prompt}],
                    model=os.getenv("OPENAI_MODEL", "gpt-4o-mini") if get_ai_provider() == "openai" else os.getenv("OLLAMA_MODEL", "qwen2.5:3b"),
                    max_tokens=220,
                    temperature=0.4,
                )
                try:
                    parsed = json.loads(ai_raw.strip())
                    ai_title = (parsed.get("title") or "").strip() or None
                    ai_description = (parsed.get("description") or "").strip() or None
                except Exception:
                    # fallback: split first line as title, rest as description
                    parts = [p.strip() for p in ai_raw.split("\n") if p.strip()]
                    if parts:
                        ai_title = parts[0][:120]
                        if len(parts) > 1:
                            ai_description = " ".join(parts[1:])[:400]
        except Exception:
            pass

        # normalise advanced settings
        privacy_val = privacy if privacy in {"public", "private"} else "public"
        allow_download_val = bool(allow_download)

        p = Presentation(
            title=title_clean,
            description=desc_clean,
            filename=unique_name,
            mimetype=file.content_type or "application/octet-stream",
            owner_id=current_user.id,
            privacy=privacy_val,
            allow_download=allow_download_val,
            ai_title=ai_title,
            ai_description=ai_description,
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

            # conversion/preview/transcode generation: always try to enqueue (with synchronous fallback)
            if file_ext in {".ppt", ".pptx", ".pptm", ".mp4", ".mov", ".m4v", ".webm"}:
                try:
                    from .tasks import enqueue_conversion

                    enqueue_conversion(p.id, unique_name)
                except Exception:
                    # fall back to running conversion inline if queue/Redis is unavailable
                    try:
                        from .tasks import convert_presentation

                        convert_presentation(p.id, unique_name)
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

            # notify followers that a new presentation was uploaded
            try:
                followers = session.exec(
                    select(Follow.follower_id).where(Follow.following_id == current_user.id)
                ).all()
                for row in followers:
                    fid = row[0] if isinstance(row, (list, tuple)) else row
                    if not fid:
                        continue
                    n = Notification(
                        recipient_id=int(fid),
                        actor_id=current_user.id,
                        verb='new_upload',
                        target_type='presentation',
                        target_id=p.id,
                    )
                    session.add(n)
                session.commit()
            except Exception:
                session.rollback()

        return RedirectResponse(
            url=f"/presentations/{presentation_id}?just_uploaded=1",
            status_code=status.HTTP_302_FOUND,
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
    # disallow anonymous or 'passerby' users from using this API
    if not current_user:
        return JSONResponse({"error": "Authentication required to upload"}, status_code=403)
    # prefer persisted site_role when available
    try:
        if getattr(current_user, 'site_role', None) == 'passerby' or request.cookies.get('user_role') == 'passerby':
            return JSONResponse({"error": "Passerby users cannot upload"}, status_code=403)
    except Exception:
        pass

    allowed_exts = {".pdf", ".ppt", ".pptx", ".pptm", ".mp4", ".mov", ".m4v", ".webm"}
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

    ai_title = None
    ai_description = None
    try:
        needs_title = not title or len((title or "").strip()) < 6
        needs_desc = not description or len((description or "").strip()) < 12
        if needs_title or needs_desc:
            sample_text = ""
            if file_ext == ".pdf" and fitz is not None:
                try:
                    doc = fitz.open(str(save_path))
                    sample_text = "\n".join([doc[i].get_text() for i in range(min(len(doc), 3))])
                    doc.close()
                except Exception:
                    sample_text = ""
            prompt = (
                "Generate a clean title and a 1-2 sentence description for this presentation. "
                "Return JSON with keys 'title' and 'description' only.\n\n"
                f"Original title: {title or ''}\n"
                f"Existing description: {description or ''}\n"
                f"Extracted text: {sample_text[:2000]}"
            )
            ai_raw = chat_completion(
                [{"role": "user", "content": prompt}],
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini") if get_ai_provider() == "openai" else os.getenv("OLLAMA_MODEL", "qwen2.5:3b"),
                max_tokens=220,
                temperature=0.4,
            )
            try:
                parsed = json.loads(ai_raw.strip())
                ai_title = (parsed.get("title") or "").strip() or None
                ai_description = (parsed.get("description") or "").strip() or None
            except Exception:
                parts = [p.strip() for p in ai_raw.split("\n") if p.strip()]
                if parts:
                    ai_title = parts[0][:120]
                    if len(parts) > 1:
                        ai_description = " ".join(parts[1:])[:400]
    except Exception:
        pass

    with Session(engine) as session:
        p = Presentation(
            title=title or Path(file.filename).stem,
            description=description,
            filename=unique_name,
            mimetype=file.content_type,
            owner_id=current_user.id if current_user else None,
            privacy="public",
            allow_download=True,
            ai_title=ai_title,
            ai_description=ai_description,
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

        # conversion/preview generation for API uploads: always enqueue Redis/RQ job and attempt sync fallback
        if file_ext in {".ppt", ".pptx", ".pptm"}:
            try:
                from .tasks import enqueue_conversion, convert_presentation
                enqueue_conversion(p.id, unique_name)  # Always enqueue in Redis/RQ
            except Exception:
                pass  # If Redis/RQ is unavailable, ignore
            # Synchronous fallback: try to convert immediately so previews show up
            try:
                convert_presentation(p.id, unique_name)
            except Exception:
                pass
        try:
            followers = session.exec(
                select(Follow.follower_id).where(Follow.following_id == current_user.id)
            ).all()
            for row in followers:
                fid = row[0] if isinstance(row, (list, tuple)) else row
                if not fid:
                    continue
                n = Notification(
                    recipient_id=int(fid),
                    actor_id=current_user.id,
                    verb='new_upload',
                    target_type='presentation',
                    target_id=p.id,
                )
                session.add(n)
            session.commit()
        except Exception:
            session.rollback()


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
    current_user = get_current_user_optional(request)
    if not current_user:
        # Force sign-in before viewing any presentation detail page
        next_url = request.url.path
        return RedirectResponse(url=f"/login?next={next_url}", status_code=status.HTTP_303_SEE_OTHER)

    owner_username = None
    owner_email = None
    with Session(engine) as session:
        p = session.get(Presentation, presentation_id)
        if not p:
            raise HTTPException(status_code=404, detail="Not found")
        # Resolve owner safely and increment views on the persisted model
        owner = session.get(User, p.owner_id) if p.owner_id else None
        # Enforce privacy: only the owner can see private presentations
        if getattr(p, "privacy", "public") == "private" and (
            not current_user or current_user.id != getattr(p, "owner_id", None)
        ):
            raise HTTPException(status_code=404, detail="Not found")
        p.views = (p.views or 0) + 1
        session.add(p)
        session.commit()
        session.refresh(p)
        # log view activity (best-effort)
        try:
            if current_user and current_user.id:
                act = Activity(user_id=current_user.id, verb="view", target_id=p.id)
                session.add(act)
                session.commit()
        except Exception:
            session.rollback()
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

        # AI results (summary/flashcards/quiz/mindmap) if available
        ai_rows = session.exec(
            select(AIResult)
            .where(AIResult.presentation_id == presentation_id)
            .order_by(AIResult.created_at.desc())
        ).all()
        ai_summary = None
        ai_flashcards = None
        ai_quiz = None
        ai_mindmap = None
        def _is_ai_error(text: str | None) -> bool:
            if not text:
                return False
            t = text.lower()
            return (
                "openai error" in t
                or "insufficient_quota" in t
                or "rate limit" in t
                or "quota" in t
                or "api error" in t
            )
        for r in ai_rows:
            if r.task_type == "summary" and not ai_summary and not _is_ai_error(r.result):
                ai_summary = r.result
            if r.task_type == "flashcards" and not ai_flashcards and not _is_ai_error(r.result):
                ai_flashcards = r.result
            if r.task_type == "quiz" and not ai_quiz and not _is_ai_error(r.result):
                ai_quiz = r.result
            if r.task_type == "mindmap" and not ai_mindmap and not _is_ai_error(r.result):
                ai_mindmap = r.result

        # Always attempt conversion for new presentations (enqueue + sync fallback)
        viewer_url = None
        conversion_status = None
        original_url = None
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
                # Always try to enqueue and convert if not already done
                if not job:
                    try:
                        enqueue_conversion(p.id, p.filename)
                        conversion_status = "queued"
                    except Exception:
                        conversion_status = "failed"
                # synchronous fallback removed from inside DB session to avoid
                # potential SQLite locking when worker functions open their own
                # sessions. A best-effort conversion will be attempted after
                # the session closes.
                if not viewer_url:
                    conversion_status = job.status if job else "queued"
            elif ext in ('.mp4', '.mov', '.m4v', '.webm'):
                # prefer a transcode result (if available) for smooth playback
                if job and getattr(job, 'result', None):
                    cand = Path(UPLOAD_DIR) / job.result
                    if cand.exists():
                        original_url = f"/download/{job.result}"
                        viewer_url = original_url
                        conversion_status = "ready"
                    else:
                        # default to original file (range support covers streaming)
                        viewer_url = original_url
                        conversion_status = "ready"
                else:
                    viewer_url = original_url
                    conversion_status = "queued" if job else "ready"
            else:
                conversion_status = "unsupported"

        # Prefer cached thumbnail (Redis/filesystem) for immediate render
        try:
            redis_url = os.getenv('REDIS_URL')
            if _redis and redis_url:
                rc = _redis.from_url(redis_url)
                key = f"presentation:{presentation_id}:thumbnails"
                val = rc.get(key)
                if val:
                    try:
                        urls = json.loads(val)
                        if urls:
                            first = urls[0]
                            if f"/presentations/{presentation_id}/slide/" in first:
                                first = f"/media/thumbs/{presentation_id}/slide_0.png"
                            viewer_url = first
                            conversion_status = "ready"
                    except Exception:
                        pass
        except Exception:
            pass

        # If still no viewer_url, but slide 0 exists, show it as a fallback
        thumbs_dir = Path(UPLOAD_DIR) / "thumbs" / str(presentation_id)
        slide0 = thumbs_dir / "slide_0.png"
        if slide0.exists():
            viewer_url = f"/media/thumbs/{presentation_id}/slide_0.png"
            conversion_status = "ready"

        # build a lightweight presentation object for templates (avoid setting attrs on SQLModel)
        if owner:
            owner_username = getattr(owner, 'username', None)
            owner_email = getattr(owner, 'email', None)
            owner_avatar = getattr(owner, 'avatar', None)
    p_safe = SimpleNamespace(
        id=p.id,
        title=p.title,
        description=getattr(p, 'description', None),
        filename=p.filename,
        music_url=getattr(p, 'music_url', None) if hasattr(p, 'music_url') else None,
        mimetype=p.mimetype,
        owner_id=p.owner_id,
        owner_username=owner_username,
        owner_email=owner_email,
        owner_avatar=owner_avatar,
        owner_site_role=getattr(owner, 'site_role', None) if owner else None,
        views=p.views,
        cover_url=getattr(p, 'cover_url', None) if hasattr(p, 'cover_url') else None,
        created_at=getattr(p, 'created_at', None),
        downloads=getattr(p, 'downloads', 0),
        ai_title=getattr(p, 'ai_title', None),
        ai_description=getattr(p, 'ai_description', None),
        ai_summary=getattr(p, 'ai_summary', None),
    )

    # Best-effort synchronous conversion (moved outside DB session to avoid locks)
    try:
        if p.filename:
            ext = Path(p.filename).suffix.lower()
            if ext in {'.ppt', '.pptx', '.pptm'} and not viewer_url:
                try:
                    from .tasks import convert_presentation
                    convert_presentation(p.id, p.filename)
                except Exception:
                    # conversion failed or not available; continue gracefully
                    pass
                # check for a converted PDF result after attempting conversion
                with Session(engine) as _s:
                    latest_job = _s.exec(
                        select(ConversionJob)
                        .where(ConversionJob.presentation_id == presentation_id)
                        .order_by(ConversionJob.created_at.desc())
                    ).first()
                    if latest_job and latest_job.result:
                        conv_path = Path(UPLOAD_DIR) / latest_job.result
                        if conv_path.exists():
                            viewer_url = f"/presentations/{presentation_id}/converted_pdf?inline=1"
                            conversion_status = "ready"
    except Exception:
        # best-effort only; do not block rendering the page on errors here
        pass

    # determine follow status for current user (subscribe)
    cu = getattr(request.state, 'current_user', None)
    # csrf token for owner-only actions (e.g., delete)
    csrf = None
    csrf_created = False
    try:
        csrf = request.cookies.get('csrf_token') if hasattr(request, 'cookies') else None
    except Exception:
        csrf = None
    if not csrf:
        csrf = uuid.uuid4().hex
        csrf_created = True
    is_following = False
    followers_count = 0
    owner_presentation_count = 0
    # defaults for bookmarks — ensure variables exist even if user not logged in
    bookmarks_count = 0
    is_bookmarked = False
    # default for like state for current user
    is_liked = False
    creator_badges: list[str] = []
    collections = []
    if p.owner_id is not None:
        with Session(engine) as session:
            followers = session.exec(select(Follow).where(Follow.following_id == p.owner_id)).all()
            followers_count = len(followers)
            if cu and cu.id:
                exists = session.exec(
                    select(Follow).where((Follow.follower_id == cu.id) & (Follow.following_id == p.owner_id))
                ).first()
                is_following = bool(exists)
                # collections for save-to-folder UX
                collections = session.exec(
                    select(Collection).where(Collection.user_id == cu.id).order_by(Collection.created_at.desc())
                ).all()
                # did the current user like this presentation?
                try:
                    liked_row = session.exec(
                        select(Like).where(
                            (Like.user_id == cu.id)
                            & (Like.presentation_id == presentation_id)
                        )
                    ).first()
                    is_liked = bool(liked_row)
                except Exception:
                    is_liked = False
                # bookmarks for this presentation
                bookmarks = session.exec(select(Bookmark).where(Bookmark.presentation_id == presentation_id)).all()
                bookmarks_count = len(bookmarks)
                is_bookmarked = False
                if cu and cu.id:
                    b_exists = session.exec(select(Bookmark).where((Bookmark.presentation_id == presentation_id) & (Bookmark.user_id == cu.id))).first()
                    is_bookmarked = bool(b_exists)
                # compute owner's total presentations
                try:
                    owner_presentation_count = session.exec(
                        select(func.count(Presentation.id)).where(Presentation.owner_id == p.owner_id)
                    ).one()
                    # SQLAlchemy may return a scalar inside a tuple depending on driver
                    if isinstance(owner_presentation_count, tuple) or isinstance(owner_presentation_count, list):
                        owner_presentation_count = int(owner_presentation_count[0])
                    else:
                        owner_presentation_count = int(owner_presentation_count)
                except Exception:
                    owner_presentation_count = 0
                try:
                    totals = session.exec(
                        select(func.coalesce(func.sum(Presentation.views), 0), func.coalesce(func.sum(Presentation.downloads), 0))
                        .where(Presentation.owner_id == p.owner_id)
                    ).one()
                    total_views = int(totals[0] or 0)
                    total_downloads = int(totals[1] or 0)
                except Exception:
                    total_views = 0
                    total_downloads = 0
                creator_badges = _compute_creator_badges(
                    getattr(owner, "site_role", None) if owner else None,
                    total_views,
                    total_downloads,
                    followers_count,
                )

    response = templates.TemplateResponse(
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
            "ai_summary": ai_summary or (p.ai_summary if not _is_ai_error(getattr(p, "ai_summary", None)) else None),
            "ai_flashcards": ai_flashcards,
            "ai_quiz": ai_quiz,
            "ai_mindmap": ai_mindmap,
            "is_following": is_following,
            "followers_count": followers_count,
            "bookmarks_count": bookmarks_count,
            "is_bookmarked": is_bookmarked,
            "owner_presentation_count": owner_presentation_count,
            "is_liked": is_liked,
            "creator_badges": creator_badges,
            "collections": collections,
            "current_user": cu,
            "csrf_token": csrf,
        },
    )
    if csrf_created:
        response.set_cookie('csrf_token', csrf, samesite='Lax')
    return response


@app.post("/presentations/{presentation_id}/delete")
def delete_presentation(
    request: Request,
    presentation_id: int,
    csrf_token: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
):
    """Allow the owner to permanently delete a presentation.

    Also cleans up associated files (original upload, thumbnails, converted
    PDFs) and dependent rows so the database stays consistent.
    """
    validate_csrf(request, csrf_token)

    from .models import AIResult  # imported lazily to avoid circular issues

    with Session(engine) as session:
        pres = session.get(Presentation, presentation_id)
        if not pres:
            raise HTTPException(status_code=404, detail="presentation not found")
        if pres.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="forbidden")

        # File cleanup
        base_dir = Path(UPLOAD_DIR)
        # original file
        if pres.filename:
            try:
                orig = base_dir / pres.filename
                if orig.exists():
                    orig.unlink()
            except Exception:
                logger.exception("Failed to delete original file for presentation %s", presentation_id)

        # thumbnails directory
        try:
            thumbs_dir = base_dir / "thumbs" / str(presentation_id)
            if thumbs_dir.exists():
                shutil.rmtree(thumbs_dir, ignore_errors=True)
        except Exception:
            logger.exception("Failed to delete thumbnails for presentation %s", presentation_id)

        # any converted PDFs recorded on ConversionJob.result
        jobs = session.exec(select(ConversionJob).where(ConversionJob.presentation_id == presentation_id)).all()
        for job in jobs or []:
            if job.result:
                try:
                    cand = base_dir / job.result
                    if cand.exists():
                        cand.unlink()
                except Exception:
                    logger.exception("Failed to delete converted file %s for presentation %s", job.result, presentation_id)

        # Dependent rows: likes, bookmarks, comments, AI results, library items, conversion jobs
        likes = session.exec(select(Like).where(Like.presentation_id == presentation_id)).all()
        for row in likes:
            session.delete(row)

        bookmarks = session.exec(select(Bookmark).where(Bookmark.presentation_id == presentation_id)).all()
        for row in bookmarks:
            session.delete(row)

        comments = session.exec(select(Comment).where(Comment.presentation_id == presentation_id)).all()
        for row in comments:
            session.delete(row)

        ai_rows = session.exec(select(AIResult).where(AIResult.presentation_id == presentation_id)).all()
        for row in ai_rows:
            session.delete(row)

        lib_items = session.exec(select(LibraryItem).where(LibraryItem.presentation_id == presentation_id)).all()
        for row in lib_items:
            session.delete(row)

        for job in jobs or []:
            session.delete(job)

        # finally delete the presentation itself
        session.delete(pres)
        session.commit()

    # after delete, send user back to homepage
    return RedirectResponse("/", status_code=303)


@app.post("/presentations/{presentation_id}/music")
def set_presentation_music(presentation_id: int, payload: dict = Body(...), current_user: User = Depends(get_current_user)):
    """Set or clear the background music URL for a presentation.

    Only the presentation owner may modify this field. Expects JSON {"music_url": "..."}.
    """
    music_url = (payload.get("music_url") or "").strip()
    with Session(engine) as session:
        pres = session.get(Presentation, presentation_id)
        if not pres:
            raise HTTPException(status_code=404, detail="presentation not found")
        if pres.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="forbidden")
        pres.music_url = music_url or None
        session.add(pres)
        session.commit()
        session.refresh(pres)
    return {"ok": True, "music_url": pres.music_url}


@app.get('/auth/spotify/login')
def spotify_login(request: Request, current_user: User = Depends(get_current_user)):
    if not SPOTIFY_CLIENT_ID:
        raise HTTPException(status_code=500, detail='Spotify not configured')
    scopes = 'streaming user-read-playback-state user-modify-playback-state user-read-email'
    params = {
        'client_id': SPOTIFY_CLIENT_ID,
        'response_type': 'code',
        'redirect_uri': SPOTIFY_REDIRECT,
        'scope': scopes,
    }
    qs = '&'.join(f"{k}={quote(v)}" for k, v in params.items())
    url = f"https://accounts.spotify.com/authorize?{qs}"
    return RedirectResponse(url)


@app.get('/auth/spotify/callback')
def spotify_callback(request: Request, code: str = Query(None), error: str = Query(None), current_user: User = Depends(get_current_user)):
    if error:
        raise HTTPException(status_code=400, detail=f"Spotify error: {error}")
    if not code:
        raise HTTPException(status_code=400, detail='Missing code')
    # Exchange code for tokens
    token_url = 'https://accounts.spotify.com/api/token'
    data = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': SPOTIFY_REDIRECT,
    }
    auth = base64.b64encode(f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()).decode()
    headers = {'Authorization': f'Basic {auth}', 'Content-Type': 'application/x-www-form-urlencoded'}
    try:
        r = httpx.post(token_url, data=data, headers=headers, timeout=10)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Failed to exchange token: {e}')
    refresh = payload.get('refresh_token')
    access = payload.get('access_token')
    if not refresh:
        raise HTTPException(status_code=500, detail='No refresh token returned')
    # persist refresh token on user
    with Session(engine) as session:
        u = session.get(User, current_user.id)
        u.spotify_refresh_token = refresh
        session.add(u)
        session.commit()
    return RedirectResponse(url='/')


def _refresh_spotify_token_for_user(u: User):
    if not u or not getattr(u, 'spotify_refresh_token', None):
        return None
    token_url = 'https://accounts.spotify.com/api/token'
    data = {'grant_type': 'refresh_token', 'refresh_token': u.spotify_refresh_token}
    auth = base64.b64encode(f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()).decode()
    headers = {'Authorization': f'Basic {auth}', 'Content-Type': 'application/x-www-form-urlencoded'}
    try:
        r = httpx.post(token_url, data=data, headers=headers, timeout=10)
        r.raise_for_status()
        return r.json().get('access_token')
    except Exception:
        return None


@app.get('/auth/spotify/token')
def spotify_token(request: Request, current_user: User = Depends(get_current_user)):
    # Return a short-lived access token for the current user using stored refresh token
    with Session(engine) as session:
        u = session.get(User, current_user.id)
        if not u or not getattr(u, 'spotify_refresh_token', None):
            raise HTTPException(status_code=404, detail='Spotify not connected')
        token = _refresh_spotify_token_for_user(u)
        if not token:
            raise HTTPException(status_code=500, detail='Failed to refresh token')
        return JSONResponse({'access_token': token})


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
                # Treat any existing converted PDF as ready, even if the
                # stored job status isn't "finished".
                if job and job.result:
                    conv_path = Path(UPLOAD_DIR) / job.result
                    if conv_path.exists():
                        viewer_url = f"/presentations/{presentation_id}/converted_pdf?inline=1"
                        conversion_status = "ready"
                if not viewer_url:
                    conversion_status = job.status if job else "queued"
                    # Try to enqueue background conversion; fallback to
                    # synchronous conversion so newly uploaded presentations
                    # behave the same as older ones.
                    if not job:
                        try:
                            enqueue_conversion(p.id, p.filename)
                            conversion_status = "queued"
                        except Exception:
                            conversion_status = "failed"
                    # synchronous fallback attempt
                    try:
                        from .tasks import convert_presentation

                        convert_presentation(p.id, p.filename)
                        with Session(engine) as _s:
                            latest_job = _s.exec(
                                select(ConversionJob)
                                .where(ConversionJob.presentation_id == presentation_id)
                                .order_by(ConversionJob.created_at.desc())
                            ).first()
                            if latest_job and latest_job.result:
                                conv_path = Path(UPLOAD_DIR) / latest_job.result
                                if conv_path.exists():
                                    viewer_url = f"/presentations/{presentation_id}/converted_pdf?inline=1"
                                    conversion_status = "ready"
                    except Exception:
                        pass
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
    # First, consult Redis cache for thumbnails (fast path)
    try:
        redis_url = os.getenv('REDIS_URL')
        if _redis and redis_url:
            try:
                rc = _redis.from_url(redis_url)
                key = f"presentation:{presentation_id}:thumbnails"
                logger.debug("Checking redis for key %s", key)
                val = rc.get(key)
                if val:
                    try:
                        urls = json.loads(val)
                        logger.info("Found cached thumbnails in Redis for %s: %s", presentation_id, urls)
                        return {"thumbnails": urls}
                    except Exception as e:
                        logger.exception("Failed to parse redis thumbnails for %s: %s", presentation_id, e)
                else:
                    logger.debug("No redis value for %s", key)
            except Exception as e:
                logger.exception("Redis lookup failed for thumbnails: %s", e)
    except Exception as e:
        logger.exception("Redis check error: %s", e)

    thumbs_dir = Path(UPLOAD_DIR) / "thumbs" / str(presentation_id)
    # If thumbs dir is missing or present but empty, attempt generation/enqueue
    if not thumbs_dir.exists() or (thumbs_dir.exists() and not any(thumbs_dir.glob('slide_*.png'))):
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
        # attempt to generate thumbnails from an available PDF (on-demand) using
        # the shared `generate_pdf_thumbnails` helper which includes fallbacks.
        try:
            from .convert import generate_pdf_thumbnails
            with Session(engine) as session:
                p = session.get(Presentation, presentation_id)
                pdf_path = None
                if p and getattr(p, 'filename', None):
                    src = Path(UPLOAD_DIR) / p.filename
                    if src.exists() and src.suffix.lower() == '.pdf':
                        pdf_path = src
                if not pdf_path:
                    # fallback to converted PDF result if available
                    job = session.exec(
                        select(ConversionJob)
                        .where(ConversionJob.presentation_id == presentation_id)
                        .order_by(ConversionJob.created_at.desc())
                    ).first()
                    if job and job.result:
                        conv = Path(UPLOAD_DIR) / job.result
                        if conv.exists():
                            pdf_path = conv
                if pdf_path:
                    try:
                        thumbs_dir.mkdir(parents=True, exist_ok=True)
                        thumbs = generate_pdf_thumbnails(str(pdf_path), str(thumbs_dir), max_pages=10)
                        if thumbs:
                            # Cache URLs in Redis for fast retrieval by the UI
                            try:
                                redis_url = os.getenv('REDIS_URL')
                                if _redis and redis_url:
                                    rc = _redis.from_url(redis_url)
                                    key = f"presentation:{presentation_id}:thumbnails"
                                    urls = [f"/media/thumbs/{presentation_id}/slide_{i}.png" for i in range(len(thumbs))]
                                    try:
                                        rc.set(key, json.dumps(urls))
                                        rc.expire(key, 7 * 24 * 3600)
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                    except Exception:
                        # on-demand generation failed; ignore and fall back
                        pass
        except Exception:
            pass

        # if thumbnails still don't exist (or dir empty), return empty
        files_present = thumbs_dir.exists() and any(thumbs_dir.glob('slide_*.png'))
        if not files_present:
            return {"thumbnails": []}

    files = sorted(thumbs_dir.glob("slide_*.png"))
    # return URLs relative to server
    urls = [f"/media/thumbs/{presentation_id}/slide_{i}.png" for i in range(len(files))]
    logger.debug("Returning %d thumbnail urls for presentation %s", len(urls), presentation_id)
    return {"thumbnails": urls}


@app.get("/debug/thumbnails/{presentation_id}")
def debug_thumbs(presentation_id: int):
    """Debug helper: returns Redis cache and filesystem status for thumbnails."""
    out = {"redis": None, "files": [], "thumbs_dir": None}
    try:
        redis_url = os.getenv('REDIS_URL')
        if _redis and redis_url:
            rc = _redis.from_url(redis_url)
            key = f"presentation:{presentation_id}:thumbnails"
            try:
                val = rc.get(key)
                out['redis'] = json.loads(val) if val else None
            except Exception as e:
                out['redis'] = f"error:{e}"
    except Exception as e:
        out['redis'] = f"error:{e}"

    thumbs_dir = Path(UPLOAD_DIR) / "thumbs" / str(presentation_id)
    out['thumbs_dir'] = str(thumbs_dir)
    if thumbs_dir.exists():
        out['files'] = [str(p.name) for p in sorted(thumbs_dir.glob('slide_*.png'))]
    return out


@app.post('/debug/run_convert/{presentation_id}')
def debug_run_convert(presentation_id: int):
    """Developer helper: run conversion/thumbnail generation synchronously and return outcome."""
    with Session(engine) as session:
        p = session.get(Presentation, presentation_id)
        if not p or not getattr(p, 'filename', None):
            return JSONResponse({'ok': False, 'error': 'presentation or file not found'}, status_code=404)
        filename = p.filename

    save_dir = Path(UPLOAD_DIR)
    src = save_dir / filename
    if not src.exists():
        return JSONResponse({'ok': False, 'error': 'source file missing on disk'}, status_code=404)

    try:
        from .convert import generate_pdf_thumbnails, convert_doc_to_pdf, generate_video_thumbnail, generate_audio_waveform
    except Exception as e:
        return JSONResponse({'ok': False, 'error': f'failed to import convert helpers: {e}'} , status_code=500)

    thumbs_dir = save_dir / 'thumbs' / str(presentation_id)
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    ext = src.suffix.lower()
    result = {'ok': True, 'generated': []}
    try:
        if ext == '.pdf':
            thumbs = generate_pdf_thumbnails(str(src), str(thumbs_dir), max_pages=5)
            result['generated'] = thumbs
        elif ext in ('.doc', '.docx', '.odt', '.ppt', '.pptx'):
            pdfp = convert_doc_to_pdf(str(src), str(save_dir))
            if pdfp:
                thumbs = generate_pdf_thumbnails(pdfp, str(thumbs_dir), max_pages=5)
                result['generated'] = thumbs
            else:
                result['ok'] = False
                result['error'] = 'document->pdf conversion failed'
        elif ext in ('.mp4', '.mov', '.m4v', '.webm'):
            out = thumbs_dir / 'video_preview.png'
            r = generate_video_thumbnail(str(src), str(out))
            result['generated'] = [str(out)] if r else []
        elif ext in ('.mp3', '.wav', '.m4a', '.ogg'):
            out = thumbs_dir / 'waveform.png'
            r = generate_audio_waveform(str(src), str(out))
            result['generated'] = [str(out)] if r else []
        else:
            result['ok'] = False
            result['error'] = f'unhandled extension: {ext}'
    except Exception as e:
        result['ok'] = False
        result['error'] = str(e)

    # list files in thumbs_dir
    result['thumbs_dir'] = str(thumbs_dir)
    result['files'] = [str(p.name) for p in sorted(thumbs_dir.glob('slide_*.png'))]
    # cache in redis if available
    try:
        redis_url = os.getenv('REDIS_URL')
        if _redis and redis_url and result.get('files'):
            rc = _redis.from_url(redis_url)
            urls = [f"/media/thumbs/{presentation_id}/slide_{i}.png" for i in range(len(result['files']))]
            try:
                rc.set(f"presentation:{presentation_id}:thumbnails", json.dumps(urls))
                rc.expire(f"presentation:{presentation_id}:thumbnails", 7 * 24 * 3600)
                result['cached'] = True
            except Exception as e:
                result['cached'] = f'failed: {e}'
    except Exception:
        result['cached'] = 'redis not available'

    return JSONResponse(result)


@app.get("/presentations/{presentation_id}/slide/{index}")
def get_slide_image(
    presentation_id: int,
    index: int,
    hd: bool = Query(False),
    quality: float = Query(1.0, ge=1.0, le=8.0),
):
    thumbs_dir = Path(UPLOAD_DIR) / "thumbs" / str(presentation_id)
    thumbs_hd_dir = Path(UPLOAD_DIR) / "thumbs_hd" / str(presentation_id)
    quality_bucket = f"q{int(round(quality * 100))}"
    target_dir = (thumbs_hd_dir / quality_bucket) if hd else thumbs_dir
    path = target_dir / f"slide_{index}.png"
    fallback_path = thumbs_dir / f"slide_{index}.png"
    if not path.exists():
        # Attempt to generate the requested slide on-demand from an available PDF.
        # Prefer a converted PDF (from ConversionJob.result), then the original upload.
        pdf_path = None
        try:
            # check converted PDF
            with Session(engine) as session:
                job = session.exec(
                    select(ConversionJob)
                    .where(ConversionJob.presentation_id == presentation_id)
                    .order_by(ConversionJob.created_at.desc())
                ).first()
                if job and job.result:
                    cand = Path(UPLOAD_DIR) / job.result
                    if cand.exists():
                        pdf_path = cand
        except Exception:
            pdf_path = None

        # fallback to original presentation file
        if not pdf_path:
            try:
                with Session(engine) as session:
                    p = session.get(Presentation, presentation_id)
                    if p and getattr(p, 'filename', None):
                        src = Path(UPLOAD_DIR) / p.filename
                        if src.exists() and src.suffix.lower() == '.pdf':
                            pdf_path = src
            except Exception:
                pdf_path = None

        # Try to render a PNG for the requested page if we found a PDF
        if pdf_path:
            target_dir.mkdir(parents=True, exist_ok=True)
            try:
                if fitz is not None:
                    doc = fitz.open(str(pdf_path))
                    if index < doc.page_count:
                        # Use quality-aware HD scale so high zoom requests can be generated crisply.
                        base_hd_scale = float(os.getenv('HD_SLIDE_SCALE', '3.0'))
                        base_thumb_scale = float(os.getenv('THUMBNAIL_SCALE', '2.0'))
                        if hd:
                            scale = min(12.0, base_hd_scale * quality)
                        else:
                            scale = base_thumb_scale
                        mat = fitz.Matrix(scale, scale)
                        page = doc.load_page(index)
                        pix = page.get_pixmap(matrix=mat)
                        out_path = path
                        pix.save(str(out_path))
                        doc.close()
                        return FileResponse(out_path, media_type='image/png', filename=out_path.name)
                # Fallback: try ImageMagick `convert` to generate a PNG for the page
                try:
                    pattern = str(target_dir / 'slide_%d.png')
                    render_size = f"x{min(6000, int(2500 * quality))}" if hd else 'x2000'
                    subprocess.run(['convert', str(pdf_path), '-thumbnail', render_size, pattern], check=True)
                    if path.exists():
                        return FileResponse(path, media_type='image/png', filename=path.name)
                except Exception:
                    pass
            except Exception:
                pass

        if hd and fallback_path.exists():
            return FileResponse(fallback_path, media_type='image/png', filename=fallback_path.name)

        raise HTTPException(status_code=404, detail='Slide not found')
    return FileResponse(path, media_type="image/png", filename=path.name)


@app.api_route("/presentations/{presentation_id}/converted_pdf", methods=["GET", "HEAD"])
def get_converted_pdf(presentation_id: int, inline: bool = Query(False)):
    with Session(engine) as session:
        job = session.exec(
            select(ConversionJob)
            .where(ConversionJob.presentation_id == presentation_id)
            .order_by(ConversionJob.created_at.desc())
        ).first()
        pdf_path = None
        if job and job.result:
            cand = Path(UPLOAD_DIR) / job.result
            if cand.exists() and cand.suffix.lower() == ".pdf":
                pdf_path = cand
        if not pdf_path:
            # fallback to original PDF upload if available
            p = session.get(Presentation, presentation_id)
            if p and getattr(p, 'filename', None):
                cand = Path(UPLOAD_DIR) / p.filename
                if cand.exists() and cand.suffix.lower() == ".pdf":
                    pdf_path = cand
        if not pdf_path or not pdf_path.exists():
            raise HTTPException(status_code=404, detail="Converted PDF not found")

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


@app.get("/presentations/{presentation_id}/download")
def download_presentation_variant(
    request: Request,
    presentation_id: int,
    kind: str = Query("original"),
    current_user: User = Depends(get_current_user_optional),
):
    """Download variants: original|pdf|images|slides.
    - pdf: converted PDF if available, otherwise original if PDF.
    - images/slides: zip of slide thumbnails.
    """
    kind = (kind or "original").lower()
    with Session(engine) as session:
        p = session.get(Presentation, presentation_id)
        if not p:
            raise HTTPException(status_code=404, detail="Presentation not found")
        # privacy gate
        if getattr(p, "privacy", "public") == "private" and (not current_user or current_user.id != p.owner_id):
            raise HTTPException(status_code=404, detail="Not found")
        # download permission for non-owners
        if kind != "slides" and kind != "images":
            if not getattr(p, "allow_download", True) and (not current_user or current_user.id != p.owner_id):
                raise HTTPException(status_code=403, detail="Downloads are disabled")

        if kind in {"images", "slides"}:
            thumbs_dir = Path(UPLOAD_DIR) / "thumbs" / str(presentation_id)
            if not thumbs_dir.exists():
                raise HTTPException(status_code=404, detail="Slides not available")
            files = sorted(thumbs_dir.glob("slide_*.png"))
            if not files:
                raise HTTPException(status_code=404, detail="Slides not available")
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
            tmp.close()
            with zipfile.ZipFile(tmp.name, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for f in files:
                    zf.write(f, arcname=f.name)
            # increment downloads
            try:
                p.downloads = int(getattr(p, "downloads", 0) or 0) + 1
                session.add(p)
                session.commit()
            except Exception:
                session.rollback()
            return FileResponse(tmp.name, media_type="application/zip", filename=f"presentation_{presentation_id}_slides.zip")

        if kind == "pdf":
            pdf_path = None
            if p.filename and p.filename.lower().endswith(".pdf"):
                pdf_path = Path(UPLOAD_DIR) / p.filename
            else:
                job = session.exec(
                    select(ConversionJob)
                    .where(ConversionJob.presentation_id == presentation_id)
                    .order_by(ConversionJob.created_at.desc())
                ).first()
                if job and job.result:
                    cand = Path(UPLOAD_DIR) / job.result
                    if cand.exists():
                        pdf_path = cand
            if not pdf_path or not pdf_path.exists():
                raise HTTPException(status_code=404, detail="PDF not available")
            try:
                p.downloads = int(getattr(p, "downloads", 0) or 0) + 1
                session.add(p)
                session.commit()
            except Exception:
                session.rollback()
            return FileResponse(str(pdf_path), media_type="application/pdf", filename=pdf_path.name)

        # default: original
        if not p.filename:
            raise HTTPException(status_code=404, detail="File not found")
        return RedirectResponse(url=f"/download/{p.filename}", status_code=302)


@app.api_route("/download/{filename}", methods=["GET", "HEAD", "OPTIONS"])
def download_file(request: Request, filename: str, inline: bool = Query(False)):
    # support GET/HEAD/OPTIONS; OPTIONS is handled by CORSMiddleware but keep explicit route
    path = Path(UPLOAD_DIR) / filename
    if request.method == "OPTIONS":
        return PlainTextResponse("ok", status_code=200)

    if not path.exists():
        # If the original file is missing, fall back to a static placeholder
        # for inline previews (used by featured cards) instead of noisy 404s.
        if inline:
            try:
                placeholder_url = request.url_for("static", path="slide-placeholder.svg")
                return RedirectResponse(placeholder_url, status_code=302)
            except Exception:
                # If static lookup fails, just return a lightweight 404.
                raise HTTPException(status_code=404, detail="Not found")
        # For non-inline downloads, return a quiet 404 without extra logging.
        raise HTTPException(status_code=404, detail="Not found")

    guessed_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    # Look up the owning presentation (if any) to enforce privacy/download rules.
    from .models import Presentation as PresentationModel
    current_user = get_current_user_optional(request)
    with Session(engine) as session:
        pres = session.exec(
            select(PresentationModel).where(PresentationModel.filename == filename)
        ).first()

    if pres is not None:
        privacy_val = getattr(pres, "privacy", "public") or "public"
        allow_dl = getattr(pres, "allow_download", True)
        owner_id = getattr(pres, "owner_id", None)

        # Private presentations are only visible to their owner (both inline and download).
        if privacy_val == "private" and (not current_user or current_user.id != owner_id):
            raise HTTPException(status_code=404, detail="Not found")

        # For non-inline downloads, enforce the allow_download flag for non-owners.
        if not inline and not allow_dl and (not current_user or current_user.id != owner_id):
            raise HTTPException(status_code=403, detail="Downloads are disabled for this file")

        # count downloads for non-inline requests
        if not inline:
            try:
                with Session(engine) as _s:
                    p_upd = _s.get(PresentationModel, pres.id)
                    if p_upd:
                        p_upd.downloads = int(getattr(p_upd, "downloads", 0) or 0) + 1
                        _s.add(p_upd)
                        _s.commit()
            except Exception:
                pass

    # Support HTTP Range requests for efficient video streaming
    range_header = request.headers.get("range")
    if range_header:
        try:
            size = path.stat().st_size
            m = re.match(r"bytes=(\d+)-(\d*)", range_header)
            if m:
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
                if end >= size:
                    end = size - 1
                if start >= size:
                    # Invalid range
                    return PlainTextResponse(status_code=416, content="Requested Range Not Satisfiable")
                length = end - start + 1

                def file_stream(p: Path, _start: int, _length: int, chunk_size: int = 8192):
                    with open(p, "rb") as fh:
                        fh.seek(_start)
                        remaining = _length
                        while remaining > 0:
                            read_size = min(chunk_size, remaining)
                            chunk = fh.read(read_size)
                            if not chunk:
                                break
                            remaining -= len(chunk)
                            yield chunk

                headers = {
                    "Content-Range": f"bytes {start}-{end}/{size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(length),
                }
                # include disposition for inline vs attachment
                if inline:
                    headers["Content-Disposition"] = f'inline; filename="{filename}"'
                else:
                    headers["Content-Disposition"] = f'attachment; filename="{filename}"'

                return StreamingResponse(
                    file_stream(path, start, length),
                    status_code=206,
                    media_type=guessed_type,
                    headers=headers,
                )
        except Exception:
            # Fall through to full-file response on any error parsing/serving ranges
            pass

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


@app.post("/presentations/{presentation_id}/template")
def use_as_template(
    request: Request,
    presentation_id: int,
    csrf_token: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
):
    validate_csrf(request, csrf_token)
    with Session(engine) as session:
        src = session.get(Presentation, presentation_id)
        if not src:
            raise HTTPException(status_code=404, detail="Presentation not found")
        if not src.filename:
            raise HTTPException(status_code=400, detail="Template is missing file")
        # enforce privacy
        if getattr(src, "privacy", "public") == "private" and src.owner_id != current_user.id:
            raise HTTPException(status_code=404, detail="Not found")

        ext = Path(src.filename).suffix.lower()
        new_name = f"{uuid.uuid4().hex}{ext}"
        src_path = Path(UPLOAD_DIR) / src.filename
        dst_path = Path(UPLOAD_DIR) / new_name
        if not src_path.exists():
            raise HTTPException(status_code=404, detail="Source file missing")
        shutil.copyfile(src_path, dst_path)

        new_p = Presentation(
            title=f"Copy of {src.title}",
            description=src.description,
            filename=new_name,
            mimetype=src.mimetype,
            owner_id=current_user.id,
            privacy="public",
            allow_download=True,
            ai_title=src.ai_title,
            ai_description=src.ai_description,
            ai_summary=src.ai_summary,
            category_id=getattr(src, "category_id", None),
        )
        session.add(new_p)
        session.commit()
        session.refresh(new_p)
        new_p_id = new_p.id
        # copy tags
        try:
            tag_links = session.exec(
                select(PresentationTag).where(PresentationTag.presentation_id == src.id)
            ).all()
            for t in tag_links:
                session.add(PresentationTag(presentation_id=new_p.id, tag_id=t.tag_id))
            session.commit()
        except Exception:
            session.rollback()
    return RedirectResponse(url=f"/presentations/{new_p_id}", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/presentations/{presentation_id}/delete")
def delete_presentation(
    presentation_id: int,
    current_user: User = Depends(get_current_user),
):
    """Allow the owner of a presentation to delete it.

    Removes the Presentation record, associated likes, bookmarks, comments,
    classroom library items, conversion jobs, AI results, and related files
    on disk (original upload, converted PDF, and thumbnails).
    """
    with Session(engine) as session:
        p = session.get(Presentation, presentation_id)
        if not p:
            raise HTTPException(status_code=404, detail="Presentation not found")
        if p.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized to delete this presentation")

        # Collect file paths before deleting DB rows
        original_path = Path(UPLOAD_DIR) / p.filename if getattr(p, "filename", None) else None
        thumbs_dir = Path(UPLOAD_DIR) / "thumbs" / str(presentation_id)

        # Delete likes, bookmarks, comments, collections
        for model in (Like, Bookmark, Comment, CollectionItem):
            rows = session.exec(
                select(model).where(model.presentation_id == presentation_id)
            ).all()
            for row in rows:
                session.delete(row)

        # Delete classroom library items that reference this presentation
        lib_items = session.exec(
            select(LibraryItem).where(LibraryItem.presentation_id == presentation_id)
        ).all()
        for item in lib_items:
            session.delete(item)

        # Delete conversion jobs and remember their output files
        jobs = session.exec(
            select(ConversionJob).where(ConversionJob.presentation_id == presentation_id)
        ).all()
        conv_paths = []
        for job in jobs:
            if getattr(job, "result", None):
                conv_paths.append(Path(UPLOAD_DIR) / job.result)
            session.delete(job)

        # Delete any stored AI results for this presentation
        ai_rows = session.exec(
            select(AIResult).where(AIResult.presentation_id == presentation_id)
        ).all()
        for ar in ai_rows:
            session.delete(ar)

        # Finally delete the presentation itself
        session.delete(p)
        session.commit()

    # Best-effort cleanup of files on disk (ignore errors)
    try:
        if original_path and original_path.exists():
            original_path.unlink()
    except Exception:
        logger.exception("Failed to delete original presentation file", extra={"presentation_id": presentation_id})

    try:
        for cp in conv_paths:
            if cp and cp.exists():
                cp.unlink()
    except Exception:
        logger.exception("Failed to delete converted presentation file", extra={"presentation_id": presentation_id})

    try:
        if thumbs_dir.exists():
            shutil.rmtree(thumbs_dir, ignore_errors=True)
    except Exception:
        logger.exception("Failed to delete thumbnails directory", extra={"presentation_id": presentation_id})

    return RedirectResponse(url="/upload?scope=mine", status_code=status.HTTP_302_FOUND)


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
        # create notification for presentation owner
        try:
            p = session.get(Presentation, presentation_id)
            if p and p.owner_id and p.owner_id != current_user.id:
                n = Notification(recipient_id=p.owner_id, actor_id=current_user.id, verb='like', target_type='presentation', target_id=presentation_id)
                session.add(n)
                session.commit()
        except Exception:
            session.rollback()
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
        # create notification for the followed user
        try:
            if target.id != current_user.id:
                n = Notification(recipient_id=target.id, actor_id=current_user.id, verb='follow', target_type='user', target_id=current_user.id)
                session.add(n)
                session.commit()
        except Exception:
            session.rollback()
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
        # batch-like counts
        pres_ids = [p.id for p in presentations_raw]
        likes_map: dict[int, int] = {}
        if pres_ids:
            rows_likes = session.exec(
                select(Like.presentation_id, func.count(Like.id))
                .where(Like.presentation_id.in_(list(pres_ids)))
                .group_by(Like.presentation_id)
            ).all()
            likes_map = {int(r[0]): int(r[1]) for r in rows_likes}

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
                    owner_site_role=getattr(owner, "site_role", None) if owner else None,
                    owner_email=getattr(owner, "email", None) if owner else None,
                    views=getattr(p, "views", None),
                    downloads=getattr(p, "downloads", 0),
                    likes_count=likes_map.get(p.id, 0),
                    cover_url=getattr(p, "cover_url", None) if hasattr(p, "cover_url") else None,
                    category=SimpleNamespace(name=cat.name) if cat is not None else None,
                    created_at=getattr(p, "created_at", None),
                )
            )

        # liked presentations
        liked_rows = session.exec(
            select(Like.presentation_id).where(Like.user_id == user.id)
        ).all()
        liked_ids = [r[0] if isinstance(r, (list, tuple)) else r for r in liked_rows]
        liked_presentations_raw = []
        liked_presentations = []
        likes_map_liked: dict[int, int] = {}
        if liked_ids:
            liked_presentations_raw = session.exec(
                select(Presentation)
                .where(Presentation.id.in_(list(liked_ids)))
                .options(selectinload(Presentation.owner), selectinload(Presentation.category))
                .order_by(Presentation.created_at.desc())
            ).all()
            liked_pres_ids = [p.id for p in liked_presentations_raw]
            if liked_pres_ids:
                rows_likes_liked = session.exec(
                    select(Like.presentation_id, func.count(Like.id))
                    .where(Like.presentation_id.in_(list(liked_pres_ids)))
                    .group_by(Like.presentation_id)
                ).all()
                likes_map_liked = {int(r[0]): int(r[1]) for r in rows_likes_liked}

        for p in liked_presentations_raw:
            owner = getattr(p, "owner", None)
            cat = getattr(p, "category", None)
            liked_presentations.append(
                SimpleNamespace(
                    id=p.id,
                    title=p.title,
                    description=getattr(p, "description", None),
                    filename=p.filename,
                    mimetype=p.mimetype,
                    owner_id=p.owner_id,
                    owner_username=getattr(owner, "username", None) if owner else None,
                    owner_site_role=getattr(owner, "site_role", None) if owner else None,
                    owner_email=getattr(owner, "email", None) if owner else None,
                    views=getattr(p, "views", None),
                    downloads=getattr(p, "downloads", 0),
                    likes_count=likes_map_liked.get(p.id, 0),
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
        follower_ids = [f.follower_id for f in followers]
        following_ids = [f.following_id for f in following]
        follower_users = session.exec(select(User).where(User.id.in_(list(follower_ids)))).all() if follower_ids else []
        following_users = session.exec(select(User).where(User.id.in_(list(following_ids)))).all() if following_ids else []
        followers_list = [
            SimpleNamespace(
                id=u.id,
                username=u.username,
                full_name=u.full_name,
                avatar=u.avatar,
                site_role=u.site_role,
            )
            for u in follower_users
        ]
        following_list = [
            SimpleNamespace(
                id=u.id,
                username=u.username,
                full_name=u.full_name,
                avatar=u.avatar,
                site_role=u.site_role,
            )
            for u in following_users
        ]
        follower_count = len(followers)
        following_count = len(following)
        owner_total_views = sum(getattr(p, 'views', 0) or 0 for p in presentations_raw)
        owner_total_downloads = sum(getattr(p, 'downloads', 0) or 0 for p in presentations_raw)
        # whether current user follows this profile
        is_following = False
        cu = getattr(current_user, 'id', None)
        if current_user and cu:
            exists = session.exec(select(Follow).where((Follow.follower_id == cu) & (Follow.following_id == user.id))).first()
            is_following = bool(exists)
        # collections (folders) for the profile owner only
        collections = []
        if current_user and cu and cu == user.id:
            rows = session.exec(
                select(Collection).where(Collection.user_id == user.id).order_by(Collection.created_at.desc())
            ).all()
            for c in rows:
                count = session.exec(
                    select(func.count(CollectionItem.id)).where(CollectionItem.collection_id == c.id)
                ).one()
                count_val = int(count[0]) if isinstance(count, (list, tuple)) else int(count)
                collections.append(SimpleNamespace(id=c.id, name=c.name, count=count_val))
        badges = _compute_creator_badges(getattr(user, "site_role", None), owner_total_views, owner_total_downloads, follower_count)
    return templates.TemplateResponse(
        "profile.html",
        {
            "request": request,
            "user_obj": user,
            "presentations": presentations,
            "liked_presentations": liked_presentations,
            "followers_list": followers_list,
            "following_list": following_list,
            "collections": collections,
            "follower_count": follower_count,
            "following_count": following_count,
            "owner_presentation_count": len(presentations),
            "owner_total_views": owner_total_views,
            "owner_total_downloads": owner_total_downloads,
            "badges": badges,
            "current_user": current_user,
            "is_following": is_following,
        },
    )


@app.get("/bookmarks", response_class=HTMLResponse)
def bookmarks_view(request: Request, current_user: User = Depends(get_current_user)):
    """Render a page showing the current user's bookmarked presentations."""
    if not current_user:
        return RedirectResponse(request.url_for('login')) if hasattr(request, 'url_for') else RedirectResponse('/login')
    with Session(engine) as session:
        rows = session.exec(select(Bookmark.presentation_id).where(Bookmark.user_id == current_user.id)).all()
        ids = [r[0] if isinstance(r, (list, tuple)) else r for r in rows]
        if not ids:
            presentations = []
        else:
            pres_raw = session.exec(
                select(Presentation)
                .where(Presentation.id.in_(list(ids)))
                .options(selectinload(Presentation.owner), selectinload(Presentation.category))
                .order_by(Presentation.created_at.desc())
            ).all()
            presentations = []
            pres_ids = [p.id for p in pres_raw]
            likes_map: dict[int, int] = {}
            if pres_ids:
                rows_likes = session.exec(
                    select(Like.presentation_id, func.count(Like.id))
                    .where(Like.presentation_id.in_(list(pres_ids)))
                    .group_by(Like.presentation_id)
                ).all()
                likes_map = {int(r[0]): int(r[1]) for r in rows_likes}

            for p in pres_raw:
                owner = getattr(p, 'owner', None)
                cat = getattr(p, 'category', None)
                presentations.append(
                    SimpleNamespace(
                        id=p.id,
                        title=p.title,
                        description=getattr(p, 'description', None),
                        filename=p.filename,
                        mimetype=p.mimetype,
                        owner_id=p.owner_id,
                        owner_username=getattr(owner, 'username', None) if owner else None,
                        owner_site_role=getattr(owner, 'site_role', None) if owner else None,
                        views=getattr(p, 'views', None),
                        likes_count=likes_map.get(p.id, 0),
                        cover_url=getattr(p, 'cover_url', None) if hasattr(p, 'cover_url') else None,
                        category=SimpleNamespace(name=cat.name) if cat is not None else None,
                    )
                )
    return templates.TemplateResponse(
        "bookmarks.html",
        {
            "request": request,
            "presentations": presentations,
            "current_user": current_user,
        },
    )


@app.get("/api/collections")
def list_collections(current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        rows = session.exec(
            select(Collection).where(Collection.user_id == current_user.id).order_by(Collection.created_at.desc())
        ).all()
        data = []
        for c in rows:
            count = session.exec(
                select(func.count(CollectionItem.id)).where(CollectionItem.collection_id == c.id)
            ).one()
            count_val = int(count[0]) if isinstance(count, (list, tuple)) else int(count)
            data.append({"id": c.id, "name": c.name, "count": count_val})
    return {"collections": data}


@app.post("/api/collections")
def create_collection(payload: dict = Body(...), current_user: User = Depends(get_current_user)):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    with Session(engine) as session:
        existing = session.exec(
            select(Collection).where((Collection.user_id == current_user.id) & (Collection.name == name))
        ).first()
        if existing:
            return {"id": existing.id, "name": existing.name}
        c = Collection(user_id=current_user.id, name=name)
        session.add(c)
        session.commit()
        session.refresh(c)
    return {"id": c.id, "name": c.name}


@app.post("/api/collections/{collection_id}/items")
def add_collection_item(collection_id: int, payload: dict = Body(...), current_user: User = Depends(get_current_user)):
    presentation_id = payload.get("presentation_id")
    if not presentation_id:
        raise HTTPException(status_code=400, detail="presentation_id is required")
    with Session(engine) as session:
        c = session.get(Collection, collection_id)
        if not c or c.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="collection not found")
        exists = session.exec(
            select(CollectionItem).where(
                (CollectionItem.collection_id == collection_id)
                & (CollectionItem.presentation_id == presentation_id)
            )
        ).first()
        if exists:
            return {"ok": True}
        item = CollectionItem(collection_id=collection_id, presentation_id=presentation_id)
        session.add(item)
        session.commit()
    return {"ok": True}


@app.delete("/api/collections/{collection_id}/items/{presentation_id}")
def remove_collection_item(collection_id: int, presentation_id: int, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        c = session.get(Collection, collection_id)
        if not c or c.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="collection not found")
        item = session.exec(
            select(CollectionItem).where(
                (CollectionItem.collection_id == collection_id)
                & (CollectionItem.presentation_id == presentation_id)
            )
        ).first()
        if item:
            session.delete(item)
            session.commit()
    return {"ok": True}


@app.get("/categories", response_class=HTMLResponse)
def categories_view(request: Request):
    """Show all categories in a styled grid. Reads a local JSON fallback, returns counts and paginates."""
    # pagination params
    page = int(request.query_params.get('page', 1))
    per_page = int(request.query_params.get('per_page', 24))

    # use cached counts (will compute on first call or after TTL)
    counts = get_category_counts()
    merged = get_available_category_names()

    # build enriched category objects with counts
    enriched = []
    for name in merged:
        enriched.append(SimpleNamespace(name=name, count=counts.get(name, 0)))

    total = len(enriched)
    # simple pagination
    start = (page - 1) * per_page
    end = start + per_page
    page_items = enriched[start:end]

    return templates.TemplateResponse(
        "categories.html",
        {
            "request": request,
            "categories": page_items,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": (total + per_page - 1) // per_page,
        },
    )


@app.post('/admin/import-categories')
def admin_import_categories(request: Request, current_user: User = Depends(get_current_user)):
    """Admin-only endpoint to import `data/categories.json` into the Category table.

    Protection: requires authenticated user whose username matches `ADMIN_USERNAME` env var.
    """
    admin_user = os.getenv('ADMIN_USERNAME')
    if not admin_user:
        raise HTTPException(status_code=403, detail='Admin import not configured')
    if not current_user or getattr(current_user, 'username', None) != admin_user:
        raise HTTPException(status_code=403, detail='Forbidden')

    data_path = Path(__file__).parent.parent / 'data' / 'categories.json'
    if not data_path.exists():
        raise HTTPException(status_code=404, detail='data/categories.json not found')

    try:
        with open(data_path, 'r', encoding='utf-8') as fh:
            payload = json.load(fh)
    except Exception:
        raise HTTPException(status_code=400, detail='Invalid JSON file')

    if not isinstance(payload, list):
        raise HTTPException(status_code=400, detail='categories.json must be a list')

    created = 0
    try:
        with Session(engine) as session:
            for item in payload:
                name = str(item).strip()
                if not name:
                    continue
                exists = session.exec(select(Category).where(Category.name == name)).first()
                if exists:
                    continue
                c = Category(name=name)
                session.add(c)
                created += 1
            session.commit()
    except Exception:
        logging.exception('failed to import categories')
        raise HTTPException(status_code=500, detail='Import failed')

    # invalidate cache so counts are recalculated
    # invalidate both Redis and memory cache
    try:
        redis_url = os.getenv('REDIS_URL')
        if _redis and redis_url:
            rc = _redis.from_url(redis_url)
            rc.delete('category_counts')
    except Exception:
        pass
    get_category_counts(force=True)

    return JSONResponse({'created': created})


@app.get('/admin/import-categories', response_class=HTMLResponse)
def admin_import_page(request: Request, current_user: User = Depends(get_current_user)):
    admin_user = os.getenv('ADMIN_USERNAME')
    if not admin_user:
        raise HTTPException(status_code=403, detail='Admin import not configured')
    if not current_user or getattr(current_user, 'username', None) != admin_user:
        raise HTTPException(status_code=403, detail='Forbidden')
    return templates.TemplateResponse('admin_import.html', {'request': request, 'current_user': current_user})


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
            # expected obj: { action: 'message'|'typing'|'read', content: '...', to: <user_id> }
            if obj.get("action") == "message":
                to_id = int(obj.get("to"))
                content = obj.get("content", "")
                # persist message
                with Session(engine) as session:
                    # allow message creation when the sender (me_id) follows
                    # the recipient (to_id). Mutual follow is no longer
                    # required for WebSocket chat messages.
                    follows = session.exec(select(Follow).where((Follow.follower_id == me_id) & (Follow.following_id == to_id))).first()
                    if not follows:
                        # ignore/skip message if sender does not follow recipient
                        continue
                    msg = Message(sender_id=me_id, recipient_id=to_id, content=content)
                    session.add(msg)
                    session.commit()
                    session.refresh(msg)
                    # create notification for recipient about new mutual chat message
                    try:
                        n = Notification(recipient_id=to_id, actor_id=me_id, verb="message", target_type="message", target_id=msg.id)
                        session.add(n)
                        session.commit()
                    except Exception:
                        session.rollback()
                out = {
                    "type": "message",
                    "id": msg.id,
                    "from": me_id,
                    "to": to_id,
                    "content": content,
                    "created_at": msg.created_at.isoformat(),
                    # sender metadata for richer chat UI (avatar, badges, names)
                    "username": getattr(user, "username", None),
                    "full_name": getattr(user, "full_name", None),
                    "avatar": getattr(user, "avatar", None),
                    "site_role": getattr(user, "site_role", None),
                }
                # send to recipient if online
                await manager.send_personal(to_id, out)
                # echo back to sender(s)
                await manager.send_personal(me_id, out)
            elif obj.get("action") == "typing":
                to_id = int(obj.get("to"))
                status = obj.get("status") or "start"
                payload = {
                    "type": "typing",
                    "from": me_id,
                    "to": to_id,
                    "status": status,
                }
                await manager.send_personal(to_id, payload)
            elif obj.get("action") == "read":
                to_id = int(obj.get("to"))
                msg_id = obj.get("message_id")
                payload = {
                    "type": "read",
                    "from": me_id,
                    "to": to_id,
                    "message_id": msg_id,
                }
                await manager.send_personal(to_id, payload)
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await manager.broadcast_presence(me_id, False)
        except Exception:
            pass
        await manager.disconnect(me_id, websocket)


# duplicate unread_counts removed; handled by earlier route to avoid path collision with /api/messages/{other_id}
def unread_counts(current_user: User = Depends(get_current_user)):
    # kept for backwards-compatibility; call same logic inline
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

        # load sender metadata for both participants so chat bubbles can
        # display names, avatars, and role badges
        participant_ids = {int(current_user.id), int(other_id)}
        users = session.exec(select(User).where(User.id.in_(participant_ids))).all()
        user_map = {int(u.id): u for u in users}

        # return most recent 50 (reverse-chronological in DB, sliced then serialized)
        out = []
        for m in msgs[:50]:
            sender = user_map.get(int(m.sender_id))
            out.append(
                {
                    "id": m.id,
                    "from": m.sender_id,
                    "to": m.recipient_id,
                    "content": m.content,
                    "file": m.file_url,
                    "thumbnail": m.thumbnail_url,
                    "created_at": m.created_at.isoformat(),
                    "read": bool(m.read),
                    "username": getattr(sender, "username", None) if sender else None,
                    "full_name": getattr(sender, "full_name", None) if sender else None,
                    "avatar": getattr(sender, "avatar", None) if sender else None,
                    "site_role": getattr(sender, "site_role", None) if sender else None,
                }
            )
    return JSONResponse(out)


@app.post('/api/messages/{other_id}/clear')
def clear_messages(other_id: int, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        stmt = delete(Message).where(
            ((Message.sender_id == current_user.id) & (Message.recipient_id == other_id))
            | ((Message.sender_id == other_id) & (Message.recipient_id == current_user.id))
        )
        session.exec(stmt)
        session.commit()
    return JSONResponse({"ok": True})


@app.post('/api/messages/{other_id}/read')
def mark_messages_read(other_id: int, current_user: User = Depends(get_current_user)):
    """Mark all messages from other_id -> current_user as read.

    Returns the last read message id for read-receipt UI.
    """
    last_read_id = None
    with Session(engine) as session:
        rows = session.exec(
            select(Message)
            .where(
                (Message.sender_id == other_id)
                & (Message.recipient_id == current_user.id)
                & (Message.read == False)
            )
            .order_by(Message.created_at.asc())
        ).all()
        for m in rows:
            m.read = True
            session.add(m)
            last_read_id = m.id
        if rows:
            session.commit()
    if last_read_id:
        try:
            import asyncio
            asyncio.create_task(manager.send_personal(other_id, {
                "type": "read",
                "from": current_user.id,
                "to": other_id,
                "message_id": last_read_id,
            }))
        except Exception:
            pass
    return JSONResponse({"ok": True, "last_read_id": last_read_id})


@app.get('/api/online/{user_id}')
def api_online(user_id: int):
    return JSONResponse({"online": manager.is_online(user_id)})


@app.get('/api/classrooms/{classroom_id}/chat/messages')
def classroom_chat_messages(classroom_id: int, current_user: User = Depends(get_current_user)):
    """Return recent classroom chat messages with sender metadata.

    Only members (student/teacher/admin) of the classroom can access.
    """
    with Session(engine) as session:
        classroom = session.get(Classroom, classroom_id)
        if not classroom:
            raise HTTPException(status_code=404, detail="Classroom not found")
        mem = session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.user_id == current_user.id)
            )
        ).first()
        if not mem:
            raise HTTPException(status_code=403, detail="Not a member of this classroom")

        rows = (
            session.exec(
                select(ClassroomMessage, User)
                .where(ClassroomMessage.classroom_id == classroom_id)
                .join(User, User.id == ClassroomMessage.sender_id)
                .order_by(ClassroomMessage.created_at.desc())
            )
            .all()
        )
        messages = []
        for msg, user in rows[:100]:
            messages.append(
                {
                    "id": msg.id,
                    "sender_id": msg.sender_id,
                    "username": getattr(user, "username", None),
                    "full_name": getattr(user, "full_name", None),
                    "avatar": getattr(user, "avatar", None),
                    "site_role": getattr(user, "site_role", None),
                    "content": msg.content,
                    "created_at": msg.created_at.isoformat(),
                }
            )
        # analytics: record a chat view / last-seen marker for this classroom
        try:
            sa = StudentAnalytics(
                user_id=current_user.id,
                classroom_id=classroom_id,
                event_type="chat_view",
                details=f"messages={len(messages)}",
            )
            session.add(sa)
            session.commit()
        except Exception:
            session.rollback()
    messages.reverse()
    return JSONResponse({"messages": messages})


@app.get('/api/spaces/{space_id}/chat/messages')
def space_chat_messages(space_id: int, current_user: User = Depends(get_current_user)):
    """Return recent space chat messages with sender metadata.

    Only members (student/teacher/admin) of the space can access.
    """
    with Session(engine) as session:
        space = session.get(Space, space_id)
        if not space:
            raise HTTPException(status_code=404, detail="Space not found")
        mem = session.exec(
            select(Membership).where(
                (Membership.user_id == current_user.id)
                & (
                    (Membership.space_id == space_id)
                    | (Membership.classroom_id == space_id)
                )
            )
        ).first()
        if not mem:
            raise HTTPException(status_code=403, detail="Not a member of this space")

        rows = (
            session.exec(
                select(SpaceMessage, User)
                .where(SpaceMessage.space_id == space_id)
                .join(User, User.id == SpaceMessage.sender_id)
                .order_by(SpaceMessage.created_at.desc())
            )
            .all()
        )
        messages = []
        for msg, user in rows[:100]:
            messages.append(
                {
                    "id": msg.id,
                    "sender_id": msg.sender_id,
                    "username": getattr(user, "username", None),
                    "full_name": getattr(user, "full_name", None),
                    "avatar": getattr(user, "avatar", None),
                    "site_role": getattr(user, "site_role", None),
                    "content": msg.content,
                    "created_at": msg.created_at.isoformat(),
                }
            )

        # analytics: record a chat view / last-seen marker for this space
        try:
            sa = StudentAnalytics(
                user_id=current_user.id,
                space_id=space_id,
                classroom_id=space_id,
                event_type="chat_view",
                details=f"messages={len(messages)}",
            )
            session.add(sa)
            session.commit()
        except Exception:
            session.rollback()
    messages.reverse()
    return JSONResponse({"messages": messages})


@app.post('/api/classrooms/{classroom_id}/chat/messages')
def classroom_chat_post(
    classroom_id: int,
    payload: dict = Body(...),
    current_user: User = Depends(get_current_user),
):
    """Post a new classroom chat message for all members to see."""
    content = (payload.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Empty message")

    with Session(engine) as session:
        classroom = session.get(Classroom, classroom_id)
        if not classroom:
            raise HTTPException(status_code=404, detail="Classroom not found")
        mem = session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.user_id == current_user.id)
            )
        ).first()
        if not mem:
            raise HTTPException(status_code=403, detail="Not a member of this classroom")

        msg = ClassroomMessage(
            classroom_id=classroom_id,
            sender_id=current_user.id,
            content=content,
        )
        session.add(msg)
        session.commit()
        session.refresh(msg)

        out = {
            "id": msg.id,
            "sender_id": msg.sender_id,
            "username": current_user.username,
            "full_name": current_user.full_name,
            "avatar": current_user.avatar,
            "site_role": current_user.site_role,
            "content": msg.content,
            "created_at": msg.created_at.isoformat(),
        }

        # analytics: classroom chat message sent
        try:
            sa = StudentAnalytics(
                user_id=current_user.id,
                classroom_id=classroom_id,
                event_type="chat_message",
                details=f"message={msg.id}",
            )
            session.add(sa)
            session.commit()
        except Exception:
            session.rollback()

    return JSONResponse(out)


@app.post('/api/spaces/{space_id}/chat/messages')
def space_chat_post(
    space_id: int,
    payload: dict = Body(...),
    current_user: User = Depends(get_current_user),
):
    """Post a new space chat message for all members to see."""
    content = (payload.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Empty message")

    with Session(engine) as session:
        space = session.get(Space, space_id)
        if not space:
            raise HTTPException(status_code=404, detail="Space not found")
        mem = session.exec(
            select(Membership).where(
                (Membership.user_id == current_user.id)
                & (
                    (Membership.space_id == space_id)
                    | (Membership.classroom_id == space_id)
                )
            )
        ).first()
        if not mem:
            raise HTTPException(status_code=403, detail="Not a member of this space")

        msg = SpaceMessage(
            space_id=space_id,
            sender_id=current_user.id,
            content=content,
        )
        session.add(msg)
        session.commit()
        session.refresh(msg)

        out = {
            "id": msg.id,
            "sender_id": msg.sender_id,
            "username": current_user.username,
            "full_name": current_user.full_name,
            "avatar": current_user.avatar,
            "site_role": current_user.site_role,
            "content": msg.content,
            "created_at": msg.created_at.isoformat(),
        }

        # analytics: space chat message sent
        try:
            sa = StudentAnalytics(
                user_id=current_user.id,
                space_id=space_id,
                classroom_id=space_id,
                event_type="chat_message",
                details=f"message={msg.id}",
            )
            session.add(sa)
            session.commit()
        except Exception:
            session.rollback()

    return JSONResponse(out)


@app.post('/api/classrooms/{classroom_id}/chat/seen')
def classroom_chat_seen(classroom_id: int, current_user: User = Depends(get_current_user)):
    """Mark classroom chat as seen for the current user.

    This is used for per-user last-seen tracking and analytics.
    """
    with Session(engine) as session:
        classroom = session.get(Classroom, classroom_id)
        if not classroom:
            raise HTTPException(status_code=404, detail="Classroom not found")
        mem = session.exec(
            select(Membership).where(
                (Membership.classroom_id == classroom_id)
                & (Membership.user_id == current_user.id)
            )
        ).first()
        if not mem:
            raise HTTPException(status_code=403, detail="Not a member of this classroom")

        try:
            sa = StudentAnalytics(
                user_id=current_user.id,
                classroom_id=classroom_id,
                event_type="chat_seen",
                details=None,
            )
            session.add(sa)
            session.commit()
        except Exception:
            session.rollback()

    return JSONResponse({"ok": True})


@app.post('/api/spaces/{space_id}/chat/seen')
def space_chat_seen(space_id: int, current_user: User = Depends(get_current_user)):
    """Mark space chat as seen for the current user.

    This is used for per-user last-seen tracking and analytics.
    """
    with Session(engine) as session:
        space = session.get(Space, space_id)
        if not space:
            raise HTTPException(status_code=404, detail="Space not found")
        mem = session.exec(
            select(Membership).where(
                (Membership.user_id == current_user.id)
                & (
                    (Membership.space_id == space_id)
                    | (Membership.classroom_id == space_id)
                )
            )
        ).first()
        if not mem:
            raise HTTPException(status_code=403, detail="Not a member of this space")

        try:
            sa = StudentAnalytics(
                user_id=current_user.id,
                space_id=space_id,
                classroom_id=space_id,
                event_type="chat_seen",
                details=None,
            )
            session.add(sa)
            session.commit()
        except Exception:
            session.rollback()

    return JSONResponse({"ok": True})


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


## NOTE: POST /api/messages/{other_id} is handled earlier by api_post_message,
## which supports both JSON and multipart form data (for file attachments) and
## sends real-time WebSocket notifications. This legacy JSON-only handler has
## been removed to avoid path conflicts.


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
    # Require sign-in to search and browse categories
    current_user = get_current_user_optional(request)
    if not current_user:
        next_url = str(request.url.path)
        if q or category:
            # preserve query parameters in next when searching from header/category chips
            next_url = str(request.url)
        return RedirectResponse(url=f"/login?next={next_url}", status_code=status.HTTP_303_SEE_OTHER)
 
    with Session(engine) as session:
        # Base statement: search title/description by query and eager-load owner
        statement = (
            select(Presentation)
            .options(selectinload(Presentation.owner), selectinload(Presentation.category))
            .where((Presentation.title.contains(q)) | (Presentation.description.contains(q)))
        )

        # If a category name is supplied, filter by presentations whose related
        # Category.name matches (case-insensitive) OR whose title contains the
        # category label. This works even when the Category row casing differs
        # from the chip label (e.g. "marketing" vs "Marketing").
        if category:
            cat_name = (category or "").strip()
            if cat_name:
                lowered = cat_name.lower()
                statement = statement.where(
                    Presentation.category.has(func.lower(Category.name) == lowered)
                    | func.lower(Presentation.title).contains(lowered)
                )

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

        # attach like counts for results (batch)
        if res_ids:
            rows_likes = session.exec(
                select(Like.presentation_id, func.count(Like.id))
                .where(Like.presentation_id.in_(list(res_ids)))
                .group_by(Like.presentation_id)
            ).all()
            lc = {int(r[0]): int(r[1]) for r in rows_likes}
        else:
            lc = {}
        for r in results:
            setattr(r, 'likes_count', lc.get(getattr(r, 'id', None), 0))

        # attach owner's presentation counts for search results
        res_owner_ids = {r.owner_id for r in results if getattr(r, 'owner_id', None) is not None}
        if res_owner_ids:
            rows_oc = session.exec(
                select(Presentation.owner_id, func.count(Presentation.id)).where(Presentation.owner_id.in_(list(res_owner_ids))).group_by(Presentation.owner_id)
            ).all()
            res_owner_counts = {int(r[0]): int(r[1]) for r in rows_oc}
        else:
            res_owner_counts = {}
        for r in results:
            setattr(r, 'owner_presentation_count', res_owner_counts.get(getattr(r, 'owner_id', None), 0))

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
                # notify presentation owner that their presentation was saved
                try:
                    if pres.owner_id and pres.owner_id != current_user.id:
                        n = Notification(recipient_id=pres.owner_id, actor_id=current_user.id, verb='save', target_type='presentation', target_id=presentation_id)
                        session.add(n)
                        session.commit()
                except Exception:
                    session.rollback()
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


@app.get("/messages", response_class=HTMLResponse, name="messages_page")
def messages_page(request: Request, current_user: User = Depends(get_current_user)):
    """Inbox-style view of direct messages grouped by conversation."""
    with Session(engine) as session:
        # fetch recent messages involving current user
        rows = session.exec(
            select(Message)
            .where((Message.sender_id == current_user.id) | (Message.recipient_id == current_user.id))
            .order_by(Message.created_at.desc())
        ).all()

        conversations: dict[int, Message] = {}
        for m in rows:
            other_id = m.recipient_id if m.sender_id == current_user.id else m.sender_id
            if other_id is None:
                continue
            # keep latest message per other user
            if other_id not in conversations:
                conversations[other_id] = m

        other_ids = list(conversations.keys())
        users_map: dict[int, User] = {}
        if other_ids:
            u_rows = session.exec(select(User).where(User.id.in_(other_ids))).all()
            users_map = {u.id: u for u in u_rows}

        # unread counts per sender
        unread_rows = session.exec(
            select(Message.sender_id, func.count(Message.id))
            .where((Message.recipient_id == current_user.id) & (Message.read == False))
            .group_by(Message.sender_id)
        ).all()
        unread_map = {int(r[0]): int(r[1]) for r in unread_rows}

        # follow relationships
        follow_rows = session.exec(
            select(Follow.follower_id, Follow.following_id)
            .where((Follow.follower_id == current_user.id) | (Follow.following_id == current_user.id))
        ).all()
        i_follow = {int(f[1]) for f in follow_rows if int(f[0]) == current_user.id}
        follows_me = {int(f[0]) for f in follow_rows if int(f[1]) == current_user.id}

        inbox_convos = []
        request_convos = []
        for other_id, last_msg in conversations.items():
            u = users_map.get(other_id)
            # build base conversation object for threads that have messages
            ns = SimpleNamespace(
                other_id=other_id,
                other_username=getattr(u, "username", None) if u else None,
                last_content=last_msg.content,
                last_created_at=last_msg.created_at,
                unread=unread_map.get(other_id, 0),
                i_follow=other_id in i_follow,
                follows_me=other_id in follows_me,
            )
            # Inbox should show all conversations with at least one message
            inbox_convos.append(ns)
            # Requests: messages from people I don't follow (one-way into me)
            is_request = (other_id not in i_follow) and (last_msg.recipient_id == current_user.id)
            if is_request:
                request_convos.append(ns)

        inbox_convos.sort(key=lambda c: c.last_created_at, reverse=True)
        request_convos.sort(key=lambda c: c.last_created_at, reverse=True)

        # For the UI, "Mutuals" should behave like "People I follow" so that
        # anyone you follow appears here, even if you haven't exchanged
        # messages yet. Start with followed users that already have threads.
        mutual_convos = [c for c in inbox_convos if c.i_follow]
        existing_ids = {c.other_id for c in mutual_convos}
        missing_follow_ids = [uid for uid in i_follow if uid not in existing_ids]
        if missing_follow_ids:
            extra_users = session.exec(select(User).where(User.id.in_(missing_follow_ids))).all()
            for u in extra_users:
                mutual_convos.append(
                    SimpleNamespace(
                        other_id=u.id,
                        other_username=u.username,
                        last_content="",
                        last_created_at=None,
                        unread=0,
                        i_follow=True,
                        follows_me=u.id in follows_me,
                    )
                )

        # sort mutuals by username so people you follow are easy to scan
        mutual_convos.sort(key=lambda c: c.other_username or "")
    return templates.TemplateResponse(
        "messages.html",
        {
            "request": request,
            "conversations": inbox_convos,
            "mutual_conversations": mutual_convos,
            "request_conversations": request_convos,
        },
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
        init_res = paystack_initialize_transaction(
            email=email,
            amount_kobo=PAYSTACK_AMOUNT_KOBO,
            callback_url=callback_url,
            metadata=metadata,
        )
        if asyncio.iscoroutine(init_res):
            init_res = await init_res
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
        verify_res = paystack_verify_transaction(reference)
        if asyncio.iscoroutine(verify_res):
            verify_res = await verify_res
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
            elif action in (None, "", "premium_subscribe"):
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
    elif action == "coffee_donation":
        redirect_target = "/?coffee=success" if status_val == "success" else "/?coffee=failed"
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
            # create notification for the followed user
            try:
                if user_id != current_user.id:
                    n = Notification(recipient_id=user_id, actor_id=current_user.id, verb='follow', target_type='user', target_id=current_user.id)
                    session.add(n)
                    session.commit()
            except Exception:
                session.rollback()
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
        init_res = paystack_initialize_transaction(email=email, amount_kobo=PAYSTACK_AMOUNT_KOBO, callback_url=callback_url, metadata=metadata)
        if asyncio.iscoroutine(init_res):
            init_res = await init_res
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


@app.get("/support/coffee")
async def support_coffee(request: Request):
    if not PAYSTACK_PUBLIC_KEY:
        return RedirectResponse(url="/?coffee_error=Payment+is+not+configured", status_code=status.HTTP_302_FOUND)

    current_user = get_current_user_optional(request)
    user_id = getattr(current_user, "id", None) if current_user else None
    username = getattr(current_user, "username", None) if current_user else None
    user_email = getattr(current_user, "email", None) if current_user else None
    email = user_email or f"guest_{uuid.uuid4().hex[:10]}@247fileshare.app"
    callback_url = str(request.url_for("paystack_callback"))
    metadata = {"action": "coffee_donation"}
    if user_id:
        metadata["user_id"] = user_id
    if username:
        metadata["username"] = username

    try:
        init_res = paystack_initialize_transaction(
            email=email,
            amount_kobo=COFFEE_AMOUNT_KOBO,
            callback_url=callback_url,
            metadata=metadata,
        )
        if asyncio.iscoroutine(init_res):
            init_res = await init_res
    except Exception:
        return RedirectResponse(url="/?coffee_error=Unable+to+start+payment", status_code=status.HTTP_302_FOUND)

    data = init_res.get("data", {}) if isinstance(init_res, dict) else {}
    auth_url = data.get("authorization_url")
    reference = data.get("reference")
    if not auth_url or not reference:
        return RedirectResponse(url="/?coffee_error=Invalid+payment+response", status_code=status.HTTP_302_FOUND)

    with Session(engine) as session:
        tx = Transaction(
            order_id=reference,
            payer_id=email,
            amount=str(COFFEE_AMOUNT_KOBO / 100),
            currency=PAYSTACK_CURRENCY,
            status="initialized",
            user_id=user_id,
        )
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
