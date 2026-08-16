import sqlite3
import threading
from contextlib import contextmanager
from typing import Optional

DATABASE_URL = "linkplease.db"

_local = threading.local()

def get_db():
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(DATABASE_URL, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn

@contextmanager
def get_db_connection():
    conn = sqlite3.connect(DATABASE_URL)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rules (
                id TEXT PRIMARY KEY,
                keyword TEXT NOT NULL,
                dm_message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sent_dms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                comment_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                dm_id TEXT,
                status TEXT DEFAULT 'queued',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                retry_count INTEGER DEFAULT 0,
                UNIQUE(rule_id, user_id, comment_id)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processed_events (
                event_id TEXT PRIMARY KEY,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_sent_dms_status ON sent_dms(status)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_sent_dms_rule_user ON sent_dms(rule_id, user_id)
        """)
        
        conn.commit()
