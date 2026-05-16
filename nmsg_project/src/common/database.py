"""
nmsg SQLite Database Layer

Schema based on nmsg_db_design.md.
Uses WAL mode for better concurrency.
"""

import sqlite3
import threading
import uuid
import contextlib
from datetime import datetime
from typing import Optional
from .exceptions import NmsgError, AuthError


class Database:
    _local = threading.local()

    def __init__(self, db_path: str = "nmsg.db"):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Thread-local connection."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def close(self):
        conn = getattr(self._local, "conn", None)
        if conn:
            conn.close()
            self._local.conn = None

    def _init_db(self):
        """Create tables if they don't exist."""
        conn = self._get_conn()
        cursor = conn.cursor()

        # clients table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_name TEXT NOT NULL,
                access_token TEXT UNIQUE NOT NULL,
                ip_address TEXT NOT NULL,
                last_seen DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # messages table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER,
                receiver_id INTEGER,
                msg_type TEXT NOT NULL CHECK(msg_type IN ('Text', 'File', 'Packet')),
                content TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (sender_id) REFERENCES clients(id),
                FOREIGN KEY (receiver_id) REFERENCES clients(id)
            )
        """)

        # file_metadata table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS file_metadata (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                file_name TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                storage_path TEXT NOT NULL,
                checksum TEXT,
                file_id TEXT UNIQUE NOT NULL,
                FOREIGN KEY (message_id) REFERENCES messages(id)
            )
        """)

        # system_logs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL CHECK(level IN ('INFO', 'WARNING', 'ERROR')),
                component TEXT NOT NULL,
                event_desc TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_clients_token ON clients(access_token)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_clients_ip ON clients(ip_address)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_receiver ON messages(receiver_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_file_id ON file_metadata(file_id)")

        conn.commit()

    # ─── Authentication ──────────────────────────────────────────────

    def register_client(self, client_name: str, ip_address: str) -> tuple[str, int]:
        """
        Generate a new token and register a new client.
        Returns (access_token, client_id).
        Raises NmsgError if client_name already registered.
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        # Check duplicate name
        cursor.execute("SELECT id FROM clients WHERE client_name=?", (client_name,))
        if cursor.fetchone():
            raise NmsgError(f"Client name '{client_name}' already registered")

        token = f"nmsg-{uuid.uuid4().hex}"
        cursor.execute(
            "INSERT INTO clients (client_name, access_token, ip_address) VALUES (?, ?, ?)",
            (client_name, token, ip_address),
        )
        conn.commit()
        self.log("INFO", "auth", f"Client registered: {client_name} ({ip_address}), token={token}")
        return token, cursor.lastrowid

    def verify_token_ip(self, token: str, ip_address: str) -> Optional[int]:
        """
        Verify token + IP binding.
        Returns client_id if valid, None otherwise.
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM clients WHERE access_token=? AND ip_address=?",
            (token, ip_address),
        )
        row = cursor.fetchone()
        if row:
            cursor.execute(
                "UPDATE clients SET last_seen=CURRENT_TIMESTAMP WHERE id=?",
                (row["id"],),
            )
            conn.commit()
        return row["id"] if row else None

    def get_client_id_by_token(self, token: str) -> Optional[int]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM clients WHERE access_token=?", (token,))
        row = cursor.fetchone()
        return row["id"] if row else None

    def get_client_info(self, client_id: int) -> Optional[dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, client_name, ip_address, last_seen, created_at FROM clients WHERE id=?",
            (client_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def list_clients(self) -> list[dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, client_name, access_token, ip_address, last_seen, created_at FROM clients ORDER BY id"
        )
        return [dict(row) for row in cursor.fetchall()]

    def delete_client(self, client_id: int):
        """Delete a client and their messages from the database."""
        conn = self._get_conn()
        cursor = conn.cursor()
        # Delete messages first (foreign key)
        cursor.execute("DELETE FROM messages WHERE sender_id=? OR receiver_id=?", (client_id, client_id))
        cursor.execute("DELETE FROM clients WHERE id=?", (client_id,))
        conn.commit()

    def get_client_last_message(self, client_id: int) -> Optional[dict]:
        """Get the most recent message involving this client."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT m.id, m.sender_id, m.receiver_id, m.msg_type, m.content, m.timestamp,
                   s.client_name AS sender_name
            FROM messages m
            LEFT JOIN clients s ON m.sender_id = s.id
            WHERE m.sender_id=? OR m.receiver_id=?
            ORDER BY m.timestamp DESC
            LIMIT 1
            """,
            (client_id, client_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_client_last_message_any(self, client_id: int) -> Optional[dict]:
        """Get the most recent message involving this client, bypassing FK validation."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, sender_id, receiver_id, msg_type, content, timestamp
            FROM messages
            WHERE sender_id=? OR receiver_id=?
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (client_id, client_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_client_ip(self, token: str, new_ip: str):
        """Update the IP bound to a token (e.g., after reconnect from different IP)."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE clients SET ip_address=? WHERE access_token=?",
            (new_ip, token),
        )
        conn.commit()

    # ─── Messages ─────────────────────────────────────────────────────

    def save_message(
        self,
        sender_id: Optional[int],
        receiver_id: int,
        msg_type: str,
        content: str,
    ) -> int:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO messages (sender_id, receiver_id, msg_type, content) VALUES (?, ?, ?, ?)",
            (sender_id, receiver_id, msg_type, content),
        )
        conn.commit()
        return cursor.lastrowid

    def save_message_no_fk(
        self,
        sender_id: int,
        receiver_id: int,
        msg_type: str,
        content: str,
    ) -> int:
        """Insert a message without FK constraint checking (use receiver_id=0 for SERVER)."""
        conn = self._get_conn()
        conn.execute("PRAGMA foreign_keys=OFF")
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO messages (sender_id, receiver_id, msg_type, content) VALUES (?, ?, ?, ?)",
            (sender_id, receiver_id, msg_type, content),
        )
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()
        return cursor.lastrowid

    def get_messages_for_client(self, client_id: int, limit: int = 100) -> list[dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT m.id, m.sender_id, m.receiver_id, m.msg_type, m.content, m.timestamp,
                   c.client_name as sender_name
            FROM messages m
            LEFT JOIN clients c ON m.sender_id = c.id
            WHERE m.receiver_id=? OR m.sender_id=?
            ORDER BY m.timestamp DESC
            LIMIT ?
            """,
            (client_id, client_id, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    # ─── File Metadata ────────────────────────────────────────────────

    def save_file_metadata(
        self,
        message_id: int,
        file_id: str,
        file_name: str,
        file_size: int,
        storage_path: str,
        checksum: str,
    ) -> int:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO file_metadata (message_id, file_id, file_name, file_size, storage_path, checksum)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (message_id, file_id, file_name, file_size, storage_path, checksum),
        )
        conn.commit()
        return cursor.lastrowid

    def get_file_metadata_by_id(self, file_id: str) -> Optional[dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM file_metadata WHERE file_id=?", (file_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_file_metadata_by_message(self, message_id: int) -> Optional[dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM file_metadata WHERE message_id=?", (message_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    # ─── Logging ────────────────────────────────────────────────────────

    def log(self, level: str, component: str, event_desc: str):
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO system_logs (level, component, event_desc) VALUES (?, ?, ?)",
            (level, component, event_desc),
        )
        conn.commit()

    def get_recent_logs(self, limit: int = 50) -> list[dict]:
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM system_logs ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]
