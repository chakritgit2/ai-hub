import os

import pytest
from cryptography.exceptions import InvalidTag

from app.core.crypto import decrypt_with_dek, encrypt_with_dek, unwrap_dek, wrap_dek


def test_encrypt_decrypt_round_trip():
    dek = os.urandom(32)
    plaintext = b"sk-super-secret-api-key"

    ciphertext, nonce = encrypt_with_dek(plaintext, dek)

    assert ciphertext != plaintext
    assert decrypt_with_dek(ciphertext, nonce, dek) == plaintext


def test_decrypt_with_wrong_dek_raises_invalid_tag():
    dek = os.urandom(32)
    wrong_dek = os.urandom(32)
    ciphertext, nonce = encrypt_with_dek(b"secret", dek)

    with pytest.raises(InvalidTag):
        decrypt_with_dek(ciphertext, nonce, wrong_dek)


def test_decrypt_with_wrong_nonce_raises_invalid_tag():
    dek = os.urandom(32)
    ciphertext, nonce = encrypt_with_dek(b"secret", dek)
    wrong_nonce = os.urandom(len(nonce))

    with pytest.raises(InvalidTag):
        decrypt_with_dek(ciphertext, wrong_nonce, dek)


def test_two_encryptions_of_same_plaintext_use_different_nonces_and_ciphertexts():
    dek = os.urandom(32)
    ciphertext1, nonce1 = encrypt_with_dek(b"same plaintext", dek)
    ciphertext2, nonce2 = encrypt_with_dek(b"same plaintext", dek)

    assert nonce1 != nonce2
    assert ciphertext1 != ciphertext2


def test_wrap_unwrap_round_trip():
    master_key = os.urandom(32)
    dek = os.urandom(32)

    wrapped = wrap_dek(dek, master_key)

    assert wrapped != dek
    assert unwrap_dek(wrapped, master_key) == dek


def test_unwrap_with_wrong_master_key_raises_invalid_tag():
    master_key = os.urandom(32)
    wrong_master_key = os.urandom(32)
    wrapped = wrap_dek(os.urandom(32), master_key)

    with pytest.raises(InvalidTag):
        unwrap_dek(wrapped, wrong_master_key)
