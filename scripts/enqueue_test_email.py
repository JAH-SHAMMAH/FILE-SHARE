"""Enqueue a test email job using `enqueue_email`.

Run with the project's venv active. If Redis is not running, the function will fall back
to synchronous send (or no-op if SMTP not configured).

Usage:
  & .\.venv\Scripts\Activate.ps1
  python .\scripts\enqueue_test_email.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str((Path(__file__).resolve().parents[0].parent / 'SLIDESHARE')))

from app.tasks import enqueue_email

def main():
    jid = enqueue_email('test@example.com', 'Test email from SLIDESHARE', 'This is a test', template_name=None, context=None)
    print('Enqueued job id:', jid)

if __name__ == '__main__':
    main()
