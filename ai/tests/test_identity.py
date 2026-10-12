import jwt
import pytest

from app.services.gateway_auth import _load_or_create_key, get_jwks
from app.services.identity import mint_delegated_token


def test_mint_delegated_token_round_trip() -> None:
    token = mint_delegated_token(
        "company-1", "ext-user-1", {"tier": "pro"}, "deployment:dep-1", "run-1", "https://partner.example.com"
    )

    key = _load_or_create_key()
    claims = jwt.decode(
        token, key.public_key(), algorithms=["RS256"], audience="https://partner.example.com"
    )
    assert claims["company_id"] == "company-1"
    assert claims["sub"] == "ext-user-1"
    assert claims["claims"] == {"tier": "pro"}
    assert claims["act"] == "deployment:dep-1"
    assert claims["run_id"] == "run-1"
    assert claims["exp"] - claims["iat"] == 60


def test_mint_delegated_token_jwks_round_trip() -> None:
    token = mint_delegated_token("company-1", "ext-user-1", {}, "act-1", "run-1", "https://partner.example.com")
    header = jwt.get_unverified_header(token)

    jwks = get_jwks()
    assert len(jwks["keys"]) == 1
    assert jwks["keys"][0]["kid"] == header["kid"]


def test_mint_delegated_token_rejects_wrong_audience_on_verify() -> None:
    token = mint_delegated_token("company-1", "ext-user-1", {}, "act-1", "run-1", "https://partner.example.com")
    key = _load_or_create_key()

    with pytest.raises(jwt.InvalidAudienceError):
        jwt.decode(token, key.public_key(), algorithms=["RS256"], audience="https://someone-else.example.com")
