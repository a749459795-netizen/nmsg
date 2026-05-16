"""
nmsg Custom Exceptions
"""


class NmsgError(Exception):
    """Base exception for nmsg."""
    pass


class AuthError(NmsgError):
    """Authentication / authorization failure."""
    pass


class TransferError(NmsgError):
    """File transfer failure."""
    pass


class ProtocolError(NmsgError):
    """Malformed or invalid protocol packet."""
    pass


class StorageError(NmsgError):
    """File storage I/O failure."""
    pass
