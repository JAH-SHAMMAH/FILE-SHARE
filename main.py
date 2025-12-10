"""App entrypoint for hosts expecting `main:app` at project root."""
import os

from app.main import app

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=False)
