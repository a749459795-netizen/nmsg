"""
nmsg Protocol Definition

Universal Envelope + Type-Specific Payload JSON format.
Implements the protocol specified in nmsg_protocol_design.md.
"""

import json
import time
import uuid
import hashlib
from enum import IntEnum
from dataclasses import dataclass, field, asdict
from typing import Optional, Any


class PacketType(IntEnum):
    TEXT = 1
    FILE = 2
    PACKET = 3


# Commands for Type 3 packets
class PacketCommand(str):
    AUTH = "AUTH"
    HEARTBEAT = "HEARTBEAT"
    REGISTER = "REGISTER"      # Client requests a new token
    LOGOUT = "LOGOUT"
    CONFIG_UPDATE = "CONFIG_UPDATE"
    ACK = "ACK"               # Generic acknowledgement
    NACK = "NACK"             # Negative acknowledgement
    FILE_REQUEST = "FILE_REQUEST"   # Client requests to download a file
    FILE_ACCEPT = "FILE_ACCEPT"     # Receiver accepts file transfer
    FILE_REJECT = "FILE_REJECT"     # Receiver rejects


# File transfer actions
class FileAction(str):
    INIT = "INIT"
    PROGRESS = "PROGRESS"
    COMPLETE = "COMPLETE"
    ERROR = "ERROR"
    CANCEL = "CANCEL"


PROTOCOL_VERSION = "1.0"
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB


@dataclass
class Packet:
    """Universal packet envelope."""
    type: int
    payload: dict
    v: str = PROTOCOL_VERSION
    ts: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_json(self) -> bytes:
        d = asdict(self)
        return json.dumps(d, ensure_ascii=False).encode("utf-8")

    @classmethod
    def from_json(cls, raw: bytes | str) -> "Packet":
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        d = json.loads(raw)
        return cls(
            v=d["v"],
            type=d["type"],
            ts=d["ts"],
            id=d.get("id", uuid.uuid4().hex),
            payload=d["payload"],
        )

    def to_dict(self) -> dict:
        return asdict(self)


class PacketFactory:
    """Helper to build typed packets."""

    @staticmethod
    def text(sender: str, receiver: str, content: str) -> Packet:
        return Packet(
            type=PacketType.TEXT,
            payload={"sender": sender, "receiver": receiver, "content": content},
        )

    @staticmethod
    def file_init(file_id: str, name: str, size: int, hash_val: str) -> Packet:
        return Packet(
            type=PacketType.FILE,
            payload={
                "file_id": file_id,
                "action": FileAction.INIT,
                "meta": {"name": name, "size": size, "hash": hash_val},
            },
        )

    @staticmethod
    def file_progress(file_id: str, bytes_sent: int) -> Packet:
        return Packet(
            type=PacketType.FILE,
            payload={"file_id": file_id, "action": FileAction.PROGRESS, "bytes_sent": bytes_sent},
        )

    @staticmethod
    def file_complete(file_id: str) -> Packet:
        return Packet(
            type=PacketType.FILE,
            payload={"file_id": file_id, "action": FileAction.COMPLETE},
        )

    @staticmethod
    def file_error(file_id: str, reason: str) -> Packet:
        return Packet(
            type=PacketType.FILE,
            payload={"file_id": file_id, "action": FileAction.ERROR, "reason": reason},
        )

    @staticmethod
    def auth(token: str) -> Packet:
        return Packet(
            type=PacketType.PACKET,
            payload={"command": PacketCommand.AUTH, "params": {"token": token}},
        )

    @staticmethod
    def heartbeat(token: str) -> Packet:
        return Packet(
            type=PacketType.PACKET,
            payload={"command": PacketCommand.HEARTBEAT, "params": {"token": token}},
        )

    @staticmethod
    def register(client_name: str) -> Packet:
        return Packet(
            type=PacketType.PACKET,
            payload={"command": PacketCommand.REGISTER, "params": {"client_name": client_name}},
        )

    @staticmethod
    def ack(msg_id: str, command: str = "") -> Packet:
        return Packet(
            type=PacketType.PACKET,
            payload={"command": PacketCommand.ACK, "params": {"ref_id": msg_id, "command": command}},
        )

    @staticmethod
    def nack(msg_id: str, reason: str, command: str = "") -> Packet:
        return Packet(
            type=PacketType.PACKET,
            payload={"command": PacketCommand.NACK, "params": {"ref_id": msg_id, "reason": reason, "command": command}},
        )

    @staticmethod
    def file_request(file_id: str, message_id: int) -> Packet:
        return Packet(
            type=PacketType.PACKET,
            payload={"command": PacketCommand.FILE_REQUEST, "params": {"file_id": file_id, "message_id": message_id}},

        )

    @staticmethod
    def generic_command(command: str, params: Optional[dict] = None) -> Packet:
        return Packet(
            type=PacketType.PACKET,
            payload={"command": command, "params": params or {}},
        )


def compute_file_hash(file_path: str) -> str:
    """Compute SHA256 hash of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_packet_size(data: bytes) -> bool:
    """Sanity check on incoming packet size (max 1MB envelope)."""
    return len(data) <= 1024 * 1024
