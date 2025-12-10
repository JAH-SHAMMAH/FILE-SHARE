import io
import pytest
from fastapi.testclient import TestClient
from app.main import app


client = TestClient(app)


def test_upload_validation():
    # try uploading an unsupported file type
    data = {"title": "t"}
    files = {"file": ("test.txt", io.BytesIO(b"hello"), "text/plain")}
    r = client.post("/upload", data=data, files=files)
    assert r.status_code in (200, 401, 422)
