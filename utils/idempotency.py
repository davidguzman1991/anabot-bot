# utils/idempotency.py
from collections import OrderedDict
import threading

class LRUCache:
    def __init__(self, maxsize=1000):
        self.cache = OrderedDict()
        self.maxsize = maxsize
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            return None

    def set(self, key, value):
        with self.lock:
            self.cache[key] = value
            self.cache.move_to_end(key)
            if len(self.cache) > self.maxsize:
                self.cache.popitem(last=False)

# Global in-memory cache
_idem_cache = LRUCache(maxsize=2000)

def mark_processed(message_id, platform):
    key = f"{platform}:{message_id}"
    _idem_cache.set(key, True)
    # Optionally, try to persist in Postgres if available
    try:
        from config import get_settings
        import psycopg2
        _DATABASE_URL = get_settings().DATABASE_URL
        if not _DATABASE_URL:
            return
        with psycopg2.connect(_DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS processed_events (
                        message_id TEXT NOT NULL,
                        platform TEXT NOT NULL,
                        ts TIMESTAMP DEFAULT NOW(),
                        PRIMARY KEY (message_id, platform)
                    )
                    """
                )
                cur.execute(
                    "INSERT INTO processed_events (message_id, platform) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (message_id, platform)
                )
                conn.commit()
    except Exception:
        pass

def is_processed(message_id, platform):
    key = f"{platform}:{message_id}"
    if _idem_cache.get(key):
        return True
    # Optionally, check in Postgres if available
    try:
        from config import get_settings
        import psycopg2
        _DATABASE_URL = get_settings().DATABASE_URL
        if not _DATABASE_URL:
            return False
        with psycopg2.connect(_DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM processed_events WHERE message_id=%s AND platform=%s LIMIT 1",
                    (message_id, platform)
                )
                if cur.fetchone():
                    _idem_cache.set(key, True)
                    return True
    except Exception:
        pass
    return False
