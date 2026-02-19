"""Simple RQ worker launcher for the project's default queue.
Run with: python -m app.rq_worker
"""
import os
from dotenv import load_dotenv
load_dotenv()
import sys
import time
try:
    from redis import Redis
    from rq import Worker, Queue, Connection
except Exception:
    print("rq or redis not installed; install requirements and try again.")
    sys.exit(1)

from .tasks import q as default_queue

if __name__ == "__main__":
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        conn = Redis.from_url(redis_url)
    except Exception as e:
        print("Failed to connect to Redis:", e)
        sys.exit(1)

    with Connection(conn):
        qs = ["default"]
        worker = Worker(qs)
        print("RQ worker started, listening on queues:", qs)
        try:
            worker.work()
        except KeyboardInterrupt:
            print("Worker interrupted; exiting.")
