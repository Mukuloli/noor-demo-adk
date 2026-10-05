"""Verify Redis connectivity without printing URLs or patient data."""
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from noor.config import settings
from noor_database.cache import get_cache


def main():
    cache = get_cache(settings)
    if cache.client is None:
        raise SystemExit('Redis is disabled. Set REDIS_URL in this app\'s local .env.')
    calls = []
    identity = uuid.uuid4().hex
    def load():
        calls.append(True)
        return {'probe': 'ok'}
    cache.remember('diagnostic', identity, 10, load)
    cache.remember('diagnostic', identity, 10, load)
    print(json.dumps(cache.status()))
    if len(calls) != 1:
        raise SystemExit('Redis is unreachable. Firestore fallback is active; check the server and REDIS_URL.')
    print('Redis connection OK: repeated reads used one source load.')


if __name__ == '__main__':
    main()
