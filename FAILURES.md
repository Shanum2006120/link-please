# Known Failure Modes

This document lists every way the system can still lose a DM, send a duplicate, or report a wrong number.

## Critical Issues

1. **Process restart during in-flight DM sends**: If the server restarts while a DM send is in progress (after the API call started but before the response was written to the database), that DM attempt is lost. The database won't have the `dm_id`, so the reconciliation task won't know to check its status. The DM may have been delivered, but we'll never confirm it and may retry it, potentially sending a duplicate.

2. **SQLite database corruption on crash**: If the process crashes during a write operation, SQLite's WAL mode helps but doesn't guarantee complete recovery. The `processed_events` table could lose entries, causing duplicate event processing on restart.

3. **Database connection pool exhaustion**: Under extreme load (500+ events in 10 seconds), the current thread-local connection pattern could exhaust file handles or cause connection timeouts, leading to dropped events.

## Moderate Issues

4. **Rate limiter cleanup timing**: The rate limiter cleans up old entries on each request. Under very low traffic, old entries could accumulate for hours, causing the window to be effectively larger than 60 seconds until a request triggers cleanup.

5. **Background task restart delay**: If the server restarts, the reconciliation task takes 10 seconds before it starts checking queued DMs. Any DMs that were accepted by the API but failed during this window won't be retried until the next cycle.

6. **No dead letter queue**: If a rule is deleted while DMs仍 queued for it, those DMs will fail permanently when they try to look up the rule message. There's no mechanism to alert or recover these.

## Minor Issues

7. **Stats accuracy during concurrent operations**: The `/stats` endpoint performs separate COUNT queries. If a DM's status changes between queries (e.g., from queued to delivered), the counts could be momentarily inconsistent. This is rare but possible under load.

8. **Comment deletion timing edge case**: If a `comment.deleted` event arrives after the DM was sent but before it's marked as delivered, we don't retract the DM. The spec says "think about what should happen" - current implementation only cancels pending DMs, not already-sent ones.

9. **No request timeout on webhook processing**: While the webhook returns quickly, if the background task queue grows very large (e.g., thousands of events), individual events could wait minutes before processing, potentially exceeding any implicit SLAs.

10. **Idempotency key collision**: The idempotency key uses `user_id_comment_id`. If the same user comments on different posts with the same comment_id (unlikely but possible if the API reuses IDs), we could get incorrect idempotency behavior.

## What Was Fixed

- **Race condition in duplicate detection**: Now uses `INSERT OR IGNORE` which is atomic at the database level
- **In-memory rate limiting**: Now uses database-backed rate limit tracking that survives restarts
- **Optional HMAC signature**: Now requires signature header on all webhook requests
- **Linear retry backoff**: Now uses exponential backoff with jitter (2^n up to 60s)
- **No retry scheduling**: Now stores `next_retry_at` in database for scheduled retries
