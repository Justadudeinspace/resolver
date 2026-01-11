import json
import logging
import os
import sqlite3
import time
import uuid
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
    default_goal TEXT,
    default_style TEXT,
    language TEXT DEFAULT 'en',
    language_mode TEXT DEFAULT 'clean',
    v2_enabled INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    meta_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS groups (
    group_id INTEGER PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    language TEXT NOT NULL DEFAULT 'en',
    language_mode TEXT NOT NULL DEFAULT 'clean',
    warn_threshold INTEGER NOT NULL DEFAULT 2,
    mute_threshold INTEGER NOT NULL DEFAULT 3,
    welcome_enabled INTEGER NOT NULL DEFAULT 0,
    welcome_text TEXT,
    rules_enabled INTEGER NOT NULL DEFAULT 0,
    rules_text TEXT,
    security_enabled INTEGER NOT NULL DEFAULT 0,
    security_config_json TEXT,
    created_at INTEGER DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS group_user_state (
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    violations INTEGER NOT NULL DEFAULT 0,
    last_ts INTEGER,
    PRIMARY KEY (group_id, user_id)
);

CREATE TABLE IF NOT EXISTS moderation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    trigger TEXT NOT NULL,
    decision_summary TEXT NOT NULL,
    action TEXT NOT NULL,
    meta_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS group_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    plan_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    start_ts INTEGER NOT NULL,
    end_ts INTEGER,
    stars_amount INTEGER NOT NULL,
    transaction_id TEXT NOT NULL UNIQUE,
    created_at INTEGER DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS invoices (
    invoice_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    plan_id TEXT NOT NULL,
    amount INTEGER NOT NULL,
    currency TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    telegram_charge_id TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS audit_events (
    event_id TEXT PRIMARY KEY,
    ts INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    actor_user_id INTEGER NOT NULL,
    target_user_id INTEGER,
    action TEXT NOT NULL,
    reason TEXT,
    metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_embeddings (
    event_id TEXT PRIMARY KEY,
    embedding_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(event_id) REFERENCES audit_events(event_id)
);
"""


class DB:
    def __init__(self, path: Optional[str] = None):
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
            conn.execute("PRAGMA busy_timeout=30000;")

            conn.executescript(SCHEMA)
            self._ensure_user_columns(conn)
            self._ensure_feedback_columns(conn)
            self._ensure_group_columns(conn)
            conn.commit()
            self._initialized = True
            logger.info("Database initialized with WAL mode")
        except Exception as exc:
            logger.error("Failed to initialize database: %s", exc)
            raise
        finally:
            conn.close()

    def _ensure_user_columns(self, conn: sqlite3.Connection) -> None:
        cursor = conn.execute("PRAGMA table_info(users)")
        existing = {row[1] for row in cursor.fetchall()}
        missing_columns = {
            "default_goal": "TEXT",
            "default_style": "TEXT",
            "language": "TEXT DEFAULT 'en'",
            "language_mode": "TEXT DEFAULT 'clean'",
            "v2_enabled": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, column_type in missing_columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE users ADD COLUMN {column} {column_type}")
                logger.info("Added missing column to users: %s", column)

        conn.execute("UPDATE users SET language = 'en' WHERE language IS NULL")
        conn.execute("UPDATE users SET language_mode = 'clean' WHERE language_mode IS NULL")
        conn.execute("UPDATE users SET v2_enabled = 0 WHERE v2_enabled IS NULL")

    def _ensure_feedback_columns(self, conn: sqlite3.Connection) -> None:
        cursor = conn.execute("PRAGMA table_info(feedback)")
        existing = {row[1] for row in cursor.fetchall()}
        missing_columns = {
            "ts": "INTEGER",
            "text": "TEXT",
            "meta_json": "TEXT",
        }
        for column, column_type in missing_columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE feedback ADD COLUMN {column} {column_type}")
                logger.info("Added missing column to feedback: %s", column)

        if "message" in existing:
            conn.execute(
                "UPDATE feedback SET text = message WHERE text IS NULL AND message IS NOT NULL"
            )
        conn.execute(
            "UPDATE feedback SET meta_json = '{}' WHERE meta_json IS NULL"
        )
        conn.execute(
            "UPDATE feedback SET ts = CAST(strftime('%s','now') AS INTEGER) WHERE ts IS NULL"
        )

    def _ensure_group_columns(self, conn: sqlite3.Connection) -> None:
        cursor = conn.execute("PRAGMA table_info(groups)")
        existing = {row[1] for row in cursor.fetchall()}
        if not existing:
            return
        missing_columns = {
            "enabled": "INTEGER NOT NULL DEFAULT 0",
            "language": "TEXT NOT NULL DEFAULT 'en'",
            "language_mode": "TEXT NOT NULL DEFAULT 'clean'",
            "warn_threshold": "INTEGER NOT NULL DEFAULT 2",
            "mute_threshold": "INTEGER NOT NULL DEFAULT 3",
            "welcome_enabled": "INTEGER NOT NULL DEFAULT 0",
            "welcome_text": "TEXT",
            "rules_enabled": "INTEGER NOT NULL DEFAULT 0",
            "rules_text": "TEXT",
            "security_enabled": "INTEGER NOT NULL DEFAULT 0",
            "security_config_json": "TEXT",
        }
        for column, column_type in missing_columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE groups ADD COLUMN {column} {column_type}")
                logger.info("Added missing column to groups: %s", column)

        conn.execute("UPDATE groups SET enabled = 0 WHERE enabled IS NULL")
        conn.execute("UPDATE groups SET language = 'en' WHERE language IS NULL")
        conn.execute("UPDATE groups SET language_mode = 'clean' WHERE language_mode IS NULL")
        conn.execute("UPDATE groups SET warn_threshold = 2 WHERE warn_threshold IS NULL")
        conn.execute("UPDATE groups SET mute_threshold = 3 WHERE mute_threshold IS NULL")
        conn.execute("UPDATE groups SET welcome_enabled = 0 WHERE welcome_enabled IS NULL")
        conn.execute("UPDATE groups SET welcome_text = '' WHERE welcome_text IS NULL")
        conn.execute("UPDATE groups SET rules_enabled = 0 WHERE rules_enabled IS NULL")
        conn.execute("UPDATE groups SET rules_text = '' WHERE rules_text IS NULL")
        conn.execute("UPDATE groups SET security_enabled = 0 WHERE security_enabled IS NULL")
        conn.execute("UPDATE groups SET security_config_json = '{}' WHERE security_config_json IS NULL")

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

    def _ensure_group_conn(self, conn: sqlite3.Connection, group_id: int) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO groups (group_id) VALUES (?)",
            (group_id,),
        )

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

    def get_defaults(self, user_id: int) -> Dict[str, Optional[str]]:
        """Get default goal and style for a user"""
        with self._conn() as conn:
            self._ensure_user_conn(conn, user_id)
            cursor = conn.execute(
                "SELECT default_goal, default_style FROM users WHERE user_id = ?",
                (user_id,),
            )
            row = cursor.fetchone()
            if not row:
                return {"default_goal": None, "default_style": None}
            return {
                "default_goal": row["default_goal"],
                "default_style": row["default_style"],
            }

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

    def set_default_goal(self, user_id: int, goal: Optional[str]) -> None:
        """Set or clear the default goal for a user"""
        with self._conn() as conn:
            self._ensure_user_conn(conn, user_id)
            conn.execute(
                "UPDATE users SET default_goal = ? WHERE user_id = ?",
                (goal, user_id),
            )

    def set_default_style(self, user_id: int, style: Optional[str]) -> None:
        """Set or clear the default style for a user"""
        with self._conn() as conn:
            self._ensure_user_conn(conn, user_id)
            conn.execute(
                "UPDATE users SET default_style = ? WHERE user_id = ?",
                (style, user_id),
            )

    def set_language(self, user_id: int, language: str) -> None:
        """Set preferred language for a user"""
        with self._conn() as conn:
            self._ensure_user_conn(conn, user_id)
            conn.execute(
                "UPDATE users SET language = ? WHERE user_id = ?",
                (language, user_id),
            )

    def set_language_mode(self, user_id: int, language_mode: str) -> None:
        """Set preferred language mode for a user"""
        with self._conn() as conn:
            self._ensure_user_conn(conn, user_id)
            conn.execute(
                "UPDATE users SET language_mode = ? WHERE user_id = ?",
                (language_mode, user_id),
            )

    def set_v2_enabled(self, user_id: int, enabled: bool) -> None:
        """Enable or disable v2 features for a user"""
        with self._conn() as conn:
            self._ensure_user_conn(conn, user_id)
            conn.execute(
                "UPDATE users SET v2_enabled = ? WHERE user_id = ?",
                (1 if enabled else 0, user_id),
            )

    def ensure_group(self, group_id: int) -> None:
        """Ensure group exists"""
        with self._conn() as conn:
            self._ensure_group_conn(conn, group_id)

    def get_group(self, group_id: int) -> Dict[str, Any]:
        """Get group settings"""
        with self._conn() as conn:
            self._ensure_group_conn(conn, group_id)
            cursor = conn.execute(
                "SELECT * FROM groups WHERE group_id = ?",
                (group_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else {}

    def get_group_settings(self, group_id: int) -> Dict[str, Any]:
        """Get group settings with defaults applied."""
        defaults = {
            "enabled": 0,
            "language": "en",
            "language_mode": "clean",
            "warn_threshold": 2,
            "mute_threshold": 3,
            "welcome_enabled": 0,
            "welcome_text": "",
            "rules_enabled": 0,
            "rules_text": "",
            "security_enabled": 0,
            "security_config_json": "{}",
        }
        group = self.get_group(group_id)
        for key, value in defaults.items():
            if group.get(key) is None:
                group[key] = value
        return group

    def set_group_enabled(self, group_id: int, enabled: bool) -> None:
        with self._conn() as conn:
            self._ensure_group_conn(conn, group_id)
            conn.execute(
                "UPDATE groups SET enabled = ? WHERE group_id = ?",
                (1 if enabled else 0, group_id),
            )

    def set_group_language(self, group_id: int, language: str) -> None:
        with self._conn() as conn:
            self._ensure_group_conn(conn, group_id)
            conn.execute(
                "UPDATE groups SET language = ? WHERE group_id = ?",
                (language, group_id),
            )

    def set_group_language_mode(self, group_id: int, language_mode: str) -> None:
        with self._conn() as conn:
            self._ensure_group_conn(conn, group_id)
            conn.execute(
                "UPDATE groups SET language_mode = ? WHERE group_id = ?",
                (language_mode, group_id),
            )

    def set_group_thresholds(self, group_id: int, warn_threshold: int, mute_threshold: int) -> None:
        with self._conn() as conn:
            self._ensure_group_conn(conn, group_id)
            conn.execute(
                "UPDATE groups SET warn_threshold = ?, mute_threshold = ? WHERE group_id = ?",
                (warn_threshold, mute_threshold, group_id),
            )

    def set_group_toggle(self, group_id: int, field: str, enabled: bool) -> None:
        if field not in {"welcome_enabled", "rules_enabled", "security_enabled"}:
            raise ValueError("Invalid group toggle field")
        with self._conn() as conn:
            self._ensure_group_conn(conn, group_id)
            conn.execute(
                f"UPDATE groups SET {field} = ? WHERE group_id = ?",
                (1 if enabled else 0, group_id),
            )

    def set_group_welcome_text(self, group_id: int, welcome_text: str) -> None:
        with self._conn() as conn:
            self._ensure_group_conn(conn, group_id)
            conn.execute(
                "UPDATE groups SET welcome_text = ? WHERE group_id = ?",
                (welcome_text, group_id),
            )

    def set_group_rules_text(self, group_id: int, rules_text: str) -> None:
        with self._conn() as conn:
            self._ensure_group_conn(conn, group_id)
            conn.execute(
                "UPDATE groups SET rules_text = ? WHERE group_id = ?",
                (rules_text, group_id),
            )

    def set_group_security_config(self, group_id: int, config_json: str) -> None:
        with self._conn() as conn:
            self._ensure_group_conn(conn, group_id)
            conn.execute(
                "UPDATE groups SET security_config_json = ? WHERE group_id = ?",
                (config_json, group_id),
            )

    def increment_violations(self, group_id: int, user_id: int, ts: int) -> int:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO group_user_state (group_id, user_id, violations, last_ts)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(group_id, user_id)
                DO UPDATE SET violations = violations + 1, last_ts = excluded.last_ts
                """,
                (group_id, user_id, ts),
            )
            cursor = conn.execute(
                "SELECT violations FROM group_user_state WHERE group_id = ? AND user_id = ?",
                (group_id, user_id),
            )
            row = cursor.fetchone()
            return int(row["violations"]) if row else 0

    def record_moderation_log(
        self,
        group_id: int,
        user_id: int,
        trigger: str,
        decision_summary: str,
        action: str,
        meta_json: str,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO moderation_log (ts, group_id, user_id, trigger, decision_summary, action, meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (int(datetime.utcnow().timestamp()), group_id, user_id, trigger, decision_summary, action, meta_json),
            )

    def get_group_logs(self, group_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM moderation_log
                WHERE group_id = ?
                ORDER BY ts DESC
                LIMIT ?
                """,
                (group_id, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    def record_audit_event(
        self,
        chat_id: int,
        actor_user_id: int,
        action: str,
        target_user_id: Optional[int] = None,
        reason: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        event_id = str(uuid.uuid4())
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO audit_events
                (event_id, ts, chat_id, actor_user_id, target_user_id, action, reason, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    int(datetime.utcnow().timestamp()),
                    chat_id,
                    actor_user_id,
                    target_user_id,
                    action,
                    reason,
                    meta_json,
                ),
            )
        return event_id

    def get_audit_event(self, chat_id: int, event_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM audit_events
                WHERE chat_id = ? AND event_id = ?
                """,
                (chat_id, event_id),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_audit_events(
        self,
        chat_id: int,
        since_ts: Optional[int] = None,
        action_filter: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        params: List[Any] = [chat_id]
        conditions = ["chat_id = ?"]
        if since_ts is not None:
            conditions.append("ts >= ?")
            params.append(since_ts)
        if action_filter:
            conditions.append("action = ?")
            params.append(action_filter)

        where_clause = " AND ".join(conditions)
        query = (
            "SELECT * FROM audit_events "
            f"WHERE {where_clause} "
            "ORDER BY ts DESC LIMIT ?"
        )
        params.append(limit)
        with self._conn() as conn:
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def search_audit_events(
        self,
        chat_id: int,
        query_text: str,
        since_ts: Optional[int] = None,
        action_filter: Optional[str] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        tokens = [token for token in query_text.lower().split() if token]
        if not tokens:
            return self.get_audit_events(chat_id, since_ts=since_ts, action_filter=action_filter, limit=limit)

        params: List[Any] = [chat_id]
        conditions = ["chat_id = ?"]
        if since_ts is not None:
            conditions.append("ts >= ?")
            params.append(since_ts)
        if action_filter:
            conditions.append("action = ?")
            params.append(action_filter)

        like_clauses = []
        for token in tokens[:5]:
            like_clauses.append("(action LIKE ? OR reason LIKE ? OR metadata_json LIKE ?)")
            pattern = f"%{token}%"
            params.extend([pattern, pattern, pattern])
        where_clause = " AND ".join(conditions + [f"({' OR '.join(like_clauses)})"])
        query = (
            "SELECT * FROM audit_events "
            f"WHERE {where_clause} "
            "ORDER BY ts DESC LIMIT ?"
        )
        params.append(limit)
        with self._conn() as conn:
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_audit_embeddings(self, event_ids: List[str]) -> Dict[str, List[float]]:
        if not event_ids:
            return {}
        placeholders = ",".join("?" for _ in event_ids)
        query = f"SELECT event_id, embedding_json FROM audit_embeddings WHERE event_id IN ({placeholders})"
        with self._conn() as conn:
            cursor = conn.execute(query, event_ids)
            results = {}
            for row in cursor.fetchall():
                try:
                    results[row["event_id"]] = json.loads(row["embedding_json"])
                except json.JSONDecodeError:
                    continue
            return results

    def add_audit_embedding(self, event_id: str, embedding: List[float]) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO audit_embeddings (event_id, embedding_json, created_at)
                VALUES (?, ?, ?)
                """,
                (event_id, json.dumps(embedding), int(time.time())),
            )

    def add_group_subscription(
        self,
        group_id: int,
        plan_id: str,
        stars_amount: int,
        transaction_id: str,
        start_ts: int,
        end_ts: Optional[int],
    ) -> bool:
        with self._conn() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO group_subscriptions
                (group_id, plan_id, stars_amount, transaction_id, start_ts, end_ts, status)
                VALUES (?, ?, ?, ?, ?, ?, 'active')
                """,
                (group_id, plan_id, stars_amount, transaction_id, start_ts, end_ts),
            )
            return cursor.rowcount == 1

    def create_invoice(
        self,
        invoice_id: str,
        user_id: int,
        plan_id: str,
        amount: int,
        currency: str,
    ) -> bool:
        with self._conn() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO invoices
                (invoice_id, user_id, plan_id, amount, currency, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'created', ?)
                """,
                (invoice_id, user_id, plan_id, amount, currency, int(time.time())),
            )
            return cursor.rowcount == 1

    def get_invoice(self, invoice_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM invoices WHERE invoice_id = ?",
                (invoice_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def process_invoice_payment(
        self,
        invoice_id: str,
        telegram_charge_id: str,
        user_id: int,
        stars_amount: int,
        resolves_added: int,
    ) -> str:
        with self._conn() as conn:
            if not telegram_charge_id:
                logger.warning("Missing Telegram charge id for invoice %s", invoice_id)
                return "invalid"

            cursor = conn.execute(
                "SELECT 1 FROM invoices WHERE telegram_charge_id = ?",
                (telegram_charge_id,),
            )
            if cursor.fetchone():
                return "duplicate"

            cursor = conn.execute(
                "SELECT 1 FROM purchases WHERE transaction_id = ?",
                (telegram_charge_id,),
            )
            if cursor.fetchone():
                return "duplicate"

            row = conn.execute(
                "SELECT status FROM invoices WHERE invoice_id = ?",
                (invoice_id,),
            ).fetchone()
            if not row or row["status"] != "created":
                return "invalid"

            cursor = conn.execute(
                """
                UPDATE invoices
                SET status = 'paid', telegram_charge_id = ?
                WHERE invoice_id = ? AND status = 'created'
                """,
                (telegram_charge_id, invoice_id),
            )
            if cursor.rowcount != 1:
                return "invalid"

            self._ensure_user_conn(conn, user_id)
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO purchases
                (user_id, stars_amount, resolves_added, transaction_id)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, stars_amount, resolves_added, telegram_charge_id),
            )
            if cursor.rowcount == 0:
                return "duplicate"

            conn.execute(
                "UPDATE users SET resolves_remaining = resolves_remaining + ? WHERE user_id = ?",
                (resolves_added, user_id),
            )
            return "processed"

    def process_group_invoice_payment(
        self,
        invoice_id: str,
        telegram_charge_id: str,
        group_id: int,
        plan_id: str,
        stars_amount: int,
        start_ts: int,
        end_ts: Optional[int],
    ) -> str:
        with self._conn() as conn:
            if not telegram_charge_id:
                logger.warning("Missing Telegram charge id for group invoice %s", invoice_id)
                return "invalid"

            cursor = conn.execute(
                "SELECT 1 FROM invoices WHERE telegram_charge_id = ?",
                (telegram_charge_id,),
            )
            if cursor.fetchone():
                return "duplicate"

            cursor = conn.execute(
                "SELECT 1 FROM group_subscriptions WHERE transaction_id = ?",
                (telegram_charge_id,),
            )
            if cursor.fetchone():
                return "duplicate"

            row = conn.execute(
                "SELECT status FROM invoices WHERE invoice_id = ?",
                (invoice_id,),
            ).fetchone()
            if not row or row["status"] != "created":
                return "invalid"

            cursor = conn.execute(
                """
                UPDATE invoices
                SET status = 'paid', telegram_charge_id = ?
                WHERE invoice_id = ? AND status = 'created'
                """,
                (telegram_charge_id, invoice_id),
            )
            if cursor.rowcount != 1:
                return "invalid"

            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO group_subscriptions
                (group_id, plan_id, stars_amount, transaction_id, start_ts, end_ts, status)
                VALUES (?, ?, ?, ?, ?, ?, 'active')
                """,
                (group_id, plan_id, stars_amount, telegram_charge_id, start_ts, end_ts),
            )
            if cursor.rowcount == 0:
                return "duplicate"

            return "processed"

    def group_subscription_active(self, group_id: int) -> bool:
        now = int(datetime.utcnow().timestamp())
        with self._conn() as conn:
            cursor = conn.execute(
                """
                SELECT 1 FROM group_subscriptions
                WHERE group_id = ?
                AND status = 'active'
                AND (end_ts IS NULL OR end_ts > ?)
                ORDER BY start_ts DESC
                LIMIT 1
                """,
                (group_id, now),
            )
            return cursor.fetchone() is not None

    def get_group_subscription_info(self, group_id: int) -> Dict[str, Any]:
        now = int(datetime.utcnow().timestamp())
        with self._conn() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM group_subscriptions
                WHERE group_id = ?
                ORDER BY start_ts DESC
                LIMIT 1
                """,
                (group_id,),
            )
            row = cursor.fetchone()
            if not row:
                return {"active": False, "end_ts": None, "plan_id": None}
            data = dict(row)
            active = data.get("status") == "active" and (
                data.get("end_ts") is None or data.get("end_ts") > now
            )
            return {
                "active": active,
                "end_ts": data.get("end_ts"),
                "plan_id": data.get("plan_id"),
            }

    def add_feedback(self, user_id: int, text: str, meta_json: str) -> None:
        """Record user feedback"""
        with self._conn() as conn:
            self._ensure_user_conn(conn, user_id)
            conn.execute(
                "INSERT INTO feedback (ts, user_id, text, meta_json) VALUES (?, ?, ?, ?)",
                (int(datetime.utcnow().timestamp()), user_id, text, meta_json),
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
                    "INSERT OR IGNORE INTO purchases (user_id, stars_amount, resolves_added, transaction_id) "
                    "VALUES (?, ?, ?, ?)",
                    (user_id, stars_amount, resolves_added, transaction_id),
                )
                if cursor.rowcount == 0:
                    logger.warning("Transaction already processed")
                    return False

            conn.execute(
                "UPDATE users SET resolves_remaining = resolves_remaining + ? WHERE user_id = ?",
                (resolves_added, user_id),
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
                        logger.warning(
                            "Could not parse created_at for user %s", user_id
                        )

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
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "users",
                        "retry_flags",
                        "interactions",
                        "purchases",
                        "feedback",
                        "groups",
                        "group_user_state",
                        "moderation_log",
                        "group_subscriptions",
                        "invoices",
                    ),
                )
                tables_found = {row["name"] for row in cursor.fetchall()}
                return len(tables_found) == 10
        except Exception as exc:
            logger.error("Database health check failed: %s", exc)
            return False
