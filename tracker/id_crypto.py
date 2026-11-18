"""Small, dependency-free encryption helper for stored ID documents.

The project's virtualenv does not include a crypto package, so this module uses
HMAC-SHA256 in counter mode plus an HMAC-SHA256 authentication tag.  It is a
basic confidentiality/integrity layer, not a replacement for a managed KMS.

Format: b"SKID1" | nonce(16) | ciphertext | tag(32)
The master key is derived from settings.ID_ENCRYPTION_KEY when set, otherwise
from settings.SECRET_KEY.  Changing that key makes existing IDs unreadable.
"""
import hashlib
import hmac
import os

MAGIC = b"SKID1"
NONCE_SIZE = 16
TAG_SIZE = 32
BLOCK_SIZE = 32


def _master_key():
    from django.conf import settings

    configured = getattr(settings, "ID_ENCRYPTION_KEY", "") or ""
    secret = configured or settings.SECRET_KEY
    return hmac.new(str(secret).encode("utf-8"), b"skillup-id-encryption-v1", hashlib.sha256).digest()


def _subkey(label):
    return hmac.new(_master_key(), label, hashlib.sha256).digest()


def _keystream(enc_key, nonce, length):
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(enc_key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def is_encrypted(blob):
    return isinstance(blob, (bytes, bytearray)) and bytes(blob[: len(MAGIC)]) == MAGIC


def encrypt_bytes(data):
    """Encrypt bytes and return a self-contained authenticated blob."""
    if data is None:
        data = b""
    data = bytes(data)
    enc_key = _subkey(b"enc")
    mac_key = _subkey(b"mac")
    nonce = os.urandom(NONCE_SIZE)
    ciphertext = bytes(a ^ b for a, b in zip(data, _keystream(enc_key, nonce, len(data))))
    tag = hmac.new(mac_key, MAGIC + nonce + ciphertext, hashlib.sha256).digest()
    return MAGIC + nonce + ciphertext + tag


def decrypt_bytes(blob, allow_plaintext=False):
    """Decrypt a blob produced by encrypt_bytes.

    With allow_plaintext=True, legacy unencrypted files are returned unchanged;
    this is used only while migrating existing ID documents.
    """
    if not isinstance(blob, (bytes, bytearray)):
        blob = bytes(blob)
    blob = bytes(blob)
    if not is_encrypted(blob):
        if allow_plaintext:
            return blob
        raise ValueError("Not an encrypted ID document.")
    if len(blob) < len(MAGIC) + NONCE_SIZE + TAG_SIZE:
        raise ValueError("Encrypted ID document is truncated.")

    nonce = blob[len(MAGIC):len(MAGIC) + NONCE_SIZE]
    ciphertext = blob[len(MAGIC) + NONCE_SIZE:-TAG_SIZE]
    tag = blob[-TAG_SIZE:]
    enc_key = _subkey(b"enc")
    mac_key = _subkey(b"mac")
    expected = hmac.new(mac_key, MAGIC + nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise ValueError("Encrypted ID document failed integrity verification.")
    return bytes(a ^ b for a, b in zip(ciphertext, _keystream(enc_key, nonce, len(ciphertext))))
