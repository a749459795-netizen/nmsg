"""
nmsg Client - Authentication & Token Management

Handles local token persistence and server authentication.
"""

import json
import os
import logging
from pathlib import Path
from .connection import Connection
from ..common.protocol import PacketFactory, PacketCommand, Packet
from ..common.exceptions import AuthError

log = logging.getLogger("nmsg.client.auth")


class ClientAuth:
    """
    Manages client identity, token persistence, and server authentication.
    Tokens are stored in a local JSON config file.
    """

    def __init__(self, config_path: str = "nmsg_client.json"):
        self.config_path = Path(config_path)
        self.client_name: str = ""
        self.token: str = ""
        self.client_id: int = 0
        self.server_ip: str = ""
        self.server_port: int = 9000
        self.server_token: str = ""
        self._load()

    def _load(self):
        """Load config from disk if it exists."""
        if self.config_path.exists():
            try:
                data = json.loads(self.config_path.read_text("utf-8"))
                self.client_name = data.get("client_name", "")
                self.token = data.get("token", "")
                self.client_id = data.get("client_id", 0)
                self.server_ip = data.get("server_ip", "")
                self.server_port = data.get("server_port", 9000)
                self.server_token = data.get("server_token", "")
            except Exception as e:
                log.warning(f"Failed to load config: {e}")

    def save(self):
        """Persist config to disk."""
        data = {
            "client_name": self.client_name,
            "token": self.token,
            "client_id": self.client_id,
            "server_ip": self.server_ip,
            "server_port": self.server_port,
            "server_token": self.server_token,
        }
        self.config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        log.info(f"Config saved to {self.config_path}")

    def has_token(self) -> bool:
        return bool(self.token)

    def update_server(self, ip: str, port: int):
        self.server_ip = ip
        self.server_port = port

    def apply_register_result(self, pkt: Packet):
        """Extract token and client_id from REGISTER ACK."""
        params = pkt.payload.get("params", {})
        self.token = params.get("token", "")
        self.client_id = params.get("client_id", 0)

    def apply_auth_result(self, pkt: Packet):
        """Called when AUTH succeeds (token already stored)."""
        pass

    def clear(self):
        self.token = ""
        self.client_id = 0
        if self.config_path.exists():
            self.config_path.unlink()
