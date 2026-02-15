"""Windows DPAPI encryption for credential storage.

Uses CryptProtectData/CryptUnprotectData from crypt32.dll to encrypt
data tied to the current Windows user account.  No external dependencies.
Encrypted bytes are base64-encoded for safe storage in INI files.
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import logging

logger = logging.getLogger(__name__)

_crypt32 = ctypes.windll.crypt32
_kernel32 = ctypes.windll.kernel32


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def dpapi_encrypt(plaintext: str) -> str:
    """Encrypt a string using Windows DPAPI.

    Returns base64-encoded ciphertext safe for INI file storage.
    """
    data = plaintext.encode("utf-8")
    buf = ctypes.create_string_buffer(data, len(data))
    input_blob = _DATA_BLOB(
        len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))
    )
    output_blob = _DATA_BLOB()

    if not _crypt32.CryptProtectData(
        ctypes.byref(input_blob), None, None, None, None, 0,
        ctypes.byref(output_blob),
    ):
        raise OSError("CryptProtectData failed")

    encrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    _kernel32.LocalFree(output_blob.pbData)
    return base64.b64encode(encrypted).decode("ascii")


def dpapi_decrypt(encrypted_b64: str) -> str:
    """Decrypt a base64-encoded DPAPI ciphertext back to a string."""
    encrypted = base64.b64decode(encrypted_b64)
    buf = ctypes.create_string_buffer(encrypted, len(encrypted))
    input_blob = _DATA_BLOB(
        len(encrypted), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))
    )
    output_blob = _DATA_BLOB()

    if not _crypt32.CryptUnprotectData(
        ctypes.byref(input_blob), None, None, None, None, 0,
        ctypes.byref(output_blob),
    ):
        raise OSError("CryptUnprotectData failed")

    plaintext = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    _kernel32.LocalFree(output_blob.pbData)
    return plaintext.decode("utf-8")


def encrypt_or_plain(plaintext: str) -> str:
    """Encrypt if DPAPI is available, otherwise return as-is."""
    if not plaintext:
        return ""
    try:
        return dpapi_encrypt(plaintext)
    except OSError:
        logger.warning("DPAPI encryption unavailable, storing key in plain text")
        return plaintext


def decrypt_or_plain(value: str) -> str:
    """Decrypt if DPAPI ciphertext, otherwise return as-is (plain text fallback)."""
    if not value:
        return ""
    try:
        return dpapi_decrypt(value)
    except Exception:
        # Not encrypted (legacy plain text) or decryption failed — return as-is
        return value
