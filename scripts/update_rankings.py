#!/usr/bin/env python3
"""Cream02 First: complete, validated holder pagination; bounded parallel fetching."""
import argparse
import concurrent.futures
import json
import logging
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

API = 'https://objekt.top/api/v1'
OUT = Path(__file__).resolve().parents[1] / 'public' / 'data' / 'rankings.json'
MEMBERS = ['SeoYeon','HyeRin','JiWoo','ChaeYeon','YooYeon','SooMin','NaKyoung','YuBin','Kaede','DaHyun','Kotone','YeonJi','Nien','SoHyun','Xinyu','Mayu','Lynn','JooBin','HaYeon','ShiOn','ChaeWon','Sullin','SeoAh','JiYeon']
RANGES = {'a': tuple(range(101,109)), 'b': tuple(range(109,117)), 'c': tuple(range(117,121))}
# Per GitHub job, not a global limit across 24 jobs. Raise if API responds 429.
MIN_REQUEST_INTERVAL = 0.20
MAX_CARD_WORKERS = 5
MAX_PAGE_WORKERS = 4
PAGE_SIZE = 50
_request_lock = threading.Lock()
_last_request = 0.0


def get_json(path):
    global _last_request
    url = API + path
    for attempt in range(5):
        with _request_lock:
            wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request)
            if wait > 0:
                time.sleep(wait)
            _last_request = time.monotonic()
        try:
            req = urllib.request.Request(url, headers={
                'Accept': 'application/json',
                'User-Agent': 'tripleS-cream02-ranking/1.0 (fan-made public data viewer)',
            })
            with urllib.request.urlopen(req, timeout=35) as response:
                value = json.load(response)
            if isinstance(value, dict) and isinstance(value.get('data'), dict):
                value = value['data']
            if isinstance(value, dict) and isinstance(value.get('body'), dict):
                value = value['body']
            return value
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            retryable = not isinstance(exc, urllib.error.HTTPError) or exc.code in (429, 500, 502, 503, 504)
            if not retryable or attempt == 4:
                raise RuntimeError(f'è®å {url} å¤±æï¼{exc}') from exc
            pause = min(3 * 2**attempt + random.random(), 35)
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
                retry_after = exc.headers.get('Retry-After')
                if retry_after and retry_after.isdigit():
                    pause = max(pause, min(int(retry_after), 120))
            logging.warning('API æ«æå¤±æï¼%.1f ç§å¾éè©¦ï¼%s', pause, exc)
            time.sleep(pause)
    raise AssertionError('unreachable')


def norm(value):
    return re.sub(r'[^a-z0-9]', '', str(value).lower())


def collection_number(c):
    raw = str(c.get('collectionNo', ''))
    match = re.fullmatch(r'(\d{3})[zZ]?', raw)
    if match:
        return int(match.group(1))
    match = re.search(r'-(\d{3})z$', str(c.get('slug', '')), flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def index_collections(collections):
    indexed = defaultdict(dict)
    for c in collections:
        if norm(c.get('artist')) != 'triples' or norm(c.get('season')) not in ('cream02', 'c2'):
            continue
        if norm(c.get('class')) != 'first' or str(c.get('onOffline', '')).lower() != 'online':
            continue
        n = collection_number(c)
        if n is None or not 101 <= n <= 120:
            continue
        member = next((m for m in MEMBERS if norm(m) == norm(c.get('member'))), None)
        if not member:
            continue
        slug = c.get('slug')
        if not isinstance(slug, str) or not slug:
            raise ValueError(f'{member} {n} ç¼ºå° slug')
        if n in indexed[member]:
            raise ValueError(f'{member} {n} éè¤å¡çï¼{slug}')
        indexed[member][n] = slug
    return indexed


def fetch_page(slug, offset):
    path = f'/collections/{urllib.parse.quote(slug, safe="")}/holders?offset={offset}&limit={PAGE_SIZE}'
    payload = get_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get('rows'), list):
        raise ValueError(f'{slug} offset={offset} åå³æ ¼å¼é¯èª¤')
    return payload


def holder_pages_once(slug):
    first = fetch_page(slug, 0)
    summary = first.get('summary')
    total = summary.get('holders') if isinstance(summary, dict) else None
    if type(total) is not int or total < 0 or total > 2_000_000:
        raise ValueError(f'{slug} ç¡æç summary.holdersï¼{total}')
    offsets = list(range(0, total, PAGE_SIZE))
    # The API should return zero rows for an empty collection.
    if not offsets:
        if first['rows'] or first.get('nextOffset') is not None:
            raise ValueError(f'{slug} ç©ºéååé ä¸ä¸è´')
        return {}
    pages = {0: first}
    if len(offsets) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_PAGE_WORKERS) as pool:
            futures = {pool.submit(fetch_page, slug, offset): offset for offset in offsets[1:]}
            for future in concurrent.futures.as_completed(futures):
                pages[futures[future]] = future.result()
    results = {}
    for offset in offsets:
        payload = pages[offset]
        rows = payload['rows']
        expected_count = min(PAGE_SIZE, total - offset)
        if len(rows) != expected_count:
            raise ValueError(f'{slug} offset={offset} é æ {expected_count} ç­ï¼å¯¦é {len(rows)} ç­')
        expected_next = offset + PAGE_SIZE if offset + PAGE_SIZE < total else None
        if payload.get('nextOffset') != expected_next:
            raise ValueError(f'{slug} offset={offset} nextOffset ä¸ä¸è´')
        page_summary = payload.get('summary')
        if isinstance(page_summary, dict) and type(page_summary.get('holders')) is int and page_summary['holders'] != total:
            raise ValueError(f'{slug} åé æéææäººæ¸è®å')
        for item in rows:
            if not isinstance(item, dict) or not isinstance(item.get('holder'), dict):
                raise ValueError(f'{slug} holder æ ¼å¼é¯èª¤')
            holder = item['holder']
            address = str(holder.get('address', '')).lower()
            copies = item.get('copies')
            if not re.fullmatch(r'0x[0-9a-f]{40}', address):
                raise ValueError(f'{slug} ç¡æé¢åå°åï¼{address}')
            if type(copies) is not int or copies <= 0:
                raise ValueError(f'{slug} ç¡æ copiesï¼{copies}')
            if address in results:
                raise ValueError(f'{slug} éè¤é¢åï¼{address}')
            results[address] = {'copies': copies, 'nickname': holder.get('nickname')}
    if len(results) != total:
        raise ValueError(f'{slug} é æ {total} ä½ï¼å¯¦é {len(results)} ä½')
    return results


def holder_pages(slug):
    for attempt in range(2):
        try:
            return holder_pages_once(slug)
        except ValueError as exc:
            if attempt:
                raise
            logging.warning('%s åé é©è­å¤±æï¼éæ°æåä¸æ¬¡ï¼%s', slug, exc)
            time.sleep(1)
    raise AssertionError('unreachable')


def score(accounts):
    scores = []
    for address, info in accounts.items():
        holdings = info['holdings']
        groups = {key: min(holdings.get(n, 0) for n in nos) for key, nos in RANGES.items()}
        total = sum(groups.values())
        if total:
            name = info['nickname'] or (address[:9] + 'â¦' + address[-6:])
            scores.append({'name': name, 'address': address, 'total': total, **groups})
    scores.sort(key=lambda r: (-r['total'], -r['a'], -r['b'], -r['c'], r['address']))
    return scores


def update_member(member, index):
    slugs = index.get(member, {})
    missing = [n for n in range(101, 121) if n not in slugs]
    if missing:
        raise ValueError(f'{member} ç¼ºå°å¡çï¼{missing}')
    accounts = defaultdict(lambda: {'holdings': {}, 'nickname': None})
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CARD_WORKERS) as pool:
        futures = {pool.submit(holder_pages, slugs[n]): n for n in range(101, 121)}
        completed = 0
        for future in concurrent.futures.as_completed(futures):
            number = futures[future]
            people = future.result()
            for address, detail in people.items():
                accounts[address]['holdings'][number] = detail['copies']
                if detail['nickname']:
                    accounts[address]['nickname'] = detail['nickname']
            completed += 1
            logging.info('%sï¼%d/20 %sï¼%d ä½ææäºº', member, completed, slugs[number], len(people))
    return {
        'checkedAt': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'rows': score(accounts),
        'walletsWithAnyFirst': len(accounts),
        'collectionsVerified': 20,
    }


def persist(database):
    database['updatedAt'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix('.tmp')
    tmp.write_text(json.dumps(database, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    tmp.replace(OUT)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--member', default='all')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    target = MEMBERS if args.member.lower() == 'all' else [next((m for m in MEMBERS if m.lower() == args.member.lower()), None)]
    if None in target:
        parser.error('è«è¼¸å¥ all æ 24 ä½æå¡è±æåç¨±')
    database = json.loads(OUT.read_text(encoding='utf-8')) if OUT.exists() else {
        'version': 1, 'source': 'https://objekt.top/', 'updatedAt': None, 'members': {}}
    payload = get_json('/collections?originals=false&ids=false')
    if not isinstance(payload, dict) or not isinstance(payload.get('collections'), list):
        raise ValueError('Collection API æ ¼å¼é¯èª¤')
    index = index_collections(payload['collections'])
    failed = []
    for member in target:
        try:
            entry = update_member(member, index)
            database['members'][member] = entry
            persist(database)
            logging.info('å®æ %sï¼%d ä½å®æ´å¥æ¸ææè', member, len(entry['rows']))
        except (ValueError, RuntimeError, urllib.error.URLError) as exc:
            logging.exception('æ´æ° %s å¤±æï¼%s', member, exc)
            failed.append(member)
            previous = database['members'].get(member)
            if isinstance(previous, dict) and 'rows' in previous:
                previous['error'] = f'æè¿ä¸æ¬¡æ´æ°å¤±æï¼{datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}ï¼'
                persist(database)
    if failed:
        logging.error('æ´æ°å¤±æï¼%s', ', '.join(failed))
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
