import requests
import json

r = requests.get('https://gamma-api.polymarket.com/markets?limit=50&active=true&closed=false&order=startDate&ascending=false')
data = r.json()

print(f"Total returned: {len(data)}")
for m in data:
    q = m.get('question', '')
    ed = m.get('endDate', '')
    print(f"{q} | {ed}")
