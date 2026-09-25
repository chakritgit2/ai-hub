"""Hashing helpers for end-user identifiers that must never be stored raw.

PRD §6.2/§7.5: `runtime.conversations.external_user_id` and the Dynamiq memory
`user_id` scoping key are derived from the caller-supplied `external_id`, but
the raw value is never persisted - only an HMAC digest, keyed by a pepper the
caller can't guess or reverse.
"""
import hashlib
import hmac

from app.core.config import get_settings


def hash_external_user_id(company_id: str, external_user_id: str) -> str:
    """HMAC-SHA256(f"{company_id}:{external_user_id}", key=HASH_PEPPER), hex digest.

    `company_id` is folded into the hashed message (not just concatenated
    with the digest afterwards) so the same `external_user_id` hashes to a
    different value per company - this is what the PRD's cross-company
    memory-isolation test case (§13) relies on: two companies never end up
    scoping to the same Dynamiq `user_id` even by coincidence.
    """
    pepper = get_settings().HASH_PEPPER.encode()
    message = f"{company_id}:{external_user_id}".encode()
    return hmac.new(pepper, message, hashlib.sha256).hexdigest()
