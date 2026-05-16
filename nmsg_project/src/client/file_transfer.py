"""
nmsg Client - File Transfer Module

Handles uploading files to the server with streaming and progress reporting.
"""

import os
import socket
import struct
import uuid
import hashlib
import threading
import logging
import time

from ..common.protocol import PacketFactory, Packet, PacketType, FileAction
from ..common.exceptions import TransferError

log = logging.getLogger("nmsg.client.file_transfer")


class FileUploader:
    """
    Uploads a file to the server over the existing TCP connection.
    Protocol: sends a File INIT packet, then streams raw bytes, then waits for COMPLETE/ERROR.
    """

    MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB

    def __init__(self, connection, progress_callback=None):
        self.conn = connection
        self.progress_callback = progress_callback or (lambda sent, total, speed: None)
        self._cancelled = False

    def upload(self, file_path: str) -> str:
        """
        Upload a file. Returns file_id on success.
        Raises TransferError on failure.
        """
        path = os.path.abspath(file_path)
        if not os.path.exists(path):
            raise TransferError(f"File not found: {path}")

        size = os.path.getsize(path)
        if size > self.MAX_FILE_SIZE:
            raise TransferError(f"File exceeds {self.MAX_FILE_SIZE} byte limit")

        file_id = uuid.uuid4().hex
        name = os.path.basename(path)

        # Compute hash
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        file_hash = h.hexdigest()

        log.info(f"Starting upload: {name} ({size} bytes), file_id={file_id}")

        # Build the INIT packet
        pkt = PacketFactory.file_init(file_id, name, size, file_hash)
        self.conn.send(pkt)

        # Stream the raw bytes through the socket
        start = time.time()
        sent = 0
        with open(path, "rb") as f:
            while sent < size:
                if self._cancelled:
                    raise TransferError("Upload cancelled")
                chunk = f.read(65536)
                if not chunk:
                    break
                # Send raw bytes directly over the socket (protocol extension)
                self.conn._sock.sendall(chunk)
                sent += len(chunk)
                elapsed = time.time() - start
                speed = sent / elapsed if elapsed > 0 else 0
                self.progress_callback(sent, size, speed)

        log.info(f"Upload complete: {file_id}")
        return file_id

    def cancel(self):
        self._cancelled = True


class FileDownloader:
    """
    Downloads a file from server via HTTP-like streaming.
    (Placeholder — implemented when server supports file download)
    """

    def __init__(self, connection, progress_callback=None):
        self.conn = connection
        self.progress_callback = progress_callback or (lambda recvd, total, speed: None)
        self._cancelled = False

    def download(self, file_id: str, dest_path: str, expected_size: int) -> str:
        """
        Request and receive a file from the server.
        Writes to dest_path. Returns actual path written.
        """
        # Request file
        pkt = PacketFactory.generic_command("FILE_DOWNLOAD", {"file_id": file_id})
        self.conn.send(pkt)

        # Wait for incoming stream (server sends raw bytes after ACK)
        received = 0
        chunks: list[bytes] = []
        start = time.time()
        sock = self.conn._sock

        while received < expected_size:
            if self._cancelled:
                raise TransferError("Download cancelled")
            chunk = sock.recv(min(65536, expected_size - received))
            if not chunk:
                raise TransferError("Server disconnected during download")
            chunks.append(chunk)
            received += len(chunk)
            elapsed = time.time() - start
            speed = received / elapsed if elapsed > 0 else 0
            self.progress_callback(received, expected_size, speed)

        data = b"".join(chunks)
        with open(dest_path, "wb") as f:
            f.write(data)

        return dest_path

    def cancel(self):
        self._cancelled = True
