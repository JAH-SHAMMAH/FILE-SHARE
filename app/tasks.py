
import os
import subprocess
try:
    from redis import Redis
    from rq import Queue
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    redis = Redis.from_url(redis_url)
    q = Queue(connection=redis)
except ImportError:
    Redis = None
    Queue = None
    redis = None
    q = None
from pathlib import Path
from .database import engine
from sqlmodel import Session, select
from .models import ConversionJob, Presentation
from .models import AIResult
import httpx
from .convert import (
    convert_doc_to_pdf,
    generate_pdf_thumbnails,
    generate_video_thumbnail,
    generate_audio_waveform,
    render_code_syntax,
)


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
        job_log = []
        job_log.append("starting conversion")
        ext = src.suffix.lower() if src.suffix else ''
        pdf_path = None
        thumbs_dir = save_dir / "thumbs" / str(presentation_id)
        thumbs_dir.mkdir(parents=True, exist_ok=True)

        # document conversions
        if ext in ('.doc', '.docx', '.odt', '.ppt', '.pptx'):
            job_log.append('converting document to PDF')
            pdf_path = convert_doc_to_pdf(str(src), str(save_dir))
            if pdf_path:
                job_log.append(f"converted to PDF: {Path(pdf_path).name}")
                thumbs = generate_pdf_thumbnails(pdf_path, str(thumbs_dir))
                if thumbs:
                    job_log.append(f"generated {len(thumbs)} thumbnails")
        # existing PDFs
        elif ext == '.pdf':
            pdf_path = str(src)
            job_log.append('source is PDF')
            thumbs = generate_pdf_thumbnails(pdf_path, str(thumbs_dir))
            if thumbs:
                job_log.append(f"generated {len(thumbs)} thumbnails")

        # video
        elif ext in ('.mp4', '.mov', '.m4v', '.webm'):
            job_log.append('generating video thumbnail')
            thumb_out = thumbs_dir / 'video_preview.png'
            res = generate_video_thumbnail(str(src), str(thumb_out))
            if res:
                job_log.append('video thumbnail generated')

        # audio
        elif ext in ('.mp3', '.wav', '.m4a', '.ogg'):
            job_log.append('generating audio waveform')
            wave_out = thumbs_dir / 'waveform.png'
            res = generate_audio_waveform(str(src), str(wave_out))
            if res:
                job_log.append('audio waveform generated')

        # code / text files
        elif ext in ('.py', '.js', '.java', '.c', '.cpp', '.txt', '.md'):
            job_log.append('rendering code/text preview')
            out_html = thumbs_dir / 'code_preview.html'
            res = render_code_syntax(str(src), str(out_html))
            if res:
                job_log.append('code preview generated')

        # finalize job record
        with Session(engine) as session:
            job_record = session.get(ConversionJob, job_record.id)
            job_record.status = "finished"
            job_record.result = str(Path(pdf_path).name) if pdf_path else (Path(src).name)
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


def ai_summarize_presentation(presentation_id: int):
    """Worker: fetch presentation text (if available) and call OpenAI to summarize."""
    OPENAI_KEY = os.getenv("OPENAI_API_KEY")
    # prepare prompt_text from description + first pages of PDF if available
    with Session(engine) as session:
        pres = session.get(Presentation, presentation_id)
        if not pres:
            return
        # start with a short header including the title so the summary can reference it
        title = (getattr(pres, "title", "") or "").strip()
        desc = (pres.description or "").strip()
        header = f"Title: {title}\n" if title else ""
        prompt_text = header + (desc or "")
        # if there's a local PDF, attempt to read first pages
        from pathlib import Path
        UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")
        if pres.filename and pres.filename.endswith(".pdf"):
            ppath = Path(UPLOAD_DIR) / pres.filename
            try:
                import fitz
                doc = fitz.open(str(ppath))
                txt = "\n".join([doc[i].get_text() for i in range(min(len(doc), 5))])
                prompt_text += "\n" + txt
            except Exception:
                pass

    if not OPENAI_KEY:
        # Fallback: simple extractive summarizer when OpenAI key missing
        try:
            # naive sentence splitter: split on punctuation
            import re
            sentences = re.split(r'(?<=[\.\!\?])\s+', (prompt_text or '').strip())
            # pick first few informative sentences up to ~120 words
            summary_lines = []
            word_count = 0
            for s in sentences:
                if not s.strip():
                    continue
                summary_lines.append(s.strip())
                word_count += len(s.split())
                if word_count >= 120 or len(summary_lines) >= 4:
                    break
            resp_text = "\n\n".join(summary_lines) if summary_lines else (prompt_text[:800] or "No text available to summarize.")
        except Exception:
            resp_text = "No OpenAI key configured and local summarizer failed"
        with Session(engine) as session:
            ar = AIResult(presentation_id=presentation_id, task_type="summary", result=resp_text)
            session.add(ar)
            session.commit()
            session.refresh(ar)
        return

    # naive approach: try to find a text-extract in Presentation.description or a linked PDF
    with Session(engine) as session:
        pres = session.get(Presentation, presentation_id)
        if not pres:
            return
        title = (getattr(pres, "title", "") or "").strip()
        desc = (pres.description or "").strip()
        header = f"Title: {title}\n" if title else ""
        prompt_text = header + (desc or "")
        # if there's a local PDF, attempt to read first 10000 chars (best-effort)
        from pathlib import Path
        UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")
        if pres.filename and pres.filename.endswith(".pdf"):
            ppath = Path(UPLOAD_DIR) / pres.filename
            try:
                import fitz
                doc = fitz.open(str(ppath))
                txt = "\n".join([doc[i].get_text() for i in range(min(len(doc), 5))])
                prompt_text += "\n" + txt
            except Exception:
                pass

    # call OpenAI (ChatCompletion) with the prompt_text
    try:
        headers = {"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"}
        data = {
            "model": "gpt-4o-mini",
            "messages": [{
                "role": "user",
                "content": (
                    "You are helping a busy student decide whether to read a presentation. "
                    "Given the title and extracted text below, write a concise, friendly overview that:\n"
                    "- Describes what the presentation is about in plain language,\n"
                    "- Highlights the main topics or sections,\n"
                    "- Mentions who it is most useful for (e.g., students, teachers, beginners, advanced),\n"
                    "- Stays under about 6 sentences.\n\n"
                    "Presentation info:\n" + prompt_text
                ),
            }],
            "max_tokens": 800,
        }
        resp_text = ""
        with httpx.Client(timeout=30) as client:
            r = client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=data)
            if r.status_code == 200:
                j = r.json()
                resp_text = j.get("choices", [{}])[0].get("message", {}).get("content", "")
            else:
                resp_text = f"OpenAI error: {r.status_code}"
    except Exception as e:
        resp_text = str(e)

    with Session(engine) as session:
        ar = AIResult(presentation_id=presentation_id, task_type="summary", result=resp_text)
        session.add(ar)
        session.commit()
        session.refresh(ar)


def enqueue_ai_summary(presentation_id: int):
    job = q.enqueue(ai_summarize_presentation, presentation_id)
    return job.get_id()


def ai_generate_quiz(presentation_id: int):
    """Generate quiz questions from presentation text using OpenAI or fallback simple heuristics."""
    OPENAI_KEY = os.getenv("OPENAI_API_KEY")
    with Session(engine) as session:
        pres = session.get(Presentation, presentation_id)
        if not pres:
            return
        title = (getattr(pres, "title", "") or "").strip()
        desc = (pres.description or "").strip()
        header = f"Title: {title}\n" if title else ""
        prompt_text = header + (desc or "")
        from pathlib import Path
        UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")
        if pres.filename and pres.filename.endswith(".pdf"):
            try:
                import fitz
                ppath = Path(UPLOAD_DIR) / pres.filename
                doc = fitz.open(str(ppath))
                txt = "\n".join([doc[i].get_text() for i in range(min(len(doc), 5))])
                prompt_text += "\n" + txt
            except Exception:
                pass

    resp_text = ""
    if OPENAI_KEY:
        try:
            headers = {"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"}
            data = {
                "model": "gpt-4o-mini",
                "messages": [{
                    "role": "user",
                    "content": (
                        "You are creating a quick self-check quiz for a presentation. "
                        "Using the title and text below, write 5 numbered multiple-choice questions. For each question, include:\n"
                        "- The question on its own line,\n"
                        "- 4 answer options labeled A), B), C), D),\n"
                        "- A final line starting with 'Answer:' and the correct option letter.\n\n"
                        "Keep the language simple and student-friendly.\n\n"
                        "Presentation info:\n" + prompt_text
                    ),
                }],
                "max_tokens": 800,
            }
            with httpx.Client(timeout=30) as client:
                r = client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=data)
                if r.status_code == 200:
                    j = r.json()
                    resp_text = j.get("choices", [{}])[0].get("message", {}).get("content", "")
                else:
                    resp_text = f"OpenAI error: {r.status_code}"
        except Exception as e:
            resp_text = str(e)
    else:
        # fallback: simple question generation by selecting sentences
        try:
            import re
            sents = re.split(r'(?<=[\.\!\?])\s+', (prompt_text or '').strip())
            qs = []
            for i, s in enumerate(sents[:5]):
                if not s.strip():
                    continue
                qs.append(f"Q{i+1}: {s.strip()}\nA) True  B) False  C) Maybe  D) Not sure\nAnswer: A")
            resp_text = "\n\n".join(qs) if qs else "No content to generate quiz."
        except Exception:
            resp_text = "No OpenAI key configured and quiz generation failed"

    with Session(engine) as session:
        ar = AIResult(presentation_id=presentation_id, task_type="quiz", result=resp_text)
        session.add(ar)
        session.commit()
        session.refresh(ar)


def enqueue_ai_quiz(presentation_id: int):
    job = q.enqueue(ai_generate_quiz, presentation_id)
    return job.get_id()


def ai_generate_flashcards(presentation_id: int):
    OPENAI_KEY = os.getenv("OPENAI_API_KEY")
    with Session(engine) as session:
        pres = session.get(Presentation, presentation_id)
        if not pres:
            return
        title = (getattr(pres, "title", "") or "").strip()
        desc = (pres.description or "").strip()
        header = f"Title: {title}\n" if title else ""
        prompt_text = header + (desc or "")
    resp_text = ""
    if OPENAI_KEY:
        try:
            headers = {"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"}
            data = {
                "model": "gpt-4o-mini",
                "messages": [{
                    "role": "user",
                    "content": (
                        "Create 10 concise study flashcards from the following presentation. "
                        "Each flashcard should be in the format 'Term: short, simple definition'. "
                        "Focus on the most important concepts, keywords, or formulas that a learner should remember.\n\n"
                        "Presentation info:\n" + prompt_text
                    ),
                }],
                "max_tokens": 800,
            }
            with httpx.Client(timeout=30) as client:
                r = client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=data)
                if r.status_code == 200:
                    j = r.json()
                    resp_text = j.get("choices", [{}])[0].get("message", {}).get("content", "")
                else:
                    resp_text = f"OpenAI error: {r.status_code}"
        except Exception as e:
            resp_text = str(e)
    else:
        resp_text = (prompt_text or '')[:800] or 'No content to generate flashcards.'

    with Session(engine) as session:
        ar = AIResult(presentation_id=presentation_id, task_type="flashcards", result=resp_text)
        session.add(ar)
        session.commit()
        session.refresh(ar)


def enqueue_ai_flashcards(presentation_id: int):
    job = q.enqueue(ai_generate_flashcards, presentation_id)
    return job.get_id()


def ai_generate_mindmap(presentation_id: int):
    OPENAI_KEY = os.getenv("OPENAI_API_KEY")
    with Session(engine) as session:
        pres = session.get(Presentation, presentation_id)
        if not pres:
            return
        title = (getattr(pres, "title", "") or "").strip()
        desc = (pres.description or "").strip()
        header = f"Title: {title}\n" if title else ""
        prompt_text = header + (desc or "")
    resp_text = ""
    if OPENAI_KEY:
        try:
            headers = {"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"}
            data = {
                "model": "gpt-4o-mini",
                "messages": [{
                    "role": "user",
                    "content": (
                        "Based on the presentation information below, create a text mind-map outline. "
                        "Use bullet points with indentation to show structure, for example:\n"
                        "- Main topic\n  - Subtopic 1\n    - Key detail A\n  - Subtopic 2\n"
                        "Cover the central idea first, then 3–6 main branches with 2–4 short subpoints each.\n\n"
                        "Presentation info:\n" + prompt_text
                    ),
                }],
                "max_tokens": 800,
            }
            with httpx.Client(timeout=30) as client:
                r = client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=data)
                if r.status_code == 200:
                    j = r.json()
                    resp_text = j.get("choices", [{}])[0].get("message", {}).get("content", "")
                else:
                    resp_text = f"OpenAI error: {r.status_code}"
        except Exception as e:
            resp_text = str(e)
    else:
        resp_text = (prompt_text or '')[:800] or 'No content to generate mind map.'

    with Session(engine) as session:
        ar = AIResult(presentation_id=presentation_id, task_type="mindmap", result=resp_text)
        session.add(ar)
        session.commit()
        session.refresh(ar)


def enqueue_ai_mindmap(presentation_id: int):
    job = q.enqueue(ai_generate_mindmap, presentation_id)
    return job.get_id()


def ai_autograde_submission(submission_id: int):
    """Auto-grade a submission using OpenAI when available, otherwise simple heuristics."""
    OPENAI_KEY = os.getenv("OPENAI_API_KEY")
    from pathlib import Path
    # import models locally
    from .models import Submission, Assignment, StudentAnalytics
    with Session(engine) as session:
        sub = session.get(Submission, submission_id)
        if not sub:
            return
        a = session.get(Assignment, sub.assignment_id)
        # locate file on disk
        UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")
        body_text = ''
        if sub.filename:
            p = Path(UPLOAD_DIR) / sub.filename
            if p.exists():
                try:
                    if p.suffix.lower() == '.pdf':
                        import fitz
                        doc = fitz.open(str(p))
                        body_text = "\n".join([doc[i].get_text() for i in range(len(doc))])
                    else:
                        body_text = p.read_text(encoding='utf-8', errors='ignore')
                except Exception:
                    try:
                        body_text = p.read_text(encoding='utf-8', errors='ignore')
                    except Exception:
                        body_text = ''

        grade = None
        feedback = None
        if OPENAI_KEY and body_text:
            try:
                import httpx
                headers = {"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"}
                prompt = f"You are a helpful grader. Assign a numeric score 0-100 and a one-paragraph feedback for this student submission for the assignment titled '{a.title}'. Submission content:\n{body_text[:8000]}"
                data = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}], "max_tokens": 300}
                with httpx.Client(timeout=30) as client:
                    r = client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=data)
                    if r.status_code == 200:
                        j = r.json()
                        resp = j.get('choices', [{}])[0].get('message', {}).get('content', '')
                        # try to parse a leading numeric grade
                        import re
                        m = re.search(r'(\d{1,3})', resp)
                        if m:
                            grade = min(100.0, float(m.group(1)))
                        feedback = resp
                    else:
                        feedback = f"OpenAI error: {r.status_code}"
            except Exception as e:
                feedback = str(e)

        if grade is None:
            # heuristic: base on content length
            length = len(body_text or '')
            if length >= 2000:
                grade = 85.0
            elif length >= 800:
                grade = 75.0
            elif length >= 200:
                grade = 65.0
            else:
                grade = 50.0
            if not feedback:
                feedback = f"Auto-graded by heuristic based on submission length ({len(body_text or '')} chars)."

        # save grade and analytics
        with Session(engine) as session:
            sub = session.get(Submission, submission_id)
            sub.grade = float(grade)
            sub.feedback = feedback
            session.add(sub)
            # record analytics event
            try:
                sa = StudentAnalytics(user_id=sub.student_id, classroom_id=a.classroom_id if a else None, event_type='grade', details=f'grade={grade};assignment={sub.assignment_id}')
                session.add(sa)
            except Exception:
                pass
            session.commit()


def enqueue_autograde_submission(submission_id: int):
    job = q.enqueue(ai_autograde_submission, submission_id)
    return job.get_id()


def send_email_worker(to_address: str, subject: str, body: str = None, template_name: str = None, context: dict = None):
    """Worker task to send email using SMTP env vars. Renders Jinja templates when provided.
    Runs inside an RQ worker."""
    import smtplib
    import ssl
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from pathlib import Path

    smtp_host = os.getenv('SMTP_HOST')
    smtp_port = int(os.getenv('SMTP_PORT', '587'))
    smtp_user = os.getenv('SMTP_USER')
    smtp_pass = os.getenv('SMTP_PASS')
    from_addr = os.getenv('EMAIL_FROM', smtp_user or 'noreply@example.com')
    if not smtp_host:
        return False

    text_body = body or ''
    html_body = None
    # if a template is provided, render it from the repository templates directory
    if template_name:
        try:
            templates_dir = Path(__file__).resolve().parents[1] / 'templates'
            env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=select_autoescape(['html', 'xml']))
            tpl = env.get_template(template_name)
            html_body = tpl.render(context or {})
            # attempt to produce a text fallback by stripping tags if no explicit text provided
            if not text_body:
                # very small fallback: render template and strip tags crudely
                import re
                text_body = re.sub(r'<[^>]+>', '', html_body)
        except Exception:
            html_body = None

    # Build MIME multipart message
    msg = MIMEMultipart('alternative')
    msg['From'] = from_addr
    msg['To'] = to_address
    msg['Subject'] = subject

    try:
        part_text = MIMEText(text_body or '', 'plain', 'utf-8')
        msg.attach(part_text)
        if html_body:
            part_html = MIMEText(html_body, 'html', 'utf-8')
            msg.attach(part_html)

        context_ssl = ssl.create_default_context()
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.starttls(context=context_ssl)
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.sendmail(from_addr, [to_address], msg.as_string())
        return True
    except Exception:
        return False


def enqueue_email(to_address: str, subject: str, body: str = None, template_name: str = None, context: dict = None):
    """Enqueue an email send job with retry/backoff. Falls back to synchronous send on failure."""
    try:
        from rq import Retry
        retry = Retry(max=3, interval=[10, 30, 60])
        job = q.enqueue(send_email_worker, to_address, subject, body, template_name, context, retry=retry, timeout=120)
        return job.get_id()
    except Exception:
        # Fallback behaviour when Redis/Q is not available:
        # 1) attempt synchronous send (existing behaviour)
        try:
            return send_email_worker(to_address, subject, body, template_name, context)
        except Exception:
            pass
        # 2) as a durable fallback, write a job file to a local queue directory for later processing by `scripts/local_worker.py`
        try:
            import json, uuid
            local_dir = Path(__file__).resolve().parents[1].parent / 'scripts' / 'local_email_queue'
            local_dir.mkdir(parents=True, exist_ok=True)
            job_id = uuid.uuid4().hex
            payload = {
                'id': job_id,
                'to': to_address,
                'subject': subject,
                'body': body,
                'template': template_name,
                'context': context,
                'ts': __import__('time').time(),
            }
            p = local_dir / f"{job_id}.json"
            with p.open('w', encoding='utf-8') as fh:
                json.dump(payload, fh)
            return job_id
        except Exception:
            return None
