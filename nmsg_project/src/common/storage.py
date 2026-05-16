"""
nmsg File Storage Manager

Manages the IP-based directory hierarchy under storage/.
Based on the non-structured storage strategy in nmsg_requirements.md.
"""

import os
import uuid
import shutil
import hashlib
from pathlib import Path
from .exceptions import StorageError
from .protocol import MAX_FILE_SIZE


class StorageManager:
    """
    Manages file storage under a root storage/ directory.
    Files are stored at: storage/uploads/<client_ip>/<file_id>_<original_name>
    """

    def __init__(self, root: str = "storage"):
        self.root = Path(root).resolve()
        self.uploads = self.root / "uploads"
        self.uploads.mkdir(parents=True, exist_ok=True)

    def _ip_dir(self, ip_address: str) -> Path:
        d = self.uploads / ip_address.replace(".", "_")
        d.mkdir(parents=True, exist_ok=True)
        return d

    def store_file(
        self,
        source_path: str | Path,
        ip_address: str,
        original_name: str,
    ) -> tuple[str, str, int]:
        """
        Copy a file into storage.
        Returns (storage_path, sha256_hash, size_bytes).
        """
        source = Path(source_path)
        if not source.exists():
            raise StorageError(f"Source file not found: {source}")

        size = source.stat().st_size
        if size > MAX_FILE_SIZE:
            raise StorageError(f"File exceeds {MAX_FILE_SIZE} byte limit: {size}")

        file_id = uuid.uuid4().hex
        safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in original_name)
        dest_name = f"{file_id}_{safe_name}"
        dest = self._ip_dir(ip_address) / dest_name

        # Copy with hash verification
        h = hashlib.sha256()
        with open(source, "rb") as src, open(dest, "wb") as dst:
            for chunk in iter(lambda: src.read(65536), b""):
                h.update(chunk)
                dst.write(chunk)

        storage_path = str(dest.relative_to(self.root))
        return storage_path, h.hexdigest(), size

    def write_stream(
        self,
        ip_address: str,
        original_name: str,
        file_id: str,
        stream,
        size: int,
    ) -> tuple[str, str]:
        """
        Write a file from a byte stream (for TCP/HTTP streaming upload).
        Returns (storage_path, sha256_hash).
        """
        if size > MAX_FILE_SIZE:
            raise StorageError(f"File exceeds {MAX_FILE_SIZE} byte limit: {size}")

        safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in original_name)
        dest_name = f"{file_id}_{safe_name}"
        dest = self._ip_dir(ip_address) / dest_name

        h = hashlib.sha256()
        written = 0
        with open(dest, "wb") as f:
            while written < size:
                chunk = stream.read(min(65536, size - written))
                if not chunk:
                    raise StorageError("Stream ended unexpectedly during write")
                h.update(chunk)
                f.write(chunk)
                written += len(chunk)

        return str(dest.relative_to(self.root)), h.hexdigest()

    def get_full_path(self, storage_path: str) -> Path:
        """Resolve a relative storage path to an absolute path."""
        return self.root / storage_path

    def delete_file(self, storage_path: str) -> bool:
        """Delete a stored file. Returns True if deleted."""
        try:
            p = self.get_full_path(storage_path)
            if p.exists():
                p.unlink()
                return True
        except Exception as e:
            raise StorageError(f"Failed to delete {storage_path}: {e}")
        return False

    def file_exists(self, storage_path: str) -> bool:
        return self.get_full_path(storage_path).exists()

    def get_storage_size(self, ip_address: str | None = None) -> int:
        """Return total bytes used, optionally for a specific IP."""
        if ip_address:
            d = self._ip_dir(ip_address)
            return sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        return sum(f.stat().st_size for f in self.root.rglob("*") if f.is_file())
