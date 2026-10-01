"""ai-gateway's own session tokens (PRD §4.4-C/§7.5/§7.6) - a 5-minute JWT embedding
end-user identity, minted server-to-server with a gateway API key
(`createDeploymentSession`) and handed to the browser, which then calls `runDeployment`/
`streamDeployment` with it directly instead of the API key (the API key itself is
"server-to-server only", PRD §6.3 - never meant to reach a browser).

ai-gateway signs with its OWN keypair, separate from console-api's (PRD §7.6's table
lists "Session token | issued/verified by ai-gateway"). `settings.GATEWAY_JWKS_PRIVATE_KEY_PATH`/
`GATEWAY_JWKS_KID` were already anticipated in `app.core.config`/`.env.example` for
exactly this; when unset (local dev default), falls back to generating once and caching
to `ai/dev/gateway-key.pem`, same convention as console-api/dev/fake-sso.php's key - not
a production key-management story (PRD §7.6 calls for 90-day rotation via multiple
`kid`-tagged keys in the JWKS, which this skeleton doesn't implement).
"""
import base64
import time
import uuid
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import get_settings

_DEV_KEY_PATH = Path(__file__).resolve().parent.parent.parent / "dev" / "gateway-key.pem"
_SESSION_TOKEN_TTL_SECONDS = 300
_ISSUER = "ai-gateway"
_AUDIENCE = "ai-gateway"


def _key_path() -> Path:
    configured = get_settings().GATEWAY_JWKS_PRIVATE_KEY_PATH
    return Path(configured) if configured else _DEV_KEY_PATH


def _kid() -> str:
    return get_settings().GATEWAY_JWKS_KID


def _load_or_create_key() -> rsa.RSAPrivateKey:
    path = _key_path()
    if path.exists():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return key


def _b64url_uint(value: int) -> str:
    length = (value.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode()


def get_jwks() -> dict:
    """The real public key, for `GET /.well-known/jwks.json` - previously always
    `{"keys": []}`, which would reject every session token/delegated-tool-JWT
    verification attempt by any external tool host (PRD §7.5)."""
    public_numbers = _load_or_create_key().public_key().public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": _kid(),
                "alg": "RS256",
                "use": "sig",
                "n": _b64url_uint(public_numbers.n),
                "e": _b64url_uint(public_numbers.e),
            }
        ]
    }


def mint_session_token(
    company_id: str, deployment_id: str, external_user_id: str, claims: dict | None = None
) -> tuple[str, int]:
    """Returns `(token, expires_in_seconds)`. `external_user_id`/`claims` are embedded
    at mint time so the gateway can ignore any identity a browser request body claims
    (PRD §7.5: "the gateway ignores identity in the body when a session token is used")."""
    key = _load_or_create_key()
    now = int(time.time())
    payload = {
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "company_id": company_id,
        "deployment_id": deployment_id,
        "external_user_id": external_user_id,
        "claims": claims or {},
        "iat": now,
        "exp": now + _SESSION_TOKEN_TTL_SECONDS,
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(payload, key, algorithm="RS256", headers={"kid": _kid()})
    return token, _SESSION_TOKEN_TTL_SECONDS


def verify_session_token(token: str) -> dict:
    """Raises `jwt.InvalidTokenError` (and subclasses) on any failure - callers map
    that to 401, same convention as `app.core.auth`'s JWT verifiers."""
    key = _load_or_create_key()
    return jwt.decode(token, key.public_key(), algorithms=["RS256"], audience=_AUDIENCE, issuer=_ISSUER)
