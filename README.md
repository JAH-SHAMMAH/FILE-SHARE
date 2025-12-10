# SLIDESHARE (Prototype)

This is a prototype SlideShare-like web application built with FastAPI and Jinja2 templates. It includes user registration/login (JWT), profile pages, presentation uploads, likes, comments, follow/unfollow, search, categories/tags, and a PayPal sandbox integration for premium membership.

This is a development scaffold — adapt secrets and production settings before deploying.

Getting started

1. Create a Python virtualenv and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Copy `.env.example` to `.env` and fill in secrets (JWT secret, PayPal sandbox keys).

3. Run the app:

```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

4. Open `http://127.0.0.1:8000`

Notes

- Uses SQLite for quick local development (`db.sqlite` in project root).
- PayPal integration uses sandbox APIs; set `PAYPAL_CLIENT_ID` and `PAYPAL_SECRET`.
- This is a prototype and not production hardened.

Dependencies and optional tools

- To enable PPTX -> PDF conversion and thumbnail generation set `ENABLE_CONVERSION=true` and install LibreOffice (`soffice`) and ImageMagick (`convert`) on your system PATH.
- For OAuth sign-in, create OAuth apps for Google/GitHub and set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GITHUB_CLIENT_ID`, and `GITHUB_CLIENT_SECRET` in your `.env`.
- For webhook verification, set `PAYPAL_WEBHOOK_ID` in `.env` (from your PayPal sandbox app webhook settings).
