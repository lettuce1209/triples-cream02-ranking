#!/usr/bin/env python3
"""從 objekt.top 公開 API 統計 tripleS Cream02 First 各錢包完整持有套數。

不需要 API 金鑰；需網站保持 API 可用。全量抓取 480 個 collection，
每個 collection 透過 holders 分頁到最後一頁，避免把只抓前 50 名誤當全站排名。
"""

import argparse
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
MEMBERS = [
    'SeoYeon', 'HyeRin', 'JiWoo', 'ChaeYeon', 'YooYeon', 'SooMin',
    'NaKyoung', 'YuBin', 'Kaede', 'DaHyun', 'Kotone', 'YeonJi',
    'Nien', 'SoHyun', 'Xinyu', 'Mayu', 'Lynn', 'JooBin',
    'HaYeon', 'ShiOn', 'ChaeWon', 'Sullin', 'SeoAh', 'JiYeon',
]
RANGES = {'a': tuple(range(101, 109)),
          'b': tuple(range(109, 117)),
          'c': tuple(range(117, 121))}
# 控制速率，避免一次對資料來源發出大量請求。
_request_lock = threading.Lock()
_last_request = 0.0


def get_json(path):
    global _last_request
    url = API + path
    for attempt in range(5):
        with _request_lock:
            now = time.monotonic()
            wait = 0.35 - (now - _last_request)  # 平均每秒不超過約 3 次
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
            # OpenAPI 預期回傳直接 JSON；同時容忍可能存在的 data 包裝。
            if isinstance(value, dict) and isinstance(value.get('data'), dict):
                value = value['data']
            if isinstance(value, dict) and isinstance(value.get('body'), dict):
                value = value['body']
            return value
        except (urllib.error.HTTPError, urllib.error.URLError,
                TimeoutError, json.JSONDecodeError) as ex:
            retryable = not isinstance(ex, urllib.error.HTTPError) or ex.code in (429, 500, 502, 503, 504)
            if not retryable or attempt == 4:
                raise RuntimeError(f'讀取 {url} 失敗：{ex}') from ex
            pause = min(3 * (2 ** attempt) + random.random(), 35)
            logging.warning('資料來源暫時失敗，%.1f 秒後重試：%s', pause, ex)
            time.sleep(pause)
    raise AssertionError('unreachable')


def norm(value):
    return re.sub(r'[^a-z0-9]', '', str(value).lower())


def collection_number(c):
    # 一些資料來源可能把 Z 放在 collectionNo，一些只放在 slug。
    raw = str(c.get('collectionNo', ''))
    found = re.fullmatch(r'(\d{3})[zZ]?', raw)
    if found:
        return int(found.group(1))
    slug = str(c.get('slug', ''))
    found = re.search(r'-(\d{3})z$', slug, flags=re.IGNORECASE)
    return int(found.group(1)) if found else None


def index_collections(collections):
    indexed = defaultdict(dict)
    for c in collections:
        if norm(c.get('artist')) != 'triples':
            continue
        if norm(c.get('season')) not in ('cream02', 'c2'):
            continue
        if norm(c.get('class')) != 'first':
            continue
        if str(c.get('onOffline', '')).lower() != 'online':
            continue
        n = collection_number(c)
        if n is None or not 101 <= n <= 120:
            continue
        member = next((m for m in MEMBERS if norm(m) == norm(c.get('member'))), None)
        if not member:
            continue
        slug = c.get('slug')
        if not isinstance(slug, str) or not slug:
            raise ValueError(f'卡片 {member} {n} 缺少 slug')
        if n in indexed[member]:
            raise ValueError(f'{member} {n} 發現重複卡片，需人工檢查：{slug}')
        indexed[member][n] = slug
    return indexed


def holder_pages(slug):
    offset = 0
    results = {}
    count_pages = 0
    while True:
        path = f'/collections/{urllib.parse.quote(slug, safe="")}/holders?offset={offset}&limit=50'
        payload = get_json(path)
        if not isinstance(payload, dict) or not isinstance(payload.get('rows'), list):
            raise ValueError(f'{slug} holders 回傳格式不符：{str(payload)[:250]}')
        for item in payload['rows']:
            holder = item.get('holder', {})
            address = str(holder.get('address', '')).lower()
            copies = item.get('copies', 0)
            if not re.fullmatch(r'0x[0-9a-f]{40}', address):
                raise ValueError(f'{slug} 出現非預期錢包地址：{address}')
            if not isinstance(copies, int) or copies <= 0:
                raise ValueError(f'{slug} 出現非預期 copies：{copies}')
            if address in results:
                raise ValueError(f'{slug} 分頁出現重複錢包地址：{address}')
            results[address] = {'copies': copies, 'nickname': holder.get('nickname')}
        nxt = payload.get('nextOffset')
        count_pages += 1
        if nxt is None:
            total = payload.get('summary', {}).get('holders')
            if isinstance(total, int) and total != len(results):
                raise ValueError(f'{slug} 取回 {len(results)} 位，但網站宣告 {total} 位。')
            return results
        if not isinstance(nxt, int) or nxt <= offset:
            raise ValueError(f'{slug} nextOffset 不正確：{nxt}')
        offset = nxt
        if count_pages > 2000:
            raise ValueError(f'{slug} 分頁過多，為安全起見停止')


def score(accounts):
    """accounts[address] = {number: copies}; 回傳每個持有至少一套者。"""
    scores = []
    for address, info in accounts.items():
        holdings = info['holdings']
        groups = {key: min(holdings.get(n, 0) for n in nos) for key, nos in RANGES.items()}
        total = sum(groups.values())
        if total == 0:
            continue
        name = info['nickname'] or (address[:9] + '…' + address[-6:])
        scores.append({'name': name, 'address': address, 'total': total, **groups})
    scores.sort(key=lambda r: (-r['total'], -r['a'], -r['b'], -r['c'], r['address']))
    return scores


def update_member(member, index):
    slugs = index.get(member, {})
    missing = [n for n in range(101, 121) if n not in slugs]
    if missing:
        raise ValueError(f'{member} 缺少 {len(missing)} 張卡片：{missing}，不更新避免錯誤排行榜。')
    accounts = defaultdict(lambda: {'holdings': {}, 'nickname': None})
    for idx, number in enumerate(range(101, 121), start=1):
        slug = slugs[number]
        people = holder_pages(slug)
        for address, detail in people.items():
            accounts[address]['holdings'][number] = detail['copies']
            if detail['nickname']:
                accounts[address]['nickname'] = detail['nickname']
        logging.info('%s：%d/20 %s，%d 位持有人', member, idx, slug, len(people))
    rows = score(accounts)
    return {
        'checkedAt': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'rows': rows,
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
    parser.add_argument('--member', default='all', help='all 或指定 Mayu、SeoYeon 等')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    target = MEMBERS if args.member.lower() == 'all' else [next((m for m in MEMBERS if m.lower() == args.member.lower()), None)]
    if None in target:
        parser.error('成員名稱不正確。請輸入 all 或 24 位英文成員名稱之一。')
    database = json.loads(OUT.read_text(encoding='utf-8')) if OUT.exists() else {
        'version': 1, 'source': 'https://objekt.top/', 'updatedAt': None, 'members': {}}
    logging.info('正在讀取 objekt.top Collection 清單')
    payload = get_json('/collections?originals=false&ids=false')
    if not isinstance(payload, dict) or not isinstance(payload.get('collections'), list):
        raise ValueError(f'Collection API 格式不符：{str(payload)[:300]}')
    index = index_collections(payload['collections'])
    failed = []
    for member in target:
        try:
            entry = update_member(member, index)
            database['members'][member] = entry
            persist(database)  # 即使後面一位失敗，也保留已完成者。
            logging.info('完成 %s：%d 筆持有至少一套的錢包', member, len(entry['rows']))
        except (ValueError, RuntimeError, urllib.error.URLError) as exc:
            logging.exception('更新 %s 失敗', member)
            failed.append(member)
            # 若上次已經成功，保留舊資料；避免把「讀不到」當成「0 套」。
            previous = database['members'].get(member)
            if isinstance(previous, dict) and 'rows' in previous:
                previous['error'] = f'最近一次更新失敗（{datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}）'
                persist(database)
    if failed:
        logging.error('以下成員未成功更新：%s', ', '.join(failed))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
