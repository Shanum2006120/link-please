# LinkPlease - Instagram Automation System

A system that automatically DMs users when they comment specific keywords on Instagram posts, built for the LinkPlease tech intern assignment.

## Features

**Part A (Required):**
- Create rules to match keywords in comments and send automated DMs
- Duplicate prevention - same user never gets DMed twice for the same rule
- Background processing to handle webhook events within 5 seconds
- Retry logic for failed DMs with rate limit handling
- Atomic duplicate detection using database constraints

**Part B:**
- Webhook signature verification using HMAC-SHA256 (REQUIRED - rejects unsigned requests)
- Live statistics endpoint with real-time counts

**Part C:**
- Delivery status reconciliation - catches DMs that were accepted by API but failed later
- Handles `comment.deleted` events by cancelling pending DMs
- Database-backed rate limiter to respect API limits (10 req/60s)
- Exponential backoff with jitter for retries
- Scheduled retry system using database timestamps

**Additional:**
- Professional web dashboard for creating rules and viewing stats
- SQLite with WAL mode for better concurrency
- Atomic operations to prevent race conditions

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Get your PseudoGram API key:
```bash
# Apply for access
curl -X POST https://pseudogram-api.onrender.com/v1/apply \
  -H "Content-Type: application/json" \
  -d '{"name":"Your Name","email":"you@example.com","phone":"+91...","whatsapp":"+91...","linkedin_url":"https://linkedin.com/in/you"}'

# Get your key
curl -X POST https://pseudogram-api.onrender.com/v1/keygen \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com"}'
```

3. Create `.env` file:
```bash
cp .env.example .env
# Edit .env and add your API_KEY
```

4. Run the server:
```bash
python main.py
```

The server will start on `http://0.0.0.0:8000`

Visit `http://localhost:8000` to access the web dashboard.

## API Endpoints

### GET /
Serves the web dashboard UI.

### POST /webhook
Receives comment events from PseudoGram. Returns 200 immediately, processes in background. **Requires HMAC-SHA256 signature.**

### POST /rules
Create a new keyword-to-DM rule.
```json
{
  "keyword": "PRICE",
  "dm_message": "Here's the price list: ..."
}
```

### GET /rules-list
List all active rules (used by dashboard).

### GET /stats
Get current statistics:
```json
{
  "sent": 142,
  "failed": 3,
  "queued": 8,
  "duplicates_blocked": 57
}
```

## Testing

Use the simulation endpoint to test:
```bash
curl -X POST https://pseudogram-api.onrender.com/v1/simulate/start \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"webhook_url":"https://your-app.example.com/webhook","count":500,"duration_seconds":10}'
```

Then check the truth data:
```bash
curl https://pseudogram-api.onrender.com/v1/simulate/{run_id}/truth
```

## Architecture

- **FastAPI** for the web server with Jinja2 templates
- **SQLite with WAL mode** for persistent storage (rules, sent DMs, processed events, rate limits)
- **httpx** for async HTTP requests to PseudoGram API
- **Background tasks** for webhook processing and DM reconciliation
- **Database-backed rate limiter** that survives process restarts
- **Exponential backoff with jitter** for intelligent retry behavior
- **Atomic operations** using `INSERT OR IGNORE` to prevent race conditions

## Known Limitations

See `FAILURES.md` for a detailed list of known failure modes and edge cases.

## Parts Completed
A+B+C
