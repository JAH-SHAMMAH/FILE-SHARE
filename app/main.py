import os
import shutil
import logging
import traceback
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
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from dotenv import load_dotenv
from .database import engine, create_db_and_tables, get_session
from .models import (
    User,
    Presentation,
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
from jose import jwt
from .payments import create_order, capture_order, get_access_token
from .payments import verify_webhook_signature
from .oauth import oauth
from .tasks import enqueue_conversion
from .models import ConversionJob
import uuid
from pathlib import Path
from typing import List
import textwrap

load_dotenv()

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI()
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent.parent / "static")),
    name="static",
)
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
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
    request.state.current_user = get_current_user_optional(request)
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
    with Session(engine) as session:
        if q:
            statement = (
                select(Presentation)
                .where(
                    Presentation.title.contains(q)
                    | Presentation.description.contains(q)
                )
                .order_by(Presentation.created_at.desc())
            )
        else:
            statement = select(Presentation).order_by(Presentation.created_at.desc())
        presentations = session.exec(statement).all()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "presentations": presentations,
            "current_user": current_user,
        },
    )


@app.get("/register", response_class=HTMLResponse, name="register")
def register_get(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


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
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    with Session(engine) as session:
        user = authenticate_user(username, password, session)
        if not user:
            return templates.TemplateResponse(
                "login.html", {"request": request, "error": "Invalid credentials"}
            )
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
def contact_post(request: Request, name: str = Form(...), email: str = Form(...), message: str = Form(...)):
    # In this prototype we simply acknowledge receipt. Production should send email or persist.
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
def upload_get(request: Request, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        categories = session.exec(select(Category)).all()
    return templates.TemplateResponse(
        "upload.html",
        {"request": request, "categories": categories, "current_user": current_user},
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
            # handle category
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
    with Session(engine) as session:
        p = session.get(Presentation, presentation_id)
        if not p:
            raise HTTPException(status_code=404, detail="Not found")
        p.views += 1
        session.add(p)
        session.commit()
        comments = session.exec(
            select(Comment).where(Comment.presentation_id == presentation_id)
        ).all()
        likes = session.exec(
            select(Like).where(Like.presentation_id == presentation_id)
        ).all()

        # Decide what to show inline: converted PDF (if exists) or original PDF; other types auto-enqueue conversion and remain download-only until ready.
        viewer_url = None
        conversion_status = None
        if p.filename:
            ext = Path(p.filename).suffix.lower()
            original_url = f"/download/{p.filename}?inline=1"
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
    return templates.TemplateResponse(
        "presentation.html",
        {
            "request": request,
            "p": p,
            "comments": comments,
            "likes": len(likes),
            "viewer_url": viewer_url,
            "conversion_status": conversion_status,
            "original_url": original_url,
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
            original_url = f"/download/{p.filename}?inline=1"
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

    media_type = "application/octet-stream"
    ext = path.suffix.lower()
    if ext == ".pdf":
        media_type = "application/pdf"

    # For inline view, omit filename so Starlette does not force attachment; set explicit inline header.
    if inline:
        return FileResponse(
            path,
            media_type=media_type,
            headers={"Content-Disposition": f"inline; filename=\"{filename}\""},
        )

    return FileResponse(path, media_type=media_type, filename=filename)


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
    request: Request, username: str, current_user: User = Depends(get_current_user)
):
    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == username)).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        presentations = session.exec(
            select(Presentation)
            .where(Presentation.owner_id == user.id)
            .order_by(Presentation.created_at.desc())
        ).all()
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


@app.get("/me/edit", response_class=HTMLResponse)
def edit_profile_get(request: Request, current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse(
        "edit_profile.html", {"request": request, "user_obj": current_user}
    )


@app.post("/me/edit")
async def edit_profile_post(
    request: Request,
    full_name: str = Form(None),
    bio: str = Form(None),
    avatar: UploadFile = File(None),
    current_user: User = Depends(get_current_user),
):
    if avatar:
        av_ext = Path(avatar.filename).suffix.lower()
        if av_ext not in {".png", ".jpg", ".jpeg", ".gif"}:
            return templates.TemplateResponse(
                "edit_profile.html",
                {
                    "request": request,
                    "error": "Unsupported avatar type",
                    "user_obj": current_user,
                },
            )
        avatar_name = f"avatar_{current_user.id}_{uuid.uuid4().hex}{av_ext}"
        save_path = Path(UPLOAD_DIR) / avatar_name
        with save_path.open("wb") as buffer:
            shutil.copyfileobj(avatar.file, buffer)
        with Session(engine) as session:
            u = session.get(User, current_user.id)
            u.avatar = avatar_name
            if full_name is not None:
                u.full_name = full_name
            if bio is not None:
                u.bio = bio
            session.add(u)
            session.commit()
    else:
        with Session(engine) as session:
            u = session.get(User, current_user.id)
            if full_name is not None:
                u.full_name = full_name
            if bio is not None:
                u.bio = bio
            session.add(u)
            session.commit()
    return RedirectResponse(
        url=f"/users/{current_user.username}", status_code=status.HTTP_302_FOUND
    )


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
        presentations = session.exec(
            select(Presentation).where(Presentation.owner_id == current_user.id)
        ).all()
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
def search(request: Request, q: str = ""):
    with Session(engine) as session:
        statement = select(Presentation).where(
            Presentation.title.contains(q) | Presentation.description.contains(q)
        )
        results = session.exec(statement).all()
    return templates.TemplateResponse(
        "search.html", {"request": request, "q": q, "results": results}
    )


@app.get("/activity", response_class=HTMLResponse)
def activity_feed(request: Request, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        acts = session.exec(select(Activity).order_by(Activity.created_at.desc())).all()
        # Eager load user and presentation title where possible
        enriched = []
        for a in acts:
            user = session.get(User, a.user_id) if a.user_id else None
            pres = session.get(Presentation, a.target_id) if a.target_id else None
            enriched.append({"activity": a, "user": user, "presentation": pres})
    return templates.TemplateResponse(
        "activity.html", {"request": request, "items": enriched}
    )


@app.get("/premium/subscribe")
async def premium_subscribe(current_user: User = Depends(get_current_user)):
    # For demo: create a PayPal order for $5.00
    order = await create_order("5.00")
    # Find approval link
    for link in order.get("links", []):
        if link.get("rel") == "approve":
            return RedirectResponse(url=link.get("href"))
    raise HTTPException(status_code=400, detail="No approval link")


@app.get("/auth/{provider}")
async def oauth_login(request: Request, provider: str):
    client = oauth.create_client(provider)
    if not client:
        raise HTTPException(status_code=404, detail="Unknown provider")
    redirect_uri = request.url_for("oauth_callback", provider=provider)
    return await client.authorize_redirect(request, str(redirect_uri))


@app.get("/auth/{provider}/callback")
async def oauth_callback(request: Request, provider: str):
    client = oauth.create_client(provider)
    if not client:
        raise HTTPException(status_code=404, detail="Unknown provider")
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

    if not profile:
        raise HTTPException(status_code=400, detail="Failed to fetch user profile")

    email = profile.get("email") or profile.get("login")
    username = profile.get("name") or profile.get("login") or email.split("@")[0]

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
    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
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
