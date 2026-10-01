import time

import jwt
import pytest

from app.services.gateway_auth import get_jwks, mint_session_token, verify_session_token


def test_mint_and_verify_round_trip() -> None:
    token, expires_in = mint_session_token(
        "company-1", "deployment-1", "external-user-1", claims={"tier": "pro"}
    )
    assert expires_in == 300

    claims = verify_session_token(token)
    assert claims["company_id"] == "company-1"
    assert claims["deployment_id"] == "deployment-1"
    assert claims["external_user_id"] == "external-user-1"
    assert claims["claims"] == {"tier": "pro"}


def test_jwks_shape_matches_the_minted_token() -> None:
    token, _ = mint_session_token("company-1", "deployment-1", "user-1")
    header = jwt.get_unverified_header(token)

    jwks = get_jwks()
    assert len(jwks["keys"]) == 1
    key = jwks["keys"][0]
    assert key["kid"] == header["kid"]
    assert key["kty"] == "RSA"
    assert key["alg"] == "RS256"


def test_verify_rejects_a_tampered_token() -> None:
    token, _ = mint_session_token("company-1", "deployment-1", "user-1")
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")

    with pytest.raises(jwt.InvalidTokenError):
        verify_session_token(tampered)


def test_verify_rejects_an_expired_token() -> None:
    from app.services.gateway_auth import _AUDIENCE, _ISSUER, _kid, _load_or_create_key

    key = _load_or_create_key()
    now = int(time.time())
    expired_payload = {
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "company_id": "company-1",
        "deployment_id": "deployment-1",
        "external_user_id": "user-1",
        "claims": {},
        "iat": now - 600,
        "exp": now - 300,
    }
    expired_token = jwt.encode(expired_payload, key, algorithm="RS256", headers={"kid": _kid()})

    with pytest.raises(jwt.ExpiredSignatureError):
        verify_session_token(expired_token)


def test_verify_rejects_wrong_audience() -> None:
    from app.services.gateway_auth import _ISSUER, _kid, _load_or_create_key

    key = _load_or_create_key()
    now = int(time.time())
    payload = {
        "iss": _ISSUER,
        "aud": "some-other-audience",
        "company_id": "company-1",
        "deployment_id": "deployment-1",
        "external_user_id": "user-1",
        "claims": {},
        "iat": now,
        "exp": now + 300,
    }
    token = jwt.encode(payload, key, algorithm="RS256", headers={"kid": _kid()})

    with pytest.raises(jwt.InvalidAudienceError):
        verify_session_token(token)
