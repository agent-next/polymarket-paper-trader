"""Bearer-token authentication for the opt-in remote MCP mode.

pm-trader is a *resource server* here: it validates access tokens minted by an
external OAuth 2.1 / OIDC issuer against that issuer's JWKS and never runs an
authorization server. The MCP authorization spec
(https://modelcontextprotocol.io/specification/) requires a resource server to

* validate the token signature, ``iss``, ``aud`` and ``exp`` and reject anything
  it cannot verify (401, per OAuth 2.1 §5.3);
* publish OAuth 2.0 Protected Resource Metadata (RFC 9728) so a client can find
  the issuer;
* never accept a token issued for another resource (audience binding).

Everything in this module is inert until the operator passes ``--auth-issuer``;
without it the server runs exactly as it did before.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt
from jwt.algorithms import get_default_algorithms
from mcp.server import MCPServer
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings

# Asymmetric signature algorithms only. ``none`` and every HS* algorithm is
# excluded on purpose: a public JWKS key is public, so accepting an HMAC token
# would let anyone mint tokens for this server.
ALLOWED_ALGS: tuple[str, ...] = (
    "RS256", "RS384", "RS512",
    "PS256", "PS384", "PS512",
    "ES256", "ES384", "ES512",
)

JWKS_CACHE_SECONDS = 300.0
_FETCH_TIMEOUT = httpx.Timeout(10.0)

# Directory under ~/.pm-trader/ that holds one hashed directory per remote user.
REMOTE_DIR = "remote"

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class RemoteAuthConfigError(ValueError):
    """Raised when the operator's --auth-* configuration is unusable."""


@dataclass
class RemoteAuthConfig:
    """The operator-supplied remote-mode configuration.

    ``issuer`` is the external authorization server that mints access tokens,
    ``audience`` is the value those tokens must carry in ``aud`` (RFC 8707
    resource binding) and ``resource_url`` is this server's canonical public
    URL, advertised in the protected-resource metadata.
    """

    issuer: str
    audience: str
    resource_url: str

    def __post_init__(self) -> None:
        """Validate the URLs up front so a misconfigured server refuses to start."""
        _check_url(self.issuer, "auth-issuer")
        _check_url(self.audience, "auth-audience")
        _check_url(self.resource_url, "resource-url")

    def auth_settings(self) -> AuthSettings:
        """Return the MCP SDK auth settings for this configuration.

        ``validate_token_resource=False`` because the verifier below checks
        ``aud`` itself (``--auth-audience``), which is the more precise check
        the SDK docstring asks a resource server to make.
        """
        return AuthSettings(
            issuer_url=self.issuer,
            resource_server_url=self.resource_url,
            validate_token_resource=False,
        )

    def token_verifier(self) -> RemoteTokenVerifier:
        """Return the bearer-token verifier for this configuration."""
        return RemoteTokenVerifier(self.issuer, self.audience)


def configure_server(server: MCPServer, config: RemoteAuthConfig) -> None:
    """Turn bearer authentication on for an already-built ``MCPServer``.

    ``MCPServer`` accepts ``auth`` and ``token_verifier`` in its constructor,
    but pm-trader registers every tool on the module-level server at import
    time, so the opt-in remote mode configures that instance instead of
    re-registering the whole tool surface. Both fields are read when the
    streamable-HTTP ASGI app is built, never before, so this is equivalent to
    passing them to the constructor.
    """
    server.settings.auth = config.auth_settings()
    server._token_verifier = config.token_verifier()


def _check_url(url: str, flag: str) -> str:
    """Return `url` unchanged after checking it is usable as a remote-MCP URL.

    HTTPS is required, except on loopback so a local IdP (and the test suite)
    can use plain HTTP. Fragments and query strings are rejected: both make the
    canonical URL ambiguous.
    """
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.hostname:
        raise RemoteAuthConfigError(f"--{flag} must be an absolute URL: {url!r}")
    if parsed.scheme == "http" and parsed.hostname not in _LOOPBACK_HOSTS:
        raise RemoteAuthConfigError(
            f"--{flag} must use https:// (http:// is only allowed on localhost): {url!r}"
        )
    if parsed.scheme not in ("http", "https"):
        raise RemoteAuthConfigError(f"--{flag} must use http:// or https://: {url!r}")
    if parsed.fragment or parsed.query:
        raise RemoteAuthConfigError(f"--{flag} must not carry a query or fragment: {url!r}")
    return url


def user_root(issuer: str, subject: str) -> Path:
    """Return the data directory owned by one token subject.

    The directory name is a hex digest of ``issuer`` and ``subject``, so no
    part of the caller-controlled ``sub`` claim ever reaches the filesystem, and
    two subjects can never share a directory. The caller's ``account`` argument
    becomes a sub-account inside it.
    """
    if not subject:
        raise RemoteAuthConfigError("token has no subject")
    digest = hashlib.sha256(f"{issuer}\x1f{subject}".encode("utf-8")).hexdigest()
    return Path.home() / ".pm-trader" / REMOTE_DIR / digest[:32]


class RemoteTokenVerifier(TokenVerifier):
    """Validate bearer JWTs against the issuer's JWKS.

    Every rejection path returns ``None``, which the SDK's bearer middleware
    turns into ``401`` with a ``WWW-Authenticate`` header. Failures never
    degrade to "accept without checking".
    """

    def __init__(
        self, issuer: str, audience: str, jwks_ttl: float = JWKS_CACHE_SECONDS
    ) -> None:
        self.issuer = issuer
        self.audience = audience
        self.jwks_ttl = jwks_ttl
        self._keys: list[dict[str, Any]] | None = None
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def _jwks(self) -> list[dict[str, Any]] | None:
        """Return the issuer's JWKS, cached for `jwks_ttl` seconds.

        Returns ``None`` when the issuer cannot be reached or its documents are
        unusable, so an unverifiable token is rejected rather than trusted.
        """
        if self._fresh():
            return self._keys
        async with self._lock:
            if self._fresh():
                return self._keys
            try:
                async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT) as client:
                    discovery = await client.get(
                        f"{self.issuer}/.well-known/openid-configuration"
                    )
                    discovery.raise_for_status()
                    jwks_uri = discovery.json()["jwks_uri"]
                    response = await client.get(jwks_uri)
                    response.raise_for_status()
                    keys = response.json()["keys"]
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                return None
            if not isinstance(keys, list):
                return None
            self._keys = keys
            self._fetched_at = time.monotonic()
            return self._keys

    def _fresh(self) -> bool:
        """True while the cached JWKS is still within its TTL."""
        return (
            self._keys is not None
            and time.monotonic() - self._fetched_at < self.jwks_ttl
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return the token's access info, or ``None`` if it is not valid here."""
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            return None
        alg = header.get("alg")
        if not isinstance(alg, str) or alg not in ALLOWED_ALGS:
            return None

        keys = await self._jwks()
        if not keys:
            return None

        public_key = _select_key(keys, header.get("kid"), alg)
        if public_key is None:
            return None

        try:
            claims = jwt.decode(
                token,
                public_key,
                algorithms=list(ALLOWED_ALGS),
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError:
            return None

        subject = claims["sub"]
        if not subject:
            return None
        return AccessToken(
            token=token,
            client_id=str(claims.get("client_id") or subject),
            scopes=_scopes(claims.get("scope")),
            expires_at=claims.get("exp"),
            subject=subject,
            claims=dict(claims),
        )


def _select_key(
    keys: list[dict[str, Any]], kid: str | None, alg: str
) -> Any | None:
    """Return the verification key for `kid`, or ``None`` if it is ambiguous.

    Without a ``kid`` the JWKS must hold exactly one key; more than one match
    is a refusal, not a guess.
    """
    matches = [k for k in keys if kid is None or k.get("kid") == kid]
    if len(matches) != 1:
        return None
    try:
        return get_default_algorithms()[alg].from_jwk(json.dumps(matches[0]))
    except (jwt.PyJWTError, ValueError, KeyError, TypeError, NotImplementedError):
        # A key the algorithm cannot use (wrong kty, undecodable n/e, ...).
        return None


def _scopes(scope: Any) -> list[str]:
    """Return the token's scopes as a list (RFC 8693 space-delimited string)."""
    if isinstance(scope, str):
        return scope.split()
    if isinstance(scope, list):
        return [str(s) for s in scope]
    return []