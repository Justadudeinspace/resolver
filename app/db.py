import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from typing import Optional, Dict, Any, List

from .config import settings

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    resolves_remaining INTEGER NOT NULL DEFAULT 0,
    free_used_date TEXT,
    current_goal TEXT,
    last_input_text TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS retry_flags (
    user_id INTEGER PRIMARY KEY,
    last_resolve_was_paid INTEGER NOT NULL DEFAULT 0,
    free_retry_available INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    goal TEXT NOT NULL,
    input_text TEXT,
    output_options TEXT,
    used_paid INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    stars_amount INTEGER NOT NULL,
    resolves_added INTEGER NOT NULL,
    transaction_id TEXT UNIQUE,
    status TEXT DEFAULT 'completed',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(user_id)
);
"""


class DB:
    def __init__(self, path: str = None):
        self.path = path or settings.db_path
        self._initialized = False

    def _ensure_directory(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)

    def _init_db(self) -> None:
        """Initialize database with schema and persistent settings"""
        if self._initialized:
            return

        self._ensure_directory()

        conn = sqlite3.connect(self.path, timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA foreign_keys=ON;")

            conn.executescript(SCHEMA)
            conn.commit()
            self._initialized = True
            logger.info("Database initialized with WAL mode")
        except Exception as exc:
            logger.error("Failed to initialize database: %s", exc)
            raise
        finally:
            conn.close()

    @contextmanager
    def _conn(self):
        """Context manager for database connections with Termux optimizations"""
        if not self._initialized:
            self._init_db()

        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA busy_timeout=30000;")

            yield conn
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.error("Database error: %s", exc)
            raise
        finally:
            conn.close()

    def _ensure_user_conn(
        self,
        conn: sqlite3.Connection,
        user_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
    ) -> None:
        """Ensure user exists using the provided connection (no nested connections)"""
        cursor = conn.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if cursor.fetchone() is None:
            conn.execute(
                "INSERT INTO users (user_id, username, first_name, last_name, resolves_remaining) VALUES (?, ?, ?, ?, 0)",
                (user_id, username, first_name, last_name),
            )
            conn.execute(
                "INSERT INTO retry_flags (user_id, last_resolve_was_paid, free_retry_available) VALUES (?, ?, ?)",
                (user_id, 0, 0),
            )
        else:
            updates = []
            params = []
            if username is not None:
                updates.append("username = ?")
                params.append(username)
            if first_name is not None:
                updates.append("first_name = ?")
                params.append(first_name)
            if last_name is not None:
                updates.append("last_name = ?")
                params.append(last_name)

            if updates:
                params.append(user_id)
                query = f"UPDATE users SET {', '.join(updates)} WHERE user_id = ?"
                conn.execute(query, params)

    def ensure_user(
        self,
        user_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
    ) -> None:
        """Public method to ensure user exists"""
        with self._conn() as conn:
            self._ensure_user_conn(conn, user_id, username, first_name, last_name)

    def get_user(self, user_id: int) -> Dict[str, Any]:
        """Get user data"""
        with self._conn() as conn:
            self._ensure_user_conn(conn, user_id)
            cursor = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else {}

    def set_goal(self, user_id: int, goal: str) -> None:
        """Set current goal for user"""
        with self._conn() as conn:
            self._ensure_user_conn(conn, user_id)
            conn.execute(
                "UPDATE users SET current_goal = ? WHERE user_id = ?",
                (goal, user_id),
            )

    def set_last_input(self, user_id: int, text: str) -> None:
        """Store last input text"""
        with self._conn() as conn:
            self._ensure_user_conn(conn, user_id)
            conn.execute(
                "UPDATE users SET last_input_text = ? WHERE user_id = ?",
                (text, user_id),
            )

    def get_retry_flags(self, user_id: int) -> Dict[str, bool]:
        """Get retry flags for user"""
        with self._conn() as conn:
            self._ensure_user_conn(conn, user_id)
            cursor = conn.execute(
                "SELECT last_resolve_was_paid, free_retry_available FROM retry_flags WHERE user_id = ?",
                (user_id,),
            )
            row = cursor.fetchone()
            if row:
                return {
                    "last_resolve_was_paid": bool(row["last_resolve_was_paid"]),
                    "free_retry_available": bool(row["free_retry_available"]),
                }
            return {"last_resolve_was_paid": False, "free_retry_available": False}

    def set_retry_flags(self, user_id: int, last_paid: bool, free_retry: bool) -> None:
        """Update retry flags"""
        with self._conn() as conn:
            self._ensure_user_conn(conn, user_id)
            conn.execute(
                "UPDATE retry_flags SET last_resolve_was_paid = ?, free_retry_available = ? WHERE user_id = ?",
                (1 if last_paid else 0, 1 if free_retry else 0, user_id),
            )

    def add_resolves(
        self,
        user_id: int,
        stars_amount: int,
        resolves_added: int,
        transaction_id: Optional[str] = None,
    ) -> bool:
        """Add resolves to user account and log purchase. Returns True if successful."""
        with self._conn() as conn:
            self._ensure_user_conn(conn, user_id)

            if transaction_id:
                cursor = conn.execute(
                    "SELECT id FROM purchases WHERE transaction_id = ?",
                    (transaction_id,),
                )
                if cursor.fetchone() is not None:
                    logger.warning("Transaction %s already processed", transaction_id)
                    return False

            conn.execute(
                "UPDATE users SET resolves_remaining = resolves_remaining + ? WHERE user_id = ?",
                (resolves_added, user_id),
            )

            if transaction_id:
                conn.execute(
                    """INSERT OR IGNORE INTO purchases
                    (user_id, stars_amount, resolves_added, transaction_id)
                    VALUES (?, ?, ?, ?)""",
                    (user_id, stars_amount, resolves_added, transaction_id),
                )

            return True

    def consume_paid_resolve(self, user_id: int) -> bool:
        """Consume one paid resolve. Returns True if successful."""
        with self._conn() as conn:
            self._ensure_user_conn(conn, user_id)

            cursor = conn.execute(
                "UPDATE users SET resolves_remaining = resolves_remaining - 1 "
                "WHERE user_id = ? AND resolves_remaining > 0",
                (user_id,),
            )
            return cursor.rowcount == 1

    def can_use_free_today(self, user_id: int) -> bool:
        """Check if free resolve is available today"""
        today = date.today().isoformat()
        with self._conn() as conn:
            self._ensure_user_conn(conn, user_id)

            cursor = conn.execute(
                "SELECT free_used_date FROM users WHERE user_id = ?",
                (user_id,),
            )
            row = cursor.fetchone()
            if not row:
                return True
            return row["free_used_date"] != today

    def mark_free_used_today(self, user_id: int) -> None:
        """Mark free resolve as used today"""
        today = date.today().isoformat()
        with self._conn() as conn:
            self._ensure_user_conn(conn, user_id)
            conn.execute(
                "UPDATE users SET free_used_date = ? WHERE user_id = ?",
                (today, user_id),
            )

    def log_interaction(
        self,
        user_id: int,
        goal: str,
        input_text: str,
        output_options: List[str],
        used_paid: bool,
    ) -> None:
        """Log an interaction"""
        with self._conn() as conn:
            self._ensure_user_conn(conn, user_id)
            conn.execute(
                """INSERT INTO interactions
                (user_id, goal, input_text, output_options, used_paid)
                VALUES (?, ?, ?, ?, ?)""",
                (user_id, goal, input_text[:1000], json.dumps(output_options), 1 if used_paid else 0),
            )

    def get_user_stats(self, user_id: int) -> Dict[str, Any]:
        """Get user statistics"""
        with self._conn() as conn:
            self._ensure_user_conn(conn, user_id)

            cursor = conn.execute(
                "SELECT COUNT(*) as count FROM interactions WHERE user_id = ?",
                (user_id,),
            )
            total = cursor.fetchone()["count"]

            cursor = conn.execute(
                "SELECT COUNT(*) as count FROM interactions WHERE user_id = ? AND used_paid = 1",
                (user_id,),
            )
            paid = cursor.fetchone()["count"]

            cursor = conn.execute(
                "SELECT created_at FROM users WHERE user_id = ?",
                (user_id,),
            )
            row = cursor.fetchone()
            age_days = 0
            if row and row["created_at"]:
                try:
                    created = datetime.strptime(str(row["created_at"]), "%Y-%m-%d %H:%M:%S")
                    age_days = (datetime.utcnow() - created).days
                except ValueError:
                    try:
                        created = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
                        age_days = (datetime.utcnow() - created).days
                    except ValueError:
                        logger.warning("Could not parse created_at for user %s: %s", user_id, row["created_at"])

            return {
                "total_interactions": total,
                "paid_interactions": paid,
                "free_interactions": total - paid,
                "account_age_days": max(0, age_days),
            }

    def health_check(self) -> bool:
        """Check if database is accessible and tables exist"""
        try:
            with self._conn() as conn:
                cursor = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?, ?, ?, ?)",
                    ("users", "retry_flags", "interactions", "purchases"),
                )
                tables_found = {row["name"] for row in cursor.fetchall()}
                return len(tables_found) == 4
        except Exception as exc:
            logger.error("Database health check failed: %s", exc)
            return False
