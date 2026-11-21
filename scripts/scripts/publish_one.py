#!/usr/bin/env python3
"""抓取一位成員的完整榜單，成功後立即寫入 Cloudflare D1 Worker。"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'public' / 'data' / 'rankings.json'

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--member', required=True)
    args = parser.parse_args()
    url = os.environ.get('LIVE_API_URL', '').rstrip('/')
    token = os.environ.get('LIVE_UPLOAD_TOKEN', '')
    if not url.startswith('https://') or not token:
        raise RuntimeError('請先設定 LIVE_API_URL 和 LIVE_UPLOAD_TOKEN')
    # 原有 Python 程式會驗證 20 張卡片、所有 holders 分頁及完整套數。
    subprocess.run([sys.executable, str(ROOT / 'scripts' / 'update_rankings.py'),
                    '--member', args.member], cwd=ROOT, check=True)
    db = json.loads(DB.read_text(encoding='utf-8'))
    member = next((m for m in db.get('members', {}) if m.lower() == args.member.lower()), None)
    if member is None:
        raise RuntimeError('未找到剛更新的成員資料')
    entry = db['members'][member]
    if entry.get('collectionsVerified') != 20 or not isinstance(entry.get('rows'), list):
        raise RuntimeError('更新資料不完整，拒絕發布')
    body = json.dumps({'member': member, 'entry': entry}, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        url + '/api/update', data=body, method='POST',
        headers={'Content-Type':'application/json',
                 'Authorization':'Bearer ' + token,
                 'User-Agent':'cream02-live-updater/1.0'}
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        result = json.load(resp)
    if not result.get('ok'):
        raise RuntimeError('Worker 回覆更新失敗')
    print(f'已發布 {member}：{len(entry["rows"])} 位完整套數持有者，更新時間 {entry["checkedAt"]}')

if __name__ == '__main__':
    main()
