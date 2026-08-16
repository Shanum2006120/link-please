import httpx
import asyncio
import time
from typing import Optional
from database import get_db_connection
from config import settings

async def acquire_rate_limit() -> tuple[bool, Optional[int]]:
    """
    Acquire rate limit using database-backed tracking.
    Returns (can_proceed, retry_after_seconds)
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        now = time.time()
        window_start = now - 60  # 60 second window
        
        # Use a transaction to ensure atomicity
        cursor.execute("BEGIN")
        try:
            # Clean up old entries
            cursor.execute("DELETE FROM rate_limit WHERE window_start < ?", (window_start,))
            
            # Count current requests in window
            cursor.execute("SELECT COUNT(*) FROM rate_limit")
            count = cursor.fetchone()[0]
            
            if count >= 10:
                # Rate limited - find when the oldest request expires
                cursor.execute("SELECT MIN(window_start) FROM rate_limit")
                oldest = cursor.fetchone()[0]
                if oldest:
                    retry_after = int(60 - (now - oldest)) + 1
                    conn.rollback()
                    return False, retry_after
                else:
                    conn.rollback()
                    return False, 5
            
            # Add this request
            cursor.execute("INSERT INTO rate_limit (key, request_count, window_start) VALUES (?, 1, ?)", 
                         (f"req_{now}", now))
            conn.commit()
            return True, None
        except Exception as e:
            conn.rollback()
            return False, 5

async def send_dm(recipient_user_id: str, message: str, comment_id: str) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Send a DM via the PseudoGram API.
    Returns (success, dm_id, error_message)
    """
    can_proceed, retry_after = await acquire_rate_limit()
    if not can_proceed:
        return False, None, f"rate_limited: retry_after={retry_after}"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(
                f"{settings.pseudogram_api_url}/v1/dm/send",
                json={
                    "recipient_user_id": recipient_user_id,
                    "message": message,
                    "comment_id": comment_id
                },
                headers={
                    "X-API-Key": settings.api_key,
                    "Idempotency-Key": f"{recipient_user_id}_{comment_id}"
                }
            )
            
            if response.status_code == 202:
                data = response.json()
                return True, data.get("dm_id"), None
            elif response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "5")
                return False, None, f"rate_limited: retry_after={retry_after}"
            elif response.status_code == 500:
                return False, None, "internal_error"
            elif response.status_code == 400:
                return False, None, f"invalid_request: {response.json().get('detail', '')}"
            else:
                return False, None, f"unexpected_status: {response.status_code}"
                
        except httpx.TimeoutException:
            return False, None, "timeout"
        except httpx.RequestError as e:
            return False, None, f"request_error: {str(e)}"

async def check_dm_status(dm_id: str) -> Optional[str]:
    """
    Check the status of a DM.
    Returns status string or None if failed to check.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(
                f"{settings.pseudogram_api_url}/v1/dm/{dm_id}",
                headers={"X-API-Key": settings.api_key}
            )
            
            if response.status_code == 200:
                data = response.json()
                return data.get("status")
            return None
        except Exception:
            return None

def calculate_backoff(retry_count: int) -> int:
    """
    Calculate exponential backoff with jitter.
    """
    base = 2 ** retry_count
    max_backoff = 60
    backoff = min(base, max_backoff)
    # Add jitter: +/- 20%
    import random
    jitter = int(backoff * 0.2 * random.random())
    return backoff + jitter

async def process_retry(dm_db_id: int, max_retries: int = 5):
    """
    Retry sending a DM that failed or is queued with exponential backoff.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT rule_id, user_id, comment_id, dm_id as api_dm_id, retry_count, status
            FROM sent_dms WHERE id = ?
        """, (dm_db_id,))
        row = cursor.fetchone()
        
        if not row:
            return
        
        rule_id = row["rule_id"]
        user_id = row["user_id"]
        comment_id = row["comment_id"]
        api_dm_id = row["api_dm_id"]
        retry_count = row["retry_count"]
        status = row["status"]
        
        # Check if we're waiting for a scheduled retry
        if row["next_retry_at"]:
            import time
            if time.time() < row["next_retry_at"]:
                return  # Not time yet
        
        if retry_count >= max_retries:
            # Give up
            cursor.execute("""
                UPDATE sent_dms SET status = 'failed', updated_at = CURRENT_TIMESTAMP WHERE id = ?
            """, (dm_db_id,))
            conn.commit()
            return
        
        # If we have an API dm_id, check its status first
        if api_dm_id:
            api_status = await check_dm_status(api_dm_id)
            if api_status == "delivered":
                cursor.execute("""
                    UPDATE sent_dms SET status = 'delivered', updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """, (dm_db_id,))
                conn.commit()
                return
            elif api_status == "failed":
                # Need to retry sending
                api_dm_id = None
        
        # Get the rule message
        cursor.execute("SELECT dm_message FROM rules WHERE id = ?", (rule_id,))
        rule_row = cursor.fetchone()
        if not rule_row:
            cursor.execute("""
                UPDATE sent_dms SET status = 'failed', updated_at = CURRENT_TIMESTAMP WHERE id = ?
            """, (dm_db_id,))
            conn.commit()
            return
        
        message = rule_row["dm_message"]
        
        # Try to send
        success, new_dm_id, error = await send_dm(user_id, message, comment_id)
        
        if success and new_dm_id:
            cursor.execute("""
                UPDATE sent_dms 
                SET dm_id = ?, status = 'queued', retry_count = retry_count + 1, updated_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (new_dm_id, dm_db_id))
        else:
            if "rate_limited" in str(error):
                # Schedule retry later - don't increment retry count for rate limits
                import time
                retry_after = int(error.split("=")[1]) if "=" in str(error) else 5
                next_retry = time.time() + retry_after
                cursor.execute("""
                    UPDATE sent_dms 
                    SET next_retry_at = ?, updated_at = CURRENT_TIMESTAMP 
                    WHERE id = ?
                """, (next_retry, dm_db_id))
            else:
                new_retry_count = retry_count + 1
                backoff = calculate_backoff(new_retry_count)
                import time
                next_retry = time.time() + backoff
                
                cursor.execute("""
                    UPDATE sent_dms 
                    SET retry_count = ?, next_retry_at = ?, updated_at = CURRENT_TIMESTAMP 
                    WHERE id = ?
                """, (new_retry_count, next_retry, dm_db_id))
                
                if new_retry_count >= max_retries:
                    cursor.execute("""
                        UPDATE sent_dms SET status = 'failed', updated_at = CURRENT_TIMESTAMP WHERE id = ?
                    """, (dm_db_id,))
        
        conn.commit()
