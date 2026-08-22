"""Supabase JWT verification.

The frontend obtains an access token from Supabase and sends it as
`Authorization: Bearer <token>`. This module turns that string into a verified set of
claims, or raises :class:`UnauthorizedError` — it never trusts a token it could not
check, and it never falls back to an unverified decode.

**Two verification paths, tried in that order.**

*JWKS (asymmetric).* Supabase projects using RS256/ES256 publish their public keys at
`{SUPABASE_URL}/auth/v1/.well-known/jwks.json`. The key set is fetched once and cached,
because it changes only when a project rotates keys, and re-fetched if a token arrives
with an unknown `kid` — which is exactly what a rotation looks like from here.

The fetch uses `httpx`, not PyJWT's own `PyJWKClient`. That client fetches with
`urllib`, which no test in this suite can intercept: `respx` would not see it and the
socket-blocking harness would not stop it, so a test that believed it was offline would
quietly reach the network. Using the same HTTP library as every other outbound call
keeps that guarantee real.

*Shared secret (HS256).* Older Supabase projects sign with a symmetric secret. If
`SUPABASE_JWT_SECRET` is set, it is used for HS256 tokens. This is the documented
fallback, kept because a project that has not migrated would otherwise be unable to
authenticate at all.

Only PyJWT is used. The Supabase Python SDK would add a dependency for something that
is, at this layer, an ordinary signature check.

Nothing here reads `SUPABASE_SERVICE_ROLE_KEY`. That key bypasses every policy in the
project; it belongs to server-to-server storage calls, never to request authentication,
and it must never be logged or returned.
"""

import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

from app.core.config import settings
from app.core.errors import UnauthorizedError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Supabase issues access tokens with this audience for signed-in users.
EXPECTED_AUDIENCE = "authenticated"

ASYMMETRIC_ALGORITHMS = ("RS256", "ES256", "RS512", "ES512")
SYMMETRIC_ALGORITHMS = ("HS256",)

# A key set changes only when a project rotates keys, so an hour is conservative. An
# unknown `kid` forces a refetch regardless, which is what makes rotation work without
# a restart.
JWKS_CACHE_SECONDS = 3600

_jwks_cache: tuple[float, jwt.PyJWKSet] | None = None


@dataclass(frozen=True)
class TokenClaims:
    """The parts of a verified token this application acts on."""

    subject: str
    email: str | None
    raw: dict[str, Any]


def jwks_url() -> str | None:
    """Where this project publishes its signing keys, if it is configured."""
    if not settings.SUPABASE_URL:
        return None
    return f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json"


def _fetch_jwks() -> jwt.PyJWKSet:
    """Fetch the project's key set. Raises on any transport or parse failure."""
    url = jwks_url()
    if url is None:
        raise UnauthorizedError(
            "Token is asymmetrically signed but SUPABASE_URL is not configured, "
            "so its signing key cannot be fetched."
        )
    response = httpx.get(url, timeout=settings.PROVIDER_TIMEOUT_S)
    response.raise_for_status()
    return jwt.PyJWKSet.from_dict(response.json())


def get_jwks(*, refresh: bool = False) -> jwt.PyJWKSet:
    """The cached key set, fetching it if absent, stale, or explicitly refreshed."""
    global _jwks_cache
    if not refresh and _jwks_cache is not None:
        fetched_at, keys = _jwks_cache
        if time.monotonic() - fetched_at < JWKS_CACHE_SECONDS:
            return keys

    keys = _fetch_jwks()
    _jwks_cache = (time.monotonic(), keys)
    return keys


def reset_jwks_client() -> None:
    """Drop the cached key set. For tests, and for a configuration change at runtime."""
    global _jwks_cache
    _jwks_cache = None


def _unverified_algorithm(token: str) -> str | None:
    """Read the `alg` header without trusting anything in the token.

    Only used to choose *which* verification path to take. The signature is always
    checked afterwards; a forged header can at worst send a token down a path whose
    key will not validate it.
    """
    try:
        return jwt.get_unverified_header(token).get("alg")
    except jwt.PyJWTError:
        return None


def _key_by_id(keys: jwt.PyJWKSet, kid: str | None):
    for candidate in keys.keys:
        if candidate.key_id == kid:
            return candidate.key
    return None


def _decode_with_jwks(token: str) -> dict[str, Any]:
    kid = jwt.get_unverified_header(token).get("kid")

    try:
        signing_key = _key_by_id(get_jwks(), kid)
        if signing_key is None:
            # An unfamiliar `kid` is what a key rotation looks like from here, so try
            # once more with a fresh key set before giving up.
            signing_key = _key_by_id(get_jwks(refresh=True), kid)
    except (jwt.PyJWTError, httpx.HTTPError, OSError, ValueError) as exc:
        # A network failure must not read as "valid". It reads as "cannot verify".
        logger.warning("jwks_fetch_failed", extra={"error": str(exc)})
        raise UnauthorizedError("Could not retrieve the signing key to verify this token.") from exc

    if signing_key is None:
        raise UnauthorizedError("No published signing key matches this token.")

    return jwt.decode(
        token,
        signing_key,
        algorithms=list(ASYMMETRIC_ALGORITHMS),
        audience=EXPECTED_AUDIENCE,
        options={"verify_exp": True, "verify_aud": True},
    )


def _decode_with_secret(token: str) -> dict[str, Any]:
    if not settings.SUPABASE_JWT_SECRET:
        raise UnauthorizedError(
            "Token is symmetrically signed but SUPABASE_JWT_SECRET is not configured."
        )
    return jwt.decode(
        token,
        settings.SUPABASE_JWT_SECRET,
        algorithms=list(SYMMETRIC_ALGORITHMS),
        audience=EXPECTED_AUDIENCE,
        options={"verify_exp": True, "verify_aud": True},
    )


def verify_token(token: str) -> TokenClaims:
    """Verify a Supabase access token and return its claims.

    Raises :class:`UnauthorizedError` for anything that is not a currently valid token:
    a bad signature, an expired token, the wrong audience, an unsupported algorithm, or
    a key that cannot be fetched. The message is deliberately non-specific about which,
    since a caller has no legitimate use for that distinction.
    """
    if not token:
        raise UnauthorizedError("An access token is required.")

    algorithm = _unverified_algorithm(token)
    if algorithm is None:
        raise UnauthorizedError("The access token is malformed.")

    try:
        if algorithm in ASYMMETRIC_ALGORITHMS:
            payload = _decode_with_jwks(token)
        elif algorithm in SYMMETRIC_ALGORITHMS:
            payload = _decode_with_secret(token)
        else:
            # `none` lands here, which is the point: an unsigned token is never valid.
            raise UnauthorizedError(f"Unsupported token algorithm: {algorithm}.")
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("The access token has expired.") from exc
    except jwt.PyJWTError as exc:
        logger.warning("token_rejected", extra={"reason": type(exc).__name__})
        raise UnauthorizedError("The access token could not be verified.") from exc

    subject = payload.get("sub")
    if not subject:
        raise UnauthorizedError("The access token carries no subject.")

    return TokenClaims(subject=str(subject), email=payload.get("email"), raw=payload)


def bearer_token(header_value: str | None) -> str:
    """Pull the token out of an `Authorization` header.

    The scheme is compared case-insensitively, as RFC 7235 requires.
    """
    if not header_value:
        raise UnauthorizedError("Authorization header is missing.")

    scheme, _, credentials = header_value.partition(" ")
    if scheme.lower() != "bearer" or not credentials.strip():
        raise UnauthorizedError("Authorization header must be of the form 'Bearer <token>'.")

    return credentials.strip()
