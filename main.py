from fastapi import FastAPI, BackgroundTasks, HTTPException, Request, Header
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import hmac
import hashlib
import asyncio
from typing import Optional
from database import get_db_connection, init_db
from models import RuleCreate, RuleResponse, WebhookEvent, StatsResponse
from dm_sender import send_dm, process_retry
from config import settings
import json

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    init_db()
    # Start background reconciliation task
    asyncio.create_task(reconcile_dm_statuses())

async def reconcile_dm_statuses():
    """
    Periodically check the status of queued DMs and process retries.
    This handles the case where the API accepts a DM but it fails later.
    """
    while True:
        try:
            await asyncio.sleep(10)  # Check every 10 seconds for faster response
            
            import time
            now = time.time()
            
            with get_db_connection() as conn:
                cursor = conn.cursor()
                
                # Process DMs that are ready for retry
                cursor.execute("""
                    SELECT id, dm_id, retry_count
                    FROM sent_dms 
                    WHERE status = 'queued' 
                    AND (next_retry_at IS NULL OR next_retry_at <= ?)
                    AND dm_id IS NOT NULL
                    LIMIT 50
                """, (now,))
                rows = cursor.fetchall()
            
            for row in rows:
                dm_db_id = row["id"]
                api_dm_id = row["dm_id"]
                retry_count = row["retry_count"]
                
                # Check the status
                from dm_sender import check_dm_status
                status = await check_dm_status(api_dm_id)
                
                if status == "delivered":
                    with get_db_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            UPDATE sent_dms 
                            SET status = 'delivered', updated_at = CURRENT_TIMESTAMP 
                            WHERE id = ?
                        """, (dm_db_id,))
                        conn.commit()
                elif status == "failed":
                    # Retry the DM
                    await process_retry(dm_db_id)
                # If status is None or 'queued', check again later
                
                # Small delay between status checks to avoid hammering the API
                await asyncio.sleep(0.1)
                    
        except Exception as e:
            print(f"Error in reconcile_dm_statuses: {e}")
            await asyncio.sleep(30)

@app.post("/webhook")
async def webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_pseudogram_signature: str = Header(...)
):
    """
    Receives comment events from PseudoGram.
    Must return 200 within 5 seconds - real work happens in background.
    Signature is REQUIRED - rejects forged requests.
    """
    # Get raw body for signature verification
    body = await request.body()
    
    # Verify webhook signature - REQUIRED
    if not x_pseudogram_signature:
        raise HTTPException(status_code=401, detail="Signature required")
    
    if not x_pseudogram_signature.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Invalid signature format")
    
    signature_hash = x_pseudogram_signature[7:]  # Remove "sha256=" prefix
    
    # Compute HMAC-SHA256 of the body using API key as secret
    computed_hash = hmac.new(
        settings.api_key.encode(),
        body,
        hashlib.sha256
    ).hexdigest()
    
    if not hmac.compare_digest(computed_hash, signature_hash):
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    # Parse the event
    try:
        event = WebhookEvent(**json.loads(body))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid payload: {str(e)}")
    
    # Queue the event for background processing
    background_tasks.add_task(process_webhook_event, event)
    
    return {"status": "accepted"}

async def process_webhook_event(event: WebhookEvent):
    """
    Process a webhook event in the background.
    """
    # Handle comment.deleted events
    if event.event_type == "comment.deleted":
        handle_comment_deleted(event)
        return
    
    # Only process comment.created events
    if event.event_type != "comment.created":
        return
    
    # Check for duplicate event_id (idempotency) - use INSERT OR IGNORE for atomicity
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO processed_events (event_id) VALUES (?)
            """, (event.event_id,))
            
            # Check if this was a new insert (changes == 1 means inserted, 0 means already existed)
            if cursor.rowcount == 0:
                return  # Already processed this event
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"Error checking processed_events: {e}")
            return
    
    # Extract comment data
    comment_id = event.data.get("comment_id")
    user_id = event.data.get("from", {}).get("user_id")
    text = event.data.get("text", "")
    
    if not user_id or not text:
        return
    
    # Find matching rules
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, keyword, dm_message FROM rules")
        rules = cursor.fetchall()
    
    text_lower = text.lower()
    
    for rule in rules:
        rule_id = rule["id"]
        keyword = rule["keyword"].lower()
        dm_message = rule["dm_message"]
        
        # Check if keyword matches (case-insensitive, anywhere in text)
        if keyword in text_lower:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                try:
                    # Check if we already sent this user a DM for this rule
                    cursor.execute("""
                        SELECT 1 FROM sent_dms 
                        WHERE rule_id = ? AND user_id = ? AND status IN ('queued', 'delivered')
                    """, (rule_id, user_id))
                    if cursor.fetchone():
                        # Duplicate - user already getting/was sent DM for this rule
                        cursor.execute("""
                            INSERT OR IGNORE INTO sent_dms (rule_id, user_id, comment_id, event_id, status)
                            VALUES (?, ?, ?, ?, 'duplicate_blocked')
                        """, (rule_id, user_id, comment_id, event.event_id))
                        conn.commit()
                        continue
                    
                    # Try to insert - UNIQUE constraint will prevent duplicates
                    cursor.execute("""
                        INSERT OR IGNORE INTO sent_dms (rule_id, user_id, comment_id, event_id, status)
                        VALUES (?, ?, ?, ?, 'queued')
                    """, (rule_id, user_id, comment_id, event.event_id))
                    
                    if cursor.rowcount == 0:
                        continue  # Already exists (duplicate comment for this rule)
                    
                    dm_db_id = cursor.lastrowid
                    conn.commit()
                    
                    # Send DM in background
                    asyncio.create_task(send_dm_async(dm_db_id, user_id, dm_message, comment_id))
                except Exception as e:
                    conn.rollback()
                    print(f"Error processing rule {rule_id}: {e}")
                    continue

def handle_comment_deleted(event: WebhookEvent):
    """
    Handle comment.deleted events.
    If we haven't sent the DM yet, cancel it.
    If we already sent it, we keep it (DM was already delivered).
    """
    comment_id = event.data.get("comment_id")
    
    if not comment_id:
        return
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Cancel any queued DMs for this comment
        cursor.execute("""
            UPDATE sent_dms 
            SET status = 'cancelled' 
            WHERE comment_id = ? AND status = 'queued'
        """, (comment_id,))
        conn.commit()

async def send_dm_async(dm_db_id: int, user_id: str, message: str, comment_id: str):
    """
    Send a DM asynchronously and handle retries.
    """
    success, dm_id, error = await send_dm(user_id, message, comment_id)
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        if success and dm_id:
            cursor.execute("""
                UPDATE sent_dms 
                SET dm_id = ?, status = 'queued', updated_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (dm_id, dm_db_id))
        else:
            # Initial send failed - will be retried
            cursor.execute("""
                UPDATE sent_dms 
                SET retry_count = retry_count + 1, updated_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (dm_db_id,))
            
            # If it's a rate limit or internal error, it stays queued for retry
            # If it's a permanent error (400), mark as failed
            if "invalid_request" in str(error):
                cursor.execute("""
                    UPDATE sent_dms SET status = 'failed', updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """, (dm_db_id,))
        
        conn.commit()

@app.get("/")
async def get_dashboard():
    """
    Serve the dashboard UI.
    """
    from fastapi.responses import HTMLResponse
    with open("templates/index.html", "r") as f:
        return HTMLResponse(content=f.read())

@app.get("/rules-list")
async def list_rules():
    """
    List all rules for the dashboard.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, keyword, dm_message FROM rules ORDER BY created_at DESC")
        rules = cursor.fetchall()
    
    return [
        {"rule_id": row["id"], "keyword": row["keyword"], "dm_message": row["dm_message"]}
        for row in rules
    ]

@app.post("/rules")
async def create_rule(rule: RuleCreate):
    """
    Create a new rule.
    """
    import uuid
    rule_id = f"rule_{uuid.uuid4().hex[:12]}"
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO rules (id, keyword, dm_message)
            VALUES (?, ?, ?)
        """, (rule_id, rule.keyword, rule.dm_message))
        conn.commit()
    
    return RuleResponse(rule_id=rule_id, keyword=rule.keyword, dm_message=rule.dm_message)

@app.get("/stats")
async def get_stats():
    """
    Get statistics about DM sending.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Count sent (delivered)
        cursor.execute("SELECT COUNT(*) FROM sent_dms WHERE status = 'delivered'")
        sent = cursor.fetchone()[0]
        
        # Count failed
        cursor.execute("SELECT COUNT(*) FROM sent_dms WHERE status = 'failed'")
        failed = cursor.fetchone()[0]
        
        # Count queued (waiting to send or retry)
        cursor.execute("SELECT COUNT(*) FROM sent_dms WHERE status = 'queued'")
        queued = cursor.fetchone()[0]
        
        # Count duplicates blocked
        cursor.execute("SELECT COUNT(*) FROM sent_dms WHERE status = 'duplicate_blocked'")
        duplicates_blocked = cursor.fetchone()[0]
    
    return StatsResponse(
        sent=sent,
        failed=failed,
        queued=queued,
        duplicates_blocked=duplicates_blocked
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
