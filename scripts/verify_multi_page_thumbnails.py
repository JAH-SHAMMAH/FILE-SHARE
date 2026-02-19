import time
from pathlib import Path

import fitz
import httpx

BASE = "http://127.0.0.1:8000"


def get_token(client: httpx.Client) -> str:
    username = "thumbcheck"
    email = "thumbcheck@example.test"
    password = "pass123"

    r = client.post(
        f"{BASE}/api/register",
        json={"username": username, "email": email, "password": password},
    )
    if r.status_code == 200:
        return r.json()["access_token"]

    r = client.post(
        f"{BASE}/api/login", json={"username": username, "password": password}
    )
    r.raise_for_status()
    return r.json()["access_token"]


def make_pdf(path: Path, pages: int = 3) -> None:
    doc = fitz.open()
    for idx in range(pages):
        page = doc.new_page(width=1280, height=720)
        page.insert_text(
            (72, 100),
            f"Sample Presentation Page {idx + 1}",
            fontsize=36,
        )
        page.insert_text((72, 170), "Thumbnail generation verification", fontsize=20)
    doc.save(path)
    doc.close()


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    pdf_path = script_dir / "multi_page_test.pdf"
    make_pdf(pdf_path, pages=3)

    with httpx.Client(timeout=30.0) as client:
        token = get_token(client)
        headers = {"Authorization": f"Bearer {token}"}

        with pdf_path.open("rb") as f:
            files = {"file": (pdf_path.name, f, "application/pdf")}
            data = {
                "title": "Multi Page Thumbnail Test",
                "description": "verify thumbs",
                "tags": "test",
                "category": "test",
            }
            up = client.post(f"{BASE}/api/uploads", headers=headers, files=files, data=data)

        print("upload status:", up.status_code)
        up.raise_for_status()
        payload = up.json()
        presentation_id = payload.get("id")
        print("presentation id:", presentation_id)

        thumbs = []
        for attempt in range(1, 16):
            resp = client.get(f"{BASE}/presentations/{presentation_id}/thumbnails")
            if resp.status_code == 200:
                info = resp.json()
                thumbs = info.get("thumbnails") or []
                print(
                    f"attempt {attempt}: status={info.get('status', 'ready')} thumbs={len(thumbs)}"
                )
                if len(thumbs) >= 2:
                    break
            else:
                print(f"attempt {attempt}: thumbnails endpoint status={resp.status_code}")
            time.sleep(2)

        if thumbs:
            print("sample thumbnail urls:", thumbs[:3])
        print("final thumbnail count:", len(thumbs))


if __name__ == "__main__":
    main()
