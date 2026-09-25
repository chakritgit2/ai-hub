"""Envelope-encryption stubs (PRD §7.4/§7.7).

Real design: each company has a data key (DEK) stored (wrapped) in
`runtime.company_keys`; the DEK itself is encrypted with `CONSOLE_MASTER_KEY`.
Connection secrets (`runtime.connection_secrets`) are encrypted with the
owning company's DEK, so decrypted material of one company is useless for
another, and a company can be erased by destroying its DEK
(crypto-shredding). Expected real implementation: AES-256-GCM via
`cryptography.hazmat.primitives.ciphers.aead.AESGCM`.
"""


def encrypt_with_dek(plaintext: bytes, dek: bytes) -> tuple[bytes, bytes]:
    """Encrypt `plaintext` with `dek`. Returns (ciphertext, nonce)."""
    raise NotImplementedError("encrypt_with_dek: AES-GCM envelope encryption not yet implemented")


def decrypt_with_dek(ciphertext: bytes, nonce: bytes, dek: bytes) -> bytes:
    raise NotImplementedError("decrypt_with_dek: AES-GCM envelope encryption not yet implemented")


def wrap_dek(dek: bytes, master_key: bytes) -> bytes:
    """Encrypt a company DEK with the console master key for storage."""
    raise NotImplementedError("wrap_dek: master-key wrapping not yet implemented")


def unwrap_dek(wrapped: bytes, master_key: bytes) -> bytes:
    raise NotImplementedError("unwrap_dek: master-key unwrapping not yet implemented")
