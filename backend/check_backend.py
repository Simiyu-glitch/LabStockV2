# check_backend.py — run this first to diagnose the calculate-days endpoint
# Usage: python check_backend.py
# Run from anywhere — it just makes an HTTP request to your local backend

import urllib.request
import json

url = "http://localhost:8000/leave/calculate-days"
payload = json.dumps({"start_date": "2026-07-13", "end_date": "2026-07-18"}).encode()

print(f"Testing: POST {url}")
print(f"Body: start_date=2026-07-13, end_date=2026-07-18")
print()

try:
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        body = json.loads(resp.read())
        print("SUCCESS — backend responded:")
        print(f"  working_days : {body.get('working_days')}")
        print(f"  total_days   : {body.get('total_days')}")
        print(f"  sundays      : {body.get('sundays')}")
        print(f"  ph_count     : {body.get('ph_count')}")
        print()
        print("The backend is healthy. If the frontend still shows 0,")
        print("the issue is the frontend file hasn't been replaced yet.")
except urllib.error.URLError as e:
    print(f"FAILED — could not reach backend: {e.reason}")
    print()
    print("This means uvicorn is not running or crashed.")
    print("Restart it: python -m uvicorn main:app --reload")
except Exception as e:
    print(f"ERROR: {e}")
