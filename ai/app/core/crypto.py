"""Envelope-encryption (PRD §7.4/§7.7). AES-256-GCM via
`cryptography.hazmat.primitives.ciphers.aead.AESGCM`.

Each company has its own data key (DEK) stored (wrapped) in
`runtime.company_keys` (see `app/services/company_keys.py`); the DEK itself
is encrypted with `CONSOLE_MASTER_KEY` (see `app/core/config.py::master_key_bytes`).
Connection secrets (`runtime.connection_secrets`) are encrypted with the
owning company's DEK, so decrypted material of one company is useless for
another, and a company can be erased by destroying its DEK (crypto-shredding,
not implemented by this slice).

`wrap_dek`/`unwrap_dek` return/accept one `bytes` blob (a fresh 12-byte nonce
prepended to the AESGCM ciphertext, which already carries its own 16-byte
auth tag) — `runtime.company_keys.wrapped_dek` is a single column, so the
nonce has to travel inside it. `encrypt_with_dek`/`decrypt_with_dek` instead
return/accept the nonce separately, matching `runtime.connection_secrets`'s
schema (separate `nonce` column) — don't conflate the two formats.
"""
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# 96-bit nonce - the size AES-GCM is designed for (NIST SP 800-38D); never reused with the
# same key, which is why every encrypt/wrap call generates a fresh one via os.urandom.
NONCE_SIZE_BYTES = 12


def encrypt_with_dek(plaintext: bytes, dek: bytes) -> tuple[bytes, bytes]:
    """Encrypt `plaintext` with `dek`. Returns (ciphertext, nonce)."""
    nonce = os.urandom(NONCE_SIZE_BYTES)
    ciphertext = AESGCM(dek).encrypt(nonce, plaintext, associated_data=None)
    return ciphertext, nonce


def decrypt_with_dek(ciphertext: bytes, nonce: bytes, dek: bytes) -> bytes:
    """Raises `cryptography.exceptions.InvalidTag` if `dek`/`nonce` is wrong or the
    ciphertext was tampered with - never returns garbage bytes silently."""
    return AESGCM(dek).decrypt(nonce, ciphertext, associated_data=None)


def wrap_dek(dek: bytes, master_key: bytes) -> bytes:
    """Encrypt a company DEK with the console master key for storage.
    Returns nonce (12 bytes) || AESGCM ciphertext."""
    nonce = os.urandom(NONCE_SIZE_BYTES)
    ciphertext = AESGCM(master_key).encrypt(nonce, dek, associated_data=None)
    return nonce + ciphertext


def unwrap_dek(wrapped: bytes, master_key: bytes) -> bytes:
    nonce, ciphertext = wrapped[:NONCE_SIZE_BYTES], wrapped[NONCE_SIZE_BYTES:]
    return AESGCM(master_key).decrypt(nonce, ciphertext, associated_data=None)
