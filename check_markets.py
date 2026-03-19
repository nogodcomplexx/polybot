import requests
import json
import datetime
from datetime import timezone

r = requests.get('https://gamma-api.polymarket.com/markets?limit=100&active=true&closed=false&tag=crypto')
data = r.json()

now_date = datetime.datetime.now(timezone.utc).date()

for m in data:
    q = m.get('question', '')
    ed = m.get('endDate', '')
    if not ed: continue
    try:
        end_dt = datetime.datetime.fromisoformat(ed.replace("Z", "+00:00")).date()
        if end_dt == now_date:
            print(f"[SAME DAY] {q} | {ed}")
        else:
            print(f"[FUTURE] {q} | {ed}")
    except Exception as e:
        print(f"[ERROR] {q} | {ed}")
