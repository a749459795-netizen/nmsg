"""
nmsg Server - Client Session Management

Manages active client connections and their authenticated state.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ClientSession:
    """Represents an active client connection."""
    client_id: int
    client_name: str
    token: str
    ip_address: str
    socket: any  # socket object
    last_heartbeat: float = field(default_factory=time.time)
    authenticated: bool = True

    def is_alive(self, timeout: float = 120.0) -> bool:
        """Check if session has a recent heartbeat."""
        return (time.time() - self.last_heartbeat) < timeout

    def touch(self):
        self.last_heartbeat = time.time()


class SessionManager:
    """
    Thread-safe session registry.
    Maps token -> ClientSession.
    """

    def __init__(self):
        self._sessions: dict[str, ClientSession] = {}
        self._lock = threading.RLock()

    def add(self, session: ClientSession):
        with self._lock:
            self._sessions[session.token] = session

    def get(self, token: str) -> Optional[ClientSession]:
        with self._lock:
            return self._sessions.get(token)

    def get_by_client_id(self, client_id: int) -> Optional[ClientSession]:
        with self._lock:
            for s in self._sessions.values():
                if s.client_id == client_id:
                    return s
        return None

    def remove(self, token: str):
        with self._lock:
            self._sessions.pop(token, None)

    def list_all(self) -> list[ClientSession]:
        with self._lock:
            return list(self._sessions.values())

    def prune_dead(self, timeout: float = 120.0) -> list[str]:
        """Remove stale sessions. Returns list of removed tokens."""
        removed = []
        with self._lock:
            dead = [t for t, s in self._sessions.items() if not s.is_alive(timeout)]
            for t in dead:
                self._sessions.pop(t, None)
            removed = dead
        return removed

    def broadcast_exclude(self, exclude_token: str) -> list[ClientSession]:
        """Return all sessions except the excluded one."""
        with self._lock:
            return [s for t, s in self._sessions.items() if t != exclude_token]
