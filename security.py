"""
Security module — Fernet symmetric encryption for API keys.
The encryption key is generated once and stored in the SQLite settings table.
"""

import os
import base64
import logging
from cryptography.fernet import Fernet
import config
import database as db

logger = logging.getLogger(__name__)


def _load_or_create_key() -> bytes:
    """Return the Fernet key, creating it on first run."""
    if config.ENCRYPTION_KEY_CACHE is not None:
        return config.ENCRYPTION_KEY_CACHE

    stored = db.get_setting("encryption_key")
    if stored:
        key = base64.urlsafe_b64decode(stored.encode())
    else:
        key = Fernet.generate_key()
        db.set_setting("encryption_key", base64.urlsafe_b64encode(key).decode())
        logger.info("Generated new encryption key and stored in DB")

    config.ENCRYPTION_KEY_CACHE = key
    return key


def _fernet() -> Fernet:
    return Fernet(_load_or_create_key())


def encrypt(plain: str) -> bytes:
    """Encrypt a plaintext string and return cipher bytes."""
    return _fernet().encrypt(plain.encode())


def decrypt(cipher: bytes) -> str:
    """Decrypt cipher bytes and return plaintext string."""
    return _fernet().decrypt(cipher).decode()
