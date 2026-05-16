"""
nmsg Client - Socket Connection & Protocol Handler

Thread-safe async socket communication with the server.
"""

import socket
import struct
import threading
import logging
import json
import time
import uuid

from ..common.protocol import (
    Packet, PacketType, PacketFactory,
    PacketCommand, FileAction,
    compute_file_hash,
)
from ..common.exceptions import AuthError, ProtocolError

log = logging.getLogger("nmsg.client")


class Connection:
    """
    Manages the TCP connection to the nmsg server.
    Handles send/receive with length-prefixed envelopes.
    """

    HEADER_SIZE = 4

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._sock: socket.socket | None = None
        self._lock = threading.RLock()
        self._running = False
        self._reader_thread: threading.Thread | None = None
        self._callbacks: dict[str, callable] = {}

    def connect(self) -> bool:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.connect((self.host, self.port))
            self._running = True
            self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
            self._reader_thread.start()
            log.info(f"Connected to {self.host}:{self.port}")
            return True
        except Exception as e:
            log.error(f"Connection failed: {e}")
            return False

    def disconnect(self):
        self._running = False
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None

    def is_connected(self) -> bool:
        return self._sock is not None and self._running

    def on(self, command: str, callback: callable):
        """Register a callback for incoming packets by command or type."""
        self._callbacks[command] = callback

    def _read_loop(self):
        """Continuously read packets from the server."""
        while self._running:
            try:
                pkt = self._recv_packet()
                if pkt is None:
                    break
                self._dispatch(pkt)
            except ConnectionError:
                break
            except Exception as e:
                log.error(f"Read error: {e}")
                break

        self._running = False
        self._callbacks.get("_disconnect", lambda: None)()

    def _dispatch(self, pkt: Packet):
        """Route packet to appropriate handler."""
        if pkt.type == PacketType.PACKET:
            cmd = pkt.payload.get("command", "")
            cb = self._callbacks.get(cmd) or self._callbacks.get(str(cmd))
            if cb:
                cb(pkt)
                return
            # Handle ACK/NACK routing to original command
            params_cmd = pkt.payload.get("params", {}).get("command", "")
            if params_cmd:
                cb = self._callbacks.get(params_cmd) or self._callbacks.get(str(params_cmd))
                if cb:
                    cb(pkt)
                    return
            return

        type_cb = self._callbacks.get(f"type_{pkt.type}")
        if type_cb:
            type_cb(pkt)

        # Generic all handler
        self._callbacks.get("_packet", lambda p: None)(pkt)

    # ─── Send ───────────────────────────────────────────────────────

    def send(self, pkt: Packet):
        """Send a packet (thread-safe)."""
        data = pkt.to_json()
        header = struct.pack("!I", len(data))
        with self._lock:
            if not self._sock:
                raise ConnectionError("Not connected")
            try:
                self._sock.sendall(header + data)
            except Exception as e:
                raise ConnectionError(f"Send failed: {e}")

    def _recv_packet(self) -> Packet | None:
        """Receive one length-prefixed packet."""
        with self._lock:
            if not self._sock:
                return None
            sock = self._sock

        # Read header
        header = self._recv_exact(sock, self.HEADER_SIZE)
        if not header:
            return None
        length = struct.unpack("!I", header)[0]
        if length > 1024 * 1024:
            raise ProtocolError(f"Packet too large: {length}")

        data = self._recv_exact(sock, length)
        if not data:
            return None
        return Packet.from_json(data)

    def _recv_exact(self, sock: socket.socket, n: int) -> bytes | None:
        chunks: list[bytes] = []
        total = 0
        while total < n:
            try:
                chunk = sock.recv(n - total)
            except Exception:
                return None
            if not chunk:
                return None
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks)

    # ─── Convenience Send Methods ────────────────────────────────────

    def send_text(self, sender: str, receiver: str, content: str):
        pkt = PacketFactory.text(sender, receiver, content)
        self.send(pkt)

    def send_file_init(self, file_id: str, name: str, size: int, hash_val: str):
        pkt = PacketFactory.file_init(file_id, name, size, hash_val)
        self.send(pkt)

    def send_auth(self, token: str):
        pkt = PacketFactory.auth(token)
        self.send(pkt)

    def send_heartbeat(self, token: str):
        pkt = PacketFactory.heartbeat(token)
        self.send(pkt)

    def send_register(self, client_name: str):
        pkt = PacketFactory.register(client_name)
        self.send(pkt)

    def send_packet_command(self, command: str, params: dict | None = None):
        pkt = PacketFactory.generic_command(command, params)
        self.send(pkt)
