import os
import subprocess
from redis import Redis
from rq import Queue
from pathlib import Path
from .database import engine
from sqlmodel import Session, select
from .models import ConversionJob, Presentation

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
redis = Redis.from_url(redis_url)
q = Queue(connection=redis)


def convert_presentation(presentation_id: int, filename: str):
    """Worker function to convert presentation to PDF and generate thumbnail."""
    save_dir = Path(os.getenv("UPLOAD_DIR", "./uploads"))
    src = save_dir / filename
    job_record = None
    with Session(engine) as session:
        job_record = session.exec(
            select(ConversionJob).where(
                ConversionJob.presentation_id == presentation_id
            )
        ).first()
        if not job_record:
            job_record = ConversionJob(
                presentation_id=presentation_id, status="started"
            )
            session.add(job_record)
            session.commit()
            session.refresh(job_record)
    try:
        # convert to pdf using LibreOffice
        job_log = []
        job_log.append("starting conversion")
        subprocess.run(
            [
                "soffice",
                "--headless",
                "--convert-to",
                "pdf",
                str(src),
                "--outdir",
                str(save_dir),
            ],
            check=True,
        )
        pdf_path = src.with_suffix(".pdf")
        job_log.append(f"converted to PDF: {pdf_path.name}")
        # generate per-page thumbnails directory
        thumbs_dir = save_dir / "thumbs" / str(presentation_id)
        thumbs_dir.mkdir(parents=True, exist_ok=True)
        # Use ImageMagick `convert` to export each page as a PNG
        # Output pattern: slide_0.png, slide_1.png, ...
        try:
            # attempt to export all pages
            out_pattern = str(thumbs_dir / "slide_%d.png")
            subprocess.run(
                [
                    "convert",
                    str(pdf_path),
                    "-thumbnail",
                    "x800",
                    out_pattern,
                ],
                check=True,
            )
            job_log.append("generated per-page thumbnails")
        except Exception as e:
            # fallback: generate only first page thumbnail
            job_log.append(f"per-page thumbnails failed: {e}")
            try:
                thumb_path = save_dir / f"thumb_{presentation_id}.png"
                subprocess.run(
                    [
                        "convert",
                        str(pdf_path) + "[0]",
                        "-thumbnail",
                        "300x300",
                        str(thumb_path),
                    ],
                    check=True,
                )
                job_log.append("generated single thumbnail")
            except Exception:
                job_log.append("thumbnail generation failed")
        with Session(engine) as session:
            job_record = session.get(ConversionJob, job_record.id)
            job_record.status = "finished"
            job_record.result = str(pdf_path.name)
            job_record.log = "\n".join(job_log)
            session.add(job_record)
            session.commit()
    except Exception as e:
        # record failure and log
        with Session(engine) as session:
            job_record = session.get(ConversionJob, job_record.id)
            job_record.status = "failed"
            prev_log = job_record.log or ""
            job_record.result = str(e)
            job_record.log = prev_log + "\n" + str(e)
            session.add(job_record)
            session.commit()


def enqueue_conversion(presentation_id: int, filename: str):
    job = q.enqueue(convert_presentation, presentation_id, filename)
    with Session(engine) as session:
        cj = ConversionJob(
            presentation_id=presentation_id, job_id=job.get_id(), status="queued"
        )
        session.add(cj)
        session.commit()
        session.refresh(cj)
    return job.get_id()
