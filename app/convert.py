import os
import subprocess
from pathlib import Path
import logging
from typing import Optional

logger = logging.getLogger("slideshare.convert")


def _resolve_soffice_command() -> str:
    """Return the best soffice command for this environment.

    On Windows installed via LibreOffice's default MSI/winget, soffice.exe
    typically lives under:
        C:\Program Files\LibreOffice\program\soffice.exe

    We prefer an explicit path when it exists so we don't depend on PATH
    configuration; otherwise we fall back to the plain "soffice" command,
    which works on Linux/macOS when LibreOffice is on PATH.
    """
    # Caller can override via env if they like
    env_path = os.getenv("LIBREOFFICE_PATH") or os.getenv("SOFFICE_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return str(p)

    if os.name == "nt":  # Windows default install locations
        candidates = [
            Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
            Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
        ]
        for c in candidates:
            if c.exists():
                return str(c)

    # Fallback: rely on PATH
    return "soffice"


def convert_doc_to_pdf(src: str, out_dir: str) -> Optional[str]:
    """Use LibreOffice (soffice) to convert a document to PDF. Returns PDF path or None."""
    try:
        src_p = Path(src)
        out_d = Path(out_dir)
        out_d.mkdir(parents=True, exist_ok=True)
        soffice_cmd = _resolve_soffice_command()
        # LibreOffice writes PDF next to source by default; call with --outdir
        subprocess.run([soffice_cmd, "--headless", "--convert-to", "pdf", str(src_p), "--outdir", str(out_d)], check=True)
        pdf = out_d / src_p.with_suffix('.pdf').name
        if pdf.exists():
            return str(pdf)
    except Exception as e:
        logger.exception("doc->pdf conversion failed: %s", e)
    return None


def generate_pdf_thumbnails(pdf_path: str, thumbs_dir: str, max_pages: int = 10) -> list:
    """Generate per-page PNG thumbnails for a PDF at high resolution.

    Prefers PyMuPDF (fitz) rendering for crisp slides; falls back to
    ImageMagick `convert` when fitz is unavailable. Returns list of
    thumbnail paths (may be empty).
    """
    out: list[str] = []
    p = Path(pdf_path)
    td = Path(thumbs_dir)
    td.mkdir(parents=True, exist_ok=True)

    # First try high-quality rendering via PyMuPDF if installed
    try:
        import fitz  # type: ignore

        doc = fitz.open(str(p))
        # scale up for Retina/hiDPI clarity; configurable via env
        # default 4.0 is very high quality and suitable for "max" clarity
        scale = float(os.getenv("THUMBNAIL_SCALE", "4.0"))
        mat = fitz.Matrix(scale, scale)
        page_count = min(doc.page_count, max_pages)
        for i in range(page_count):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=mat)
            out_path = td / f"slide_{i}.png"
            pix.save(str(out_path))
            out.append(str(out_path))
        doc.close()
        return out
    except Exception as e:
        logger.exception("PyMuPDF thumbnail generation failed; falling back to convert: %s", e)

    # Fallback: ImageMagick `convert` if available
    try:
        pattern = str(td / "slide_%d.png")
        # Use a generous thumbnail height for clarity
        subprocess.run(["convert", str(p), "-thumbnail", "x2000", pattern], check=True)
        for f in sorted(td.glob("slide_*.png"))[:max_pages]:
            out.append(str(f))
    except Exception as e:
        logger.exception("pdf thumbnails generation failed: %s", e)
    return out


def generate_video_thumbnail(video_path: str, out_path: str, time_pos: float = 1.0) -> Optional[str]:
    """Use ffmpeg to extract a frame at time_pos seconds and save as PNG."""
    try:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-ss", str(time_pos), "-i", str(video_path), "-frames:v", "1", str(out_path)], check=True)
        if Path(out_path).exists():
            return str(out_path)
    except Exception as e:
        logger.exception("video thumbnail failed: %s", e)
    return None


def generate_audio_waveform(audio_path: str, out_path: str, width: int = 800, height: int = 200) -> Optional[str]:
    """Use ffmpeg showwavespic filter to create waveform PNG."""
    try:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            "ffmpeg",
            "-y",
            "-i",
            str(audio_path),
            "-filter_complex",
            f"aformat=channel_layouts=mono,showwavespic=s={width}x{height}",
            "-frames:v",
            "1",
            str(out_path),
        ], check=True)
        if Path(out_path).exists():
            return str(out_path)
    except Exception as e:
        logger.exception("audio waveform generation failed: %s", e)
    return None


def render_code_syntax(input_path: str, out_path: str) -> Optional[str]:
    """Render source code to an HTML snippet using Pygments. Returns HTML path."""
    try:
        from pygments import highlight
        from pygments.lexers import guess_lexer_for_filename, get_lexer_by_name
        from pygments.formatters import HtmlFormatter

        p = Path(input_path)
        code = p.read_text(encoding='utf-8', errors='ignore')
        try:
            lexer = guess_lexer_for_filename(p.name, code)
        except Exception:
            lexer = get_lexer_by_name('text')
        fmt = HtmlFormatter(full=True, linenos=False, style='friendly')
        html = highlight(code, lexer, fmt)
        out_p = Path(out_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(html, encoding='utf-8')
        return str(out_p)
    except Exception as e:
        logger.exception("code rendering failed: %s", e)
    return None
