"""
nmsg Server - Main TCP Server

Handles:
- TCP client connections
- Token + IP authentication
- Message routing (text, file metadata, system packets)
- File transfer streaming (upload from client)
- Heartbeat tracking
- Client session management
"""

import socket
import struct
import threading
import selectors
import logging
import json
import uuid
import time
import os
from pathlib import Path

from ..common.protocol import (
    Packet, PacketType, PacketFactory,
    PacketCommand, FileAction,
    compute_file_hash, validate_packet_size,
)
from ..common.database import Database
from ..common.storage import StorageManager
from ..common.exceptions import AuthError, TransferError, ProtocolError
from .session import SessionManager, ClientSession

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("nmsg.server")


class NmsgServer:
    """
    Main server class. Runs the TCP socket loop and dispatches to handlers.
    """

    HEADER_SIZE = 4  # 4-byte big-endian length prefix

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 9000,
        db_path: str = "nmsg.db",
        storage_root: str = "storage",
    ):
        self.host = host
        self.port = port
        self.db = Database(db_path)
        self.storage = StorageManager(storage_root)
        self.sessions = SessionManager()

        self._sock: socket.socket | None = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._selector = selectors.DefaultSelector()
        self._client_threads: list[threading.Thread] = []

        # Lock for send operations
        self._send_lock = threading.Lock()

    # ─── Lifecycle ───────────────────────────────────────────────────

    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(50)
        self._sock.setblocking(False)
        self._running = True

        self._selector.register(self._sock, selectors.EVENT_READ, self._accept)

        log.info(f"nmsg server listening on {self.host}:{self.port}")

        # Prune dead sessions periodically
        self._prune_thread = threading.Thread(target=self._prune_loop, daemon=True)
        self._prune_thread.start()

        self._main_loop()

    def stop(self):
        self._running = False
        if self._sock:
            self._sock.close()
        self._selector.close()
        self.db.close()
        log.info("Server stopped")

    def _main_loop(self):
        while self._running:
            try:
                events = self._selector.select(timeout=1.0)
                for key, mask in events:
                    callback = key.data
                    callback(key.fileobj)
            except Exception as e:
                log.error(f"Main loop error: {e}")

    # ─── Client Acceptance ────────────────────────────────────────────

    def _accept(self, sock: socket.socket):
        try:
            client_sock, addr = sock.accept()
            client_sock.setblocking(True)
            ip, port = addr
            log.info(f"Incoming connection from {ip}:{port}")
            t = threading.Thread(target=self._handle_client, args=(client_sock, ip), daemon=True)
            t.start()
        except Exception as e:
            log.error(f"Accept error: {e}")

    # ─── Client Handler ───────────────────────────────────────────────

    def _handle_client(self, sock: socket.socket, ip: str):
        """Main per-client handler loop."""
        client_session: ClientSession | None = None

        try:
            while self._running:
                pkt = self._recv_packet(sock)
                if pkt is None:
                    break

                # ── Type 1: Text ───────────────────────────────────
                if pkt.type == PacketType.TEXT:
                    client_session = self._handle_text(pkt, ip, client_session)

                # ── Type 2: File ────────────────────────────────────
                elif pkt.type == PacketType.FILE:
                    client_session = self._handle_file(pkt, ip, sock, client_session)

                # ── Type 3: Packet ─────────────────────────────────
                elif pkt.type == PacketType.PACKET:
                    client_session = self._handle_packet(pkt, ip, sock, client_session)

        except ConnectionError as e:
            log.warning(f"Client {ip} disconnected: {e}")
        except Exception as e:
            log.error(f"Handler error for {ip}: {e}")
        finally:
            if client_session:
                self.sessions.remove(client_session.token)
            try:
                sock.close()
            except Exception:
                pass

    # ─── Packet Receive ────────────────────────────────────────────────

    def _recv_packet(self, sock: socket.socket) -> Packet | None:
        """Receive a length-prefixed JSON packet."""
        try:
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
        except ProtocolError:
            raise
        except Exception as e:
            raise ConnectionError(f"recv error: {e}")

    def _recv_exact(self, sock: socket.socket, n: int) -> bytes | None:
        """Receive exactly n bytes."""
        chunks: list[bytes] = []
        total = 0
        while total < n:
            chunk = sock.recv(n - total)
            if not chunk:
                return None
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks)

    def _send_packet(self, sock: socket.socket, pkt: Packet):
        """Send a length-prefixed JSON packet."""
        data = pkt.to_json()
        header = struct.pack("!I", len(data))
        with self._send_lock:
            sock.sendall(header + data)

    # ─── Type 1: Text ─────────────────────────────────────────────────

    def _handle_text(
        self, pkt: Packet, ip: str, session: ClientSession | None
    ) -> ClientSession:
        """Route a text message to its recipient."""
        payload = pkt.payload
        sender = payload.get("sender", "")
        receiver = payload.get("receiver", "")
        content = payload.get("content", "")

        # Auth check
        if not session or not session.authenticated:
            raise AuthError("Unauthenticated text message rejected")

        log.info(f"[TEXT] {session.client_name} -> {receiver}: {content[:50]}")

        # Determine recipient session
        recipient_session = self._find_session_by_name(receiver)
        receiver_id = self.db.get_client_id_by_token(session.token)

        # Save to DB
        sender_id = session.client_id
        receiver_db_id = None
        if receiver == "SERVER":
            # SERVER = special system recipient; save with receiver_id=0 so the
            # message shows in the GUI "last message" column.
            log.warning(f"[TEXT] Receiver is SERVER, saving with receiver_id=0")
            self.db.save_message_no_fk(sender_id, 0, "Text", content)
            if recipient_session:
                forward = PacketFactory.text(sender, receiver, content)
                self._send_packet(recipient_session.socket, forward)
            return session

        receiver_client = self._find_client_by_name(receiver)
        if receiver_client:
            receiver_db_id = receiver_client["id"]
        else:
            log.warning(f"[TEXT] Receiver '{receiver}' not found, skipping DB save")
            if recipient_session:
                forward = PacketFactory.text(sender, receiver, content)
                self._send_packet(recipient_session.socket, forward)
            return session

        msg_id = self.db.save_message(
            sender_id=sender_id,
            receiver_id=receiver_db_id,
            msg_type="Text",
            content=content,
        )

        # Forward to recipient if online
        if recipient_session:
            forward = PacketFactory.text(sender, receiver, content)
            self._send_packet(recipient_session.socket, forward)

        return session

    # ─── Type 2: File ─────────────────────────────────────────────────

    def _handle_file(
        self, pkt: Packet, ip: str, sock: socket.socket, session: ClientSession | None
    ) -> ClientSession:
        """Handle file transfer packets (INIT/PROGRESS/COMPLETE/ERROR)."""
        if not session or not session.authenticated:
            raise AuthError("Unauthenticated file operation rejected")

        payload = pkt.payload
        action = payload.get("action")
        file_id = payload.get("file_id")

        if action == FileAction.INIT:
            return self._handle_file_init(pkt, ip, sock, session)
        elif action == FileAction.COMPLETE:
            return self._handle_file_complete(payload, session)
        elif action == FileAction.ERROR:
            log.warning(f"[FILE] {session.client_name}: transfer error: {payload.get('reason')}")
        elif action == FileAction.CANCEL:
            log.info(f"[FILE] {session.client_name}: transfer cancelled")

        return session

    def _handle_file_init(
        self, pkt: Packet, ip: str, sock: socket.socket, session: ClientSession
    ) -> ClientSession:
        """Receive file metadata and stream the binary data."""
        payload = pkt.payload
        meta = payload.get("meta", {})
        file_id = payload.get("file_id")
        name = meta.get("name", "unknown")
        size = meta.get("size", 0)
        expected_hash = meta.get("hash", "")

        log.info(f"[FILE INIT] {session.client_name}: {name} ({size} bytes)")

        if size > 100 * 1024 * 1024:
            nack = PacketFactory.file_error(file_id, "File exceeds 100MB limit")
            self._send_packet(sock, nack)
            return session

        # Receive the binary stream
        try:
            received = 0
            chunks: list[bytes] = []
            while received < size:
                chunk = sock.recv(min(65536, size - received))
                if not chunk:
                    raise TransferError("Stream ended unexpectedly")
                chunks.append(chunk)
                received += len(chunk)

            data = b"".join(chunks)

            # Verify hash
            import hashlib
            h = hashlib.sha256(data).hexdigest()
            if h != expected_hash:
                raise TransferError(f"Hash mismatch: expected {expected_hash}, got {h}")

            # Save to storage
            dest_dir = self.storage.uploads / ip.replace(".", "_")
            dest_dir.mkdir(parents=True, exist_ok=True)
            safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
            dest_path = dest_dir / f"{file_id}_{safe_name}"
            dest_path.write_bytes(data)

            storage_rel = str(dest_path.relative_to(self.storage.root))

            log.info(f"[FILE] Saved: {dest_path} ({size} bytes)")

            # Save metadata to DB (need a message record first)
            msg_id = self.db.save_message(
                sender_id=session.client_id,
                receiver_id=0,
                msg_type="File",
                content=f"File: {name} ({size} bytes)",
            )
            self.db.save_file_metadata(
                message_id=msg_id,
                file_id=file_id,
                file_name=name,
                file_size=size,
                storage_path=storage_rel,
                checksum=h,
            )

            # Notify sender of completion
            ack = PacketFactory.file_complete(file_id)
            self._send_packet(sock, ack)

            log.info(f"[FILE COMPLETE] {session.client_name}: {name} stored at {storage_rel}")

        except Exception as e:
            log.error(f"[FILE ERROR] {session.client_name}: {e}")
            err = PacketFactory.file_error(file_id, str(e))
            self._send_packet(sock, err)

        return session

    def _handle_file_complete(self, payload: dict, session: ClientSession) -> ClientSession:
        """Handle file COMPLETE action (used for receiver acknowledgement)."""
        file_id = payload.get("file_id")
        log.info(f"[FILE ACK] {session.client_name}: file {file_id} transfer signalled complete")
        return session

    # ─── Type 3: Packet ───────────────────────────────────────────────

    def _handle_packet(
        self, pkt: Packet, ip: str, sock: socket.socket, session: ClientSession | None
    ) -> ClientSession:
        """Handle system protocol commands."""
        payload = pkt.payload
        command = payload.get("command")
        params = payload.get("params", {})

        if command == PacketCommand.REGISTER:
            return self._cmd_register(pkt, ip, sock)
        elif command == PacketCommand.AUTH:
            return self._cmd_auth(pkt, ip, sock)
        elif command == PacketCommand.HEARTBEAT:
            return self._cmd_heartbeat(pkt, ip, sock, session)
        elif command == PacketCommand.LOGOUT:
            return self._cmd_logout(pkt, ip, sock, session)
        else:
            log.warning(f"Unknown packet command: {command}")
            nack = PacketFactory.nack(pkt.id, f"Unknown command: {command}", command)
            self._send_packet(sock, nack)
            return session

    def _cmd_register(
        self, pkt: Packet, ip: str, sock: socket.socket
    ) -> ClientSession:
        """Register a new client and return token."""
        client_name = pkt.payload.get("params", {}).get("client_name", f"client_{ip}")
        try:
            token, client_id = self.db.register_client(client_name, ip)
            session = ClientSession(
                client_id=client_id,
                client_name=client_name,
                token=token,
                ip_address=ip,
                socket=sock,
            )
            self.sessions.add(session)
            ack = PacketFactory.ack(pkt.id, PacketCommand.REGISTER)
            ack.payload["params"]["token"] = token
            ack.payload["params"]["client_id"] = client_id
            self._send_packet(sock, ack)
            log.info(f"[REGISTER] {client_name} registered, token={token}")
            return session
        except Exception as e:
            log.error(f"[REGISTER ERROR] {e}")
            nack = PacketFactory.nack(pkt.id, str(e), PacketCommand.REGISTER)
            self._send_packet(sock, nack)
            raise

    def _cmd_auth(
        self, pkt: Packet, ip: str, sock: socket.socket
    ) -> ClientSession:
        """Authenticate an existing client by token."""
        token = pkt.payload.get("params", {}).get("token", "")
        client_id = self.db.verify_token_ip(token, ip)
        if client_id is None:
            nack = PacketFactory.nack(pkt.id, "Invalid token or IP mismatch", PacketCommand.AUTH)
            self._send_packet(sock, nack)
            raise AuthError(f"Auth failed for token={token}, ip={ip}")

        info = self.db.get_client_info(client_id)
        session = ClientSession(
            client_id=client_id,
            client_name=info["client_name"],
            token=token,
            ip_address=ip,
            socket=sock,
        )
        self.sessions.add(session)
        ack = PacketFactory.ack(pkt.id, PacketCommand.AUTH)
        self._send_packet(sock, ack)
        log.info(f"[AUTH] {info['client_name']} authenticated from {ip}")
        return session

    def _cmd_heartbeat(
        self, pkt: Packet, ip: str, sock: socket.socket, session: ClientSession | None
    ) -> ClientSession:
        if session:
            session.touch()
            ack = PacketFactory.ack(pkt.id, PacketCommand.HEARTBEAT)
            self._send_packet(sock, ack)
        else:
            nack = PacketFactory.nack(pkt.id, "No active session", PacketCommand.HEARTBEAT)
            self._send_packet(sock, nack)
        return session

    def _cmd_logout(
        self, pkt: Packet, ip: str, sock: socket.socket, session: ClientSession | None
    ) -> ClientSession:
        if session:
            self.sessions.remove(session.token)
            log.info(f"[LOGOUT] {session.client_name} logged out")
        ack = PacketFactory.ack(pkt.id, PacketCommand.LOGOUT)
        self._send_packet(sock, ack)
        raise ConnectionError("Client logged out")

    # ─── Helpers ───────────────────────────────────────────────────────

    def _find_session_by_name(self, name: str) -> ClientSession | None:
        for s in self.sessions.list_all():
            if s.client_name == name:
                return s
        return None

    def _find_client_by_name(self, name: str) -> dict | None:
        for c in self.db.list_clients():
            if c["client_name"] == name:
                return c
        return None

    # ─── Prune Loop ───────────────────────────────────────────────────

    def _prune_loop(self):
        while self._running:
            time.sleep(60)
            removed = self.sessions.prune_dead()
            if removed:
                log.info(f"Pruned {len(removed)} dead sessions")
