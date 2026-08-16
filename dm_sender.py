import httpx
import asyncio
import time
from typing import Optional
from database import get_db_connection
from config import settings

class RateLimiter:
    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests = []
        self._lock = asyncio.Lock()
    
    async def acquire(self):
        async with self._lock:
            now = time.time()
            # Remove old requests outside the window
            self.requests = [req_time for req_time in self.requests if now - req_time < self.window_seconds]
            
            if len(self.requests) >= self.max_requests:
                # Wait until we can make a request
                oldest = self.requests[0]
                wait_time = self.window_seconds - (now - oldest)
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
                    # Clean up old requests after waiting
                    now = time.time()
                    self.requests = [req_time for req_time in self.requests if now - req_time < self.window_seconds]
            
            self.requests.append(now)

rate_limiter = RateLimiter()

async def send_dm(recipient_user_id: str, message: str, comment_id: str) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Send a DM via the PseudoGram API.
    Returns (success, dm_id, error_message)
    """
    await rate_limiter.acquire()
    
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

async def process_retry(dm_id: int, max_retries: int = 3):
    """
    Retry sending a DM that failed or is queued.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT rule_id, user_id, comment_id, dm_id as api_dm_id, retry_count
            FROM sent_dms WHERE id = ?
        """, (dm_id,))
        row = cursor.fetchone()
        
        if not row:
            return
        
        rule_id = row["rule_id"]
        user_id = row["user_id"]
        comment_id = row["comment_id"]
        api_dm_id = row["api_dm_id"]
        retry_count = row["retry_count"]
        
        if retry_count >= max_retries:
            # Give up
            cursor.execute("""
                UPDATE sent_dms SET status = 'failed', updated_at = CURRENT_TIMESTAMP WHERE id = ?
            """, (dm_id,))
            conn.commit()
            return
        
        # If we have an API dm_id, check its status first
        if api_dm_id:
            status = await check_dm_status(api_dm_id)
            if status == "delivered":
                cursor.execute("""
                    UPDATE sent_dms SET status = 'delivered', updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """, (dm_id,))
                conn.commit()
                return
            elif status == "failed":
                # Need to retry sending
                api_dm_id = None
        
        # Get the rule message
        cursor.execute("SELECT dm_message FROM rules WHERE id = ?", (rule_id,))
        rule_row = cursor.fetchone()
        if not rule_row:
            cursor.execute("""
                UPDATE sent_dms SET status = 'failed', updated_at = CURRENT_TIMESTAMP WHERE id = ?
            """, (dm_id,))
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
            """, (new_dm_id, dm_id))
        else:
            if "rate_limited" in str(error):
                # Schedule retry later - don't increment retry count for rate limits
                pass
            else:
                cursor.execute("""
                    UPDATE sent_dms 
                    SET retry_count = retry_count + 1, updated_at = CURRENT_TIMESTAMP 
                    WHERE id = ?
                """, (dm_id,))
                
                if retry_count + 1 >= max_retries:
                    cursor.execute("""
                        UPDATE sent_dms SET status = 'failed', updated_at = CURRENT_TIMESTAMP WHERE id = ?
                    """, (dm_id,))
        
        conn.commit()
