"""Exercises app.core.auth's real JWT verification (PRD §7.1/§7.6) against a
self-signed RSA keypair, with the JWKS fetch monkeypatched instead of hitting a real
HTTP endpoint — mirrors console-api's AuthServiceTest.php, just on the verifying side
of the same handshake."""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.core import auth as auth_module
from app.core.auth import InvalidTokenError, verify_internal_token, verify_runtime_token
from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _clear_jwks_cache():
    auth_module._jwks_cache.clear()
    yield
    auth_module._jwks_cache.clear()


def _keypair_and_jwks(kid: str = "test-kid"):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    public_jwk["kid"] = kid
    public_jwk["alg"] = "RS256"
    public_jwk["use"] = "sig"
    return private_key, [public_jwk]


def _sign(private_key, claims: dict, kid: str = "test-kid") -> str:
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})


def _patch_jwks(monkeypatch, keys: list[dict]) -> None:
    monkeypatch.setattr(auth_module, "_fetch_jwks_keys", lambda url: keys)


def _runtime_claims(**overrides) -> dict:
    now = int(time.time())
    settings = get_settings()
    claims = {
        "iss": settings.CONSOLE_INTERNAL_JWT_ISSUER,
        "aud": settings.RUNTIME_TOKEN_AUD,
        "company_id": "company-1",
        "user_id": "user-1",
        "role": "developer",
        "agent_version_id": "agent-version-1",
        "iat": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    return claims


def test_verify_runtime_token_round_trip(monkeypatch):
    private_key, keys = _keypair_and_jwks()
    _patch_jwks(monkeypatch, keys)

    token = _sign(private_key, _runtime_claims())
    claims = verify_runtime_token(token)

    assert claims["company_id"] == "company-1"
    assert claims["user_id"] == "user-1"
    assert claims["role"] == "developer"
    assert claims["agent_version_id"] == "agent-version-1"


def test_verify_runtime_token_rejects_missing_claims(monkeypatch):
    private_key, keys = _keypair_and_jwks()
    _patch_jwks(monkeypatch, keys)

    claims = _runtime_claims()
    del claims["agent_version_id"]
    token = _sign(private_key, claims)

    with pytest.raises(InvalidTokenError):
        verify_runtime_token(token)


def test_verify_runtime_token_rejects_expired(monkeypatch):
    private_key, keys = _keypair_and_jwks()
    _patch_jwks(monkeypatch, keys)

    now = int(time.time())
    token = _sign(private_key, _runtime_claims(iat=now - 400, exp=now - 100))

    with pytest.raises(InvalidTokenError):
        verify_runtime_token(token)


def test_verify_runtime_token_rejects_wrong_audience(monkeypatch):
    private_key, keys = _keypair_and_jwks()
    _patch_jwks(monkeypatch, keys)

    token = _sign(private_key, _runtime_claims(aud="some-other-audience"))

    with pytest.raises(InvalidTokenError):
        verify_runtime_token(token)


def test_verify_runtime_token_rejects_unknown_kid(monkeypatch):
    private_key, keys = _keypair_and_jwks()
    _patch_jwks(monkeypatch, keys)

    token = _sign(private_key, _runtime_claims(), kid="some-other-kid")

    with pytest.raises(InvalidTokenError):
        verify_runtime_token(token)


def test_verify_internal_token_round_trip(monkeypatch):
    private_key, keys = _keypair_and_jwks()
    _patch_jwks(monkeypatch, keys)
    settings = get_settings()

    now = int(time.time())
    token = _sign(private_key, {
        "iss": settings.CONSOLE_INTERNAL_JWT_ISSUER,
        "aud": settings.CONSOLE_INTERNAL_JWT_AUD,
        "iat": now,
        "exp": now + 60,
    })

    claims = verify_internal_token(token)
    assert claims["aud"] == settings.CONSOLE_INTERNAL_JWT_AUD
