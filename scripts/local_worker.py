"""Local worker to process fallback email job files written to `scripts/local_email_queue`.

Run this when Redis isn't available. It will process queued JSON job files by calling
`send_email_worker` from the application and write a log to `scripts/local_worker.log`.

Usage:
  & .\.venv\Scripts\Activate.ps1
  python .\scripts\local_worker.py
"""
import time
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[0].parent
sys.path.insert(0, str(ROOT / 'SLIDESHARE'))

from app.tasks import send_email_worker

QUEUE_DIR = Path(__file__).resolve().parents[0] / 'local_email_queue'
LOG_FILE = Path(__file__).resolve().parents[0] / 'local_worker.log'

def process_one(p: Path):
    try:
        with p.open('r', encoding='utf-8') as fh:
            job = json.load(fh)
        ok = send_email_worker(job.get('to'), job.get('subject'), job.get('body'), job.get('template'), job.get('context'))
        with LOG_FILE.open('a', encoding='utf-8') as lf:
            lf.write(f"{time.asctime()}: processed {p.name} -> {'OK' if ok else 'FAIL'}\n")
        # move to processed
        processed = QUEUE_DIR / 'processed'
        processed.mkdir(exist_ok=True)
        p.rename(processed / p.name)
    except Exception as e:
        with LOG_FILE.open('a', encoding='utf-8') as lf:
            lf.write(f"{time.asctime()}: error processing {p.name}: {e}\n")

def main(poll=5):
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    print('Local worker started, watching', QUEUE_DIR)
    while True:
        for p in sorted(QUEUE_DIR.glob('*.json')):
            process_one(p)
        time.sleep(poll)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Stopped')
