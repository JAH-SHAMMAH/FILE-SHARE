import os
import sys

# make package importable
ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)

from app import main as app_main

def main():
    # delete redis key if configured and redis client available
    redis_url = os.getenv('REDIS_URL')
    try:
        if hasattr(app_main, '_redis') and app_main._redis and redis_url:
            rc = app_main._redis.from_url(redis_url)
            rc.delete('category_counts')
            print('Deleted redis key: category_counts')
    except Exception as e:
        print('Redis delete failed or not configured:', e)

    # force recompute in-memory cache
    try:
        counts = app_main.get_category_counts(force=True)
        print('Recomputed in-memory category counts; categories:', len(counts))
    except Exception as e:
        print('Failed to recompute category counts:', e)

if __name__ == '__main__':
    main()
