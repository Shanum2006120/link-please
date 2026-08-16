import requests
import json

api_key = "c2hhbnVtc2hhaWsyQGdtYWlsLmNvbQ.f33e35a9f4b670384790"
webhook_url = "https://link-please-g3mf.onrender.com/webhook"

print("Starting simulation...")
print(f"Webhook URL: {webhook_url}")
print(f"Events: 500 over 10 seconds")
print()

response = requests.post(
    "https://pseudogram-api.onrender.com/v1/simulate/start",
    json={
        "webhook_url": webhook_url,
        "count": 500,
        "duration_seconds": 10
    },
    headers={
        "Content-Type": "application/json",
        "X-API-Key": api_key
    }
)

print(f"Status: {response.status_code}")
print(f"Response: {response.text}")

if response.status_code == 200:
    data = response.json()
    run_id = data.get("run_id")
    print(f"\n✓ Simulation started!")
    print(f"Run ID: {run_id}")
    print(f"\nCheck truth data with:")
    print(f"curl https://pseudogram-api.onrender.com/v1/simulate/{run_id}/truth")
    print(f"\nWatch your dashboard: {webhook_url.replace('/webhook', '')}")
else:
    print("\n✗ Simulation failed")
