"""Tests for the opt-in authenticated multi-user remote MCP mode.

Offline by construction: every test generates its own RSA keypair, signs its
own tokens and serves the JWKS through the httpx mock, so no external issuer,
no network and no writes to the real ``~/.pm-trader`` are involved.
See docs/remote-mcp.md.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, call, patch

import jwt
import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from pytest_httpx import HTTPXMock
from starlette.testclient import TestClient

from pm_trader import mcp_server
from pm_trader.remote_auth import (
    JWKS_CACHE_SECONDS,
    RemoteAuthConfig,
    RemoteAuthConfigError,
    RemoteTokenVerifier,
    configure_server,
    user_root,
)

ISSUER = "https://issuer.example"
AUDIENCE = "https://pm.example/mcp"
RESOURCE_URL = "https://pm.example/mcp"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
JWKS_URL = f"{ISSUER}/jwks"
KID = "test-key-1"
OTHER_KID = "test-key-2"
HMAC_SECRET = "0" * 32  # >= 32 bytes, so PyJWT does not warn about key length

MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


# ---------------------------------------------------------------------------
# Issuer / token helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def key() -> rsa.RSAPrivateKey:
    """The issuer key: the only signature the server must accept."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_key() -> rsa.RSAPrivateKey:
    """A second key, used both as a forged signer and as JWKS noise."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(key: rsa.RSAPrivateKey, kid: str = KID) -> dict[str, Any]:
    """Return the public JWK the issuer publishes for `key`."""
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return jwk


def _token(
    key: rsa.RSAPrivateKey,
    *,
    sub: Any = "alice",
    iss: str = ISSUER,
    aud: str = AUDIENCE,
    ttl: int = 300,
    kid: str = KID,
    algorithm: str | None = "RS256",
    secret: str | bytes | rsa.RSAPrivateKey | None = None,
    omit_kid: bool = False,
    drop: tuple[str, ...] = (),
    **extra: Any,
) -> str:
    """Sign a bearer token the way the issuer would — or a forger would."""
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": iss, "aud": aud, "sub": sub, "iat": now, "exp": now + ttl, **extra,
    }
    for name in drop:
        claims.pop(name, None)
    headers = {} if omit_kid else {"kid": kid}
    if algorithm is None:  # alg: none — no signature at all
        return jwt.encode(claims, key=None, algorithm=None, headers=headers)
    return jwt.encode(
        claims, key if secret is None else secret, algorithm=algorithm,
        headers=headers,
    )


def _serve_issuer(
    httpx_mock: HTTPXMock, key: rsa.RSAPrivateKey, *, keys: list | None = None
) -> None:
    """Publish `key` (plus any extra JWKS entries) at the issuer."""
    httpx_mock.add_response(
        url=DISCOVERY_URL, json={"issuer": ISSUER, "jwks_uri": JWKS_URL}
    )
    httpx_mock.add_response(
        url=JWKS_URL, json={"keys": keys if keys is not None else [_jwk(key)]}
    )


def _verify(
    verifier: RemoteTokenVerifier, *tokens: str
) -> list[AccessToken | None]:
    """Run `verify_token` for every token inside a single event loop."""

    async def _run() -> list[AccessToken | None]:
        return [await verifier.verify_token(token) for token in tokens]

    return asyncio.run(_run())


def _config(**overrides: str) -> RemoteAuthConfig:
    values = {
        "issuer": ISSUER, "audience": AUDIENCE, "resource_url": RESOURCE_URL,
    }
    values.update(overrides)
    return RemoteAuthConfig(**values)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Operator configuration
# ---------------------------------------------------------------------------


class TestRemoteAuthConfig:
    def test_auth_settings_describe_this_server_as_the_resource(self):
        settings = _config().auth_settings()

        assert str(settings.issuer_url) == ISSUER
        assert str(settings.resource_server_url) == RESOURCE_URL
        # The verifier checks `aud` itself, so the SDK's own check stays off.
        assert settings.validate_token_resource is False
        assert settings.required_scopes is None

    def test_token_verifier_binds_to_issuer_and_audience(self):
        verifier = _config().token_verifier()

        assert isinstance(verifier, RemoteTokenVerifier)
        assert verifier.issuer == ISSUER
        assert verifier.audience == AUDIENCE
        assert verifier.jwks_ttl == JWKS_CACHE_SECONDS

    @pytest.mark.parametrize(
        "url",
        [
            "http://issuer.example",
            "ftp://issuer.example",
            "issuer.example",
            "https://",
            "https://issuer.example?x=1",
            "https://issuer.example#frag",
        ],
    )
    def test_rejects_unusable_urls(self, url):
        with pytest.raises(RemoteAuthConfigError):
            _config(issuer=url)

    def test_allows_http_on_loopback(self):
        config = _config(
            issuer="http://localhost:8080", audience="http://127.0.0.1:8000/mcp"
        )

        assert config.issuer == "http://localhost:8080"

    def test_reports_which_flag_is_bad(self):
        with pytest.raises(RemoteAuthConfigError, match="--auth-audience"):
            _config(audience="http://pm.example/mcp")


# ---------------------------------------------------------------------------
# Per-user data directories
# ---------------------------------------------------------------------------


class TestUserRoot:
    def test_directory_is_a_hash_under_the_remote_root(self, tmp_path):
        with patch.object(Path, "home", return_value=tmp_path):
            root = user_root(ISSUER, "alice")

        assert root.parent.parent == tmp_path / ".pm-trader"
        assert root.parent.name == "remote"
        assert len(root.name) == 32
        assert set(root.name) <= set("0123456789abcdef")

    def test_subject_text_never_reaches_the_path(self, tmp_path):
        with patch.object(Path, "home", return_value=tmp_path):
            root = user_root(ISSUER, "../../etc")

        assert ".." not in str(root)
        assert "etc" not in root.name

    def test_same_subject_under_another_issuer_is_another_directory(self, tmp_path):
        with patch.object(Path, "home", return_value=tmp_path):
            first = user_root(ISSUER, "alice")
            second = user_root("https://other.example", "alice")

        assert first != second

    def test_same_subject_and_issuer_is_stable(self, tmp_path):
        with patch.object(Path, "home", return_value=tmp_path):
            assert user_root(ISSUER, "alice") == user_root(ISSUER, "alice")

    def test_empty_subject_is_refused(self):
        with pytest.raises(RemoteAuthConfigError, match="subject"):
            user_root(ISSUER, "")


# ---------------------------------------------------------------------------
# Token verification: real RSA signatures, stubbed issuer
# ---------------------------------------------------------------------------


class TestTokenVerifierAccepts:
    def test_valid_token(self, key, httpx_mock):
        _serve_issuer(httpx_mock, key)
        token = _token(key, sub="alice", client_id="chatgpt", scope="trade read")

        (result,) = _verify(_config().token_verifier(), token)

        assert result is not None
        assert result.subject == "alice"
        assert result.client_id == "chatgpt"
        assert result.scopes == ["trade", "read"]
        assert result.expires_at == pytest.approx(int(time.time()) + 300, abs=5)
        assert result.claims["iss"] == ISSUER
        assert result.token == token

    def test_scope_claim_accepts_a_list_and_defaults_to_empty(self, key, httpx_mock):
        _serve_issuer(httpx_mock, key)
        as_list = _token(key, scope=["a", "b"])
        without = _token(key)

        listed, plain = _verify(_config().token_verifier(), as_list, without)

        assert listed is not None and listed.scopes == ["a", "b"]
        assert plain is not None and plain.scopes == []

    def test_token_without_kid_uses_a_single_key_jwks(self, key, httpx_mock):
        _serve_issuer(httpx_mock, key)

        (result,) = _verify(_config().token_verifier(), _token(key, omit_kid=True))

        assert result is not None and result.subject == "alice"

    def test_client_id_falls_back_to_the_subject(self, key, httpx_mock):
        _serve_issuer(httpx_mock, key)

        (result,) = _verify(_config().token_verifier(), _token(key, sub="bob"))

        assert result is not None and result.client_id == "bob"


@pytest.mark.httpx_mock(can_send_already_matched_responses=True)
class TestTokenVerifierCaching:
    def test_jwks_is_fetched_once_and_reused(self, key, httpx_mock):
        _serve_issuer(httpx_mock, key)
        verifier = _config().token_verifier()

        results = _verify(verifier, _token(key), _token(key))

        assert [r is not None for r in results] == [True, True]
        assert [str(r.url) for r in httpx_mock.get_requests()] == [
            DISCOVERY_URL, JWKS_URL,
        ]

    def test_jwks_is_refetched_once_the_ttl_expires(self, key, httpx_mock):
        _serve_issuer(httpx_mock, key)
        verifier = RemoteTokenVerifier(ISSUER, AUDIENCE, jwks_ttl=0.0)

        _verify(verifier, _token(key), _token(key))

        assert len(httpx_mock.get_requests()) == 4

    def test_concurrent_callers_share_one_fetch(self, key, httpx_mock):
        """Four simultaneous token checks must not hammer the issuer."""
        httpx_mock.add_response(url=JWKS_URL, json={"keys": [_jwk(key)]})

        async def slow_discovery(_request: Any) -> Any:
            await asyncio.sleep(0.01)  # let the other callers pile up on the lock
            return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": JWKS_URL})

        httpx_mock.add_callback(slow_discovery, url=DISCOVERY_URL)
        verifier = _config().token_verifier()

        async def _run() -> list[AccessToken | None]:
            return await asyncio.gather(
                *(verifier.verify_token(_token(key)) for _ in range(4))
            )

        results = asyncio.run(_run())

        assert all(result is not None for result in results)
        assert [str(r.url) for r in httpx_mock.get_requests()] == [
            DISCOVERY_URL, JWKS_URL,
        ]


def _forged_token(
    case: str, key: rsa.RSAPrivateKey, other: rsa.RSAPrivateKey
) -> str:
    """Return the token for one rejection case."""
    match case:
        case "expired":
            return _token(key, ttl=-60)
        case "wrong-audience":
            return _token(key, aud="https://someone-else.example/mcp")
        case "wrong-issuer":
            return _token(key, iss="https://evil.example")
        case "missing-subject":
            return _token(key, drop=("sub",))
        case "missing-expiry":
            return _token(key, drop=("exp",))
        case "unknown-kid":
            return _token(key, kid=OTHER_KID)
        case "bad-signature":
            return _token(key, secret=other)
        case "alg-none":
            return _token(key, algorithm=None)
        case "alg-hs256":
            return _token(key, algorithm="HS256", secret=HMAC_SECRET)
        case "not-a-jwt":
            return "definitely.not.a.jwt"
        case "empty-subject":
            return _token(key, sub="")
    raise AssertionError(f"unknown rejection case {case!r}")


class TestTokenVerifierRejects:
    @pytest.mark.parametrize(
        "case",
        [
            "expired", "wrong-audience", "wrong-issuer", "missing-subject",
            "missing-expiry", "unknown-kid", "bad-signature", "empty-subject",
        ],
    )
    def test_rejected(self, case, key, other_key, httpx_mock):
        _serve_issuer(
            httpx_mock, key, keys=[_jwk(key), _jwk(other_key, kid=OTHER_KID)]
        )

        (result,) = _verify(
            _config().token_verifier(), _forged_token(case, key, other_key)
        )

        assert result is None

    @pytest.mark.parametrize("case", ["alg-none", "alg-hs256", "not-a-jwt"])
    def test_rejected_from_the_header_alone(self, case, key, other_key):
        """No issuer is published: these must never reach a JWKS lookup."""
        (result,) = _verify(
            _config().token_verifier(), _forged_token(case, key, other_key)
        )

        assert result is None

    def test_ambiguous_jwks_without_kid_is_refused(
        self, key, other_key, httpx_mock
    ):
        _serve_issuer(httpx_mock, key, keys=[_jwk(key), _jwk(other_key, kid=OTHER_KID)])

        (result,) = _verify(_config().token_verifier(), _token(key, omit_kid=True))

        assert result is None

    def test_key_the_algorithm_cannot_use_is_refused(self, key, httpx_mock):
        ec_key = {"kty": "EC", "kid": KID, "crv": "P-256", "x": "eA", "y": "eQ"}
        _serve_issuer(httpx_mock, key, keys=[ec_key])

        (result,) = _verify(_config().token_verifier(), _token(key))

        assert result is None

    def test_undecodable_key_is_refused(self, key, httpx_mock):
        broken = {"kty": "RSA", "kid": KID, "n": "not base64 %%%", "e": "AQAB"}
        _serve_issuer(httpx_mock, key, keys=[broken])

        (result,) = _verify(_config().token_verifier(), _token(key))

        assert result is None

    def test_unreachable_issuer_is_never_a_pass(self, key, httpx_mock):
        httpx_mock.add_response(url=DISCOVERY_URL, status_code=500)

        (result,) = _verify(_config().token_verifier(), _token(key))

        assert result is None

    @pytest.mark.httpx_mock(can_send_already_matched_responses=True)
    @pytest.mark.parametrize(("retry_seconds", "fetches"), [(30.0, 1), (0.0, 2)])
    def test_issuer_outage_is_retried_once_per_window(
        self, key, httpx_mock, monkeypatch, retry_seconds, fetches
    ):
        """A down issuer costs one fetch per retry window, not one per request."""
        monkeypatch.setattr("pm_trader.remote_auth.JWKS_RETRY_SECONDS", retry_seconds)
        httpx_mock.add_response(url=DISCOVERY_URL, status_code=500)

        results = _verify(_config().token_verifier(), _token(key), _token(key))

        assert results == [None, None]
        assert len(httpx_mock.get_requests()) == fetches

    def test_failed_refresh_keeps_the_last_good_keys(self, key, httpx_mock):
        _serve_issuer(httpx_mock, key)
        httpx_mock.add_response(url=DISCOVERY_URL, status_code=500)
        verifier = RemoteTokenVerifier(ISSUER, AUDIENCE, jwks_ttl=0.0)

        results = _verify(verifier, _token(key), _token(key))

        assert [r is not None for r in results] == [True, True]
        assert [str(r.url) for r in httpx_mock.get_requests()] == [
            DISCOVERY_URL, JWKS_URL, DISCOVERY_URL,
        ]

    def test_discovery_without_jwks_uri_is_refused(self, key, httpx_mock):
        httpx_mock.add_response(url=DISCOVERY_URL, json={"issuer": ISSUER})

        (result,) = _verify(_config().token_verifier(), _token(key))

        assert result is None

    def test_jwks_without_a_key_list_is_refused(self, key, httpx_mock):
        httpx_mock.add_response(url=DISCOVERY_URL, json={"jwks_uri": JWKS_URL})
        httpx_mock.add_response(url=JWKS_URL, json={"keys": {"kid": KID}})

        (result,) = _verify(_config().token_verifier(), _token(key))

        assert result is None


# ---------------------------------------------------------------------------
# HTTP surface: the real MCP server, served with auth on
# ---------------------------------------------------------------------------


class _McpClient:
    """Minimal MCP streamable-HTTP client (initialize, then one tools/call)."""

    def __init__(self, client: TestClient) -> None:
        self._client = client

    def post(self, payload: dict, token: str | None = None, session: str | None = None):
        headers = dict(MCP_HEADERS)
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if session is not None:
            headers["mcp-session-id"] = session
        return self._client.post("/mcp", json=payload, headers=headers)

    def call(self, tool: str, arguments: dict, token: str) -> dict:
        """Run one tool over MCP and return the tool's JSON envelope."""
        opened = self.post(
            {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
            token,
        )
        assert opened.status_code == 200, opened.text
        session = opened.headers["mcp-session-id"]
        assert self.post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}, token, session
        ).status_code == 202

        called = self.post(
            {
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            },
            token,
            session,
        )
        assert called.status_code == 200, called.text
        data = json.loads(
            next(
                line[6:] for line in called.text.splitlines() if line.startswith("data: ")
            )
        )
        return json.loads(data["result"]["content"][0]["text"])


@contextmanager
def _remote_server(tmp_path: Path) -> Iterator[TestClient]:
    """Serve the real pm-trader MCP server with remote auth enabled.

    Global state (`_remote`, `_engine`, the server's auth settings) is restored
    on exit so the rest of the suite still sees an unauthenticated server. The
    issuer is *not* published here: a test registers it only when it actually
    presents a token, which keeps "this request never verified anything" honest.
    """
    with (
        patch.object(Path, "home", return_value=tmp_path),
        patch.object(mcp_server, "_remote", None),
        patch.object(mcp_server, "_engine", None),
        patch.object(mcp_server.mcp.settings, "auth", None),
        patch.object(mcp_server.mcp, "_token_verifier", None),
    ):
        config = _config()
        mcp_server._remote = config
        configure_server(mcp_server.mcp, config)
        app = mcp_server.mcp.streamable_http_app(host="127.0.0.1")
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            yield client


@contextmanager
def _as_user(token: AccessToken) -> Iterator[None]:
    """Run a block as if the MCP SDK had authenticated this access token."""
    handle = auth_context_var.set(AuthenticatedUser(token))
    try:
        yield
    finally:
        auth_context_var.reset(handle)


class TestProtectedResourceMetadata:
    def test_serves_the_issuer_for_this_resource(self, tmp_path):
        with _remote_server(tmp_path) as client:
            response = client.get("/.well-known/oauth-protected-resource/mcp")

        assert response.status_code == 200
        assert response.json() == {
            "resource": RESOURCE_URL,
            "authorization_servers": [ISSUER],
            "bearer_methods_supported": ["header"],
        }

    def test_metadata_needs_no_token(self, tmp_path):
        with _remote_server(tmp_path) as client:
            assert client.get("/.well-known/oauth-protected-resource/mcp").status_code == 200


class TestHttpRequiresAToken:
    def test_missing_token_is_401_with_www_authenticate(self, tmp_path):
        with _remote_server(tmp_path) as client:
            response = _McpClient(client).post({"jsonrpc": "2.0", "id": 1})

        assert response.status_code == 401
        challenge = response.headers["www-authenticate"]
        assert challenge.startswith('Bearer error="invalid_token"')
        assert (
            'resource_metadata="https://pm.example/.well-known/'
            'oauth-protected-resource/mcp"' in challenge
        )
        assert response.json() == {
            "error": "invalid_token", "error_description": "Authentication required",
        }

    @pytest.mark.parametrize(
        "case", ["expired", "wrong-audience", "wrong-issuer", "bad-signature"]
    )
    def test_invalid_token_is_401(self, case, tmp_path, key, other_key, httpx_mock):
        _serve_issuer(httpx_mock, key)
        with _remote_server(tmp_path) as client:
            response = _McpClient(client).post(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                _forged_token(case, key, other_key),
            )

        assert response.status_code == 401
        assert response.json()["error"] == "invalid_token"

    @pytest.mark.parametrize("case", ["alg-none", "alg-hs256", "not-a-jwt"])
    def test_token_rejected_before_the_issuer_is_consulted(
        self, case, tmp_path, key, other_key
    ):
        """A header we refuse is rejected without ever fetching the JWKS."""
        with (
            _remote_server(tmp_path) as client,
            patch.object(RemoteTokenVerifier, "_jwks", new_callable=AsyncMock) as jwks,
        ):
            response = _McpClient(client).post(
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                _forged_token(case, key, other_key),
            )

        assert response.status_code == 401
        jwks.assert_not_called()


class TestPerUserIsolation:
    def test_valid_token_lands_in_that_subjects_directory(
        self, tmp_path, key, httpx_mock
    ):
        _serve_issuer(httpx_mock, key)
        with _remote_server(tmp_path) as client:
            envelope = _McpClient(client).call(
                "init_account", {"balance": 1234.5}, _token(key, sub="alice")
            )

        assert envelope == {
            "ok": True, "data": {"cash": 1234.5, "starting_balance": 1234.5},
        }
        alice_db = (
            tmp_path / ".pm-trader" / "remote" / user_root(ISSUER, "alice").name
            / "default" / "paper.db"
        )
        assert alice_db.is_file()

    def test_two_users_with_the_same_account_name_are_isolated(
        self, tmp_path, key, httpx_mock
    ):
        _serve_issuer(httpx_mock, key)
        with _remote_server(tmp_path) as client:
            mcp = _McpClient(client)
            mcp.call(
                "init_account", {"balance": 500, "account": "shared"},
                _token(key, sub="alice"),
            )
            mcp.call(
                "init_account", {"balance": 700, "account": "shared"},
                _token(key, sub="bob"),
            )

            alice = mcp.call(
                "get_balance", {"account": "shared"}, _token(key, sub="alice")
            )
            bob = mcp.call("get_balance", {"account": "shared"}, _token(key, sub="bob"))

        assert alice["data"]["cash"] == 500
        assert bob["data"]["cash"] == 700
        remote = tmp_path / ".pm-trader" / "remote"
        assert (remote / user_root(ISSUER, "alice").name / "shared" / "paper.db").is_file()
        assert (remote / user_root(ISSUER, "bob").name / "shared" / "paper.db").is_file()

    def test_one_user_cannot_read_another_users_trades(
        self, tmp_path, key, httpx_mock
    ):
        _serve_issuer(httpx_mock, key)
        with _remote_server(tmp_path) as client:
            mcp = _McpClient(client)
            mcp.call(
                "init_account", {"balance": 500, "account": "shared"},
                _token(key, sub="alice"),
            )

            envelope = mcp.call(
                "history", {"account": "shared"}, _token(key, sub="bob")
            )

        assert envelope == {
            "ok": False,
            "error": "Account not initialized. Run 'pm-trader init' first.",
            "code": "NOT_INITIALIZED",
        }

    def test_account_argument_cannot_escape_the_users_directory(
        self, tmp_path, key, httpx_mock
    ):
        _serve_issuer(httpx_mock, key)
        with _remote_server(tmp_path) as client:
            envelope = _McpClient(client).call(
                "init_account", {"account": "../escape"}, _token(key, sub="alice")
            )

        assert envelope["ok"] is False
        assert "Invalid account name" in envelope["error"]
        assert not (tmp_path / ".pm-trader" / "remote" / "escape").exists()

    def test_tool_call_without_a_verified_token_is_refused(self, tmp_path):
        """Remote mode never silently falls back to the local data root."""
        with (
            patch.object(Path, "home", return_value=tmp_path),
            patch.object(mcp_server, "_remote", _config()),
            patch.object(mcp_server, "_engine", None),
        ):
            envelope = json.loads(mcp_server.get_balance())

        assert envelope["ok"] is False
        assert envelope["error"] == "Remote MCP tools require a verified bearer token"

    @pytest.mark.parametrize("missing", ["subject", "issuer"])
    def test_incomplete_principal_is_refused(self, missing, tmp_path):
        """The directory hash needs both halves of the principal."""
        claims = {} if missing == "issuer" else {"iss": ISSUER}
        token = AccessToken(
            token="t", client_id="c", scopes=[],
            subject=None if missing == "subject" else "alice", claims=claims,
        )
        with (
            patch.object(Path, "home", return_value=tmp_path),
            patch.object(mcp_server, "_remote", _config()),
            patch.object(mcp_server, "_engine", None),
            _as_user(token),
        ):
            envelope = json.loads(mcp_server.get_balance())

        assert envelope["ok"] is False
        assert envelope["error"] == "Remote MCP tools require a verified bearer token"


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


_REMOTE_ARGS = [
    "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "9001",
    "--auth-issuer", ISSUER,
    "--auth-audience", AUDIENCE,
    "--resource-url", RESOURCE_URL,
]


class TestCliFlags:
    def test_remote_mode_is_off_by_default(self):
        args = mcp_server._build_arg_parser().parse_args([])

        assert (args.auth_issuer, args.auth_audience, args.resource_url) == (
            None, None, None
        )
        assert mcp_server._auth_config(args) is None

    def test_flags_parse_into_a_config(self):
        args = mcp_server._build_arg_parser().parse_args(_REMOTE_ARGS)

        config = mcp_server._auth_config(args)

        assert config == RemoteAuthConfig(
            issuer=ISSUER, audience=AUDIENCE, resource_url=RESOURCE_URL
        )

    def test_environment_variables_provide_the_defaults(self, monkeypatch):
        monkeypatch.setenv("PM_TRADER_AUTH_ISSUER", ISSUER)
        monkeypatch.setenv("PM_TRADER_AUTH_AUDIENCE", AUDIENCE)
        monkeypatch.setenv("PM_TRADER_RESOURCE_URL", RESOURCE_URL)

        config = mcp_server._auth_config(mcp_server._build_arg_parser().parse_args([]))

        assert config == RemoteAuthConfig(
            issuer=ISSUER, audience=AUDIENCE, resource_url=RESOURCE_URL
        )

    def test_partial_configuration_is_refused(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            mcp_server.main(["--auth-issuer", ISSUER])

        assert excinfo.value.code == 2
        err = capsys.readouterr().err
        assert "--auth-audience" in err and "--resource-url" in err

    def test_auth_needs_the_http_transport(self, capsys):
        argv = ["--auth-issuer", ISSUER, "--auth-audience", AUDIENCE,
                "--resource-url", RESOURCE_URL]

        with pytest.raises(SystemExit) as excinfo:
            mcp_server.main(argv)

        assert excinfo.value.code == 2
        assert "--transport streamable-http" in capsys.readouterr().err

    def test_bad_issuer_url_is_refused(self, capsys):
        argv = ["--auth-issuer", "http://issuer.example", "--auth-audience", AUDIENCE,
                "--resource-url", RESOURCE_URL, "--transport", "streamable-http"]

        with pytest.raises(SystemExit):
            mcp_server.main(argv)

        assert "https://" in capsys.readouterr().err


class TestRunWiring:
    def test_remote_run_configures_auth_and_drops_local_tools(self, capsys):
        with (
            patch.object(mcp_server, "_remote", None),
            patch.object(mcp_server.mcp, "run") as mock_run,
            patch.object(mcp_server.mcp, "remove_tool") as mock_remove,
            patch.object(mcp_server.mcp.settings, "auth", None),
            patch.object(mcp_server.mcp, "_token_verifier", None),
        ):
            mcp_server._run("streamable-http", "0.0.0.0", 9001, _config())

            mock_remove.assert_has_calls(
                [call(name) for name in mcp_server._LOCAL_ONLY_TOOLS]
            )
            mock_run.assert_called_once_with(
                transport="streamable-http", host="0.0.0.0", port=9001
            )
            assert str(
                mcp_server.mcp.settings.auth.resource_server_url
            ) == RESOURCE_URL
            assert isinstance(mcp_server.mcp._token_verifier, RemoteTokenVerifier)
            assert mcp_server._remote == _config()
            # Authenticated, so the unauthenticated-bind warning stays silent.
            assert "UNAUTHENTICATED" not in capsys.readouterr().err

    def test_main_enables_the_mode_end_to_end(self, capsys):
        with (
            patch.object(mcp_server, "_remote", None),
            patch.object(mcp_server.mcp, "run") as mock_run,
            patch.object(mcp_server.mcp, "remove_tool"),
            patch.object(mcp_server.mcp.settings, "auth", None),
            patch.object(mcp_server.mcp, "_token_verifier", None),
        ):
            mcp_server.main(_REMOTE_ARGS)

            mock_run.assert_called_once_with(
                transport="streamable-http", host="0.0.0.0", port=9001
            )
            assert mcp_server._remote == _config()
            assert "UNAUTHENTICATED" not in capsys.readouterr().err

    def test_unauthenticated_bind_still_warns(self, capsys):
        with (
            patch.object(mcp_server, "_remote", None),
            patch.object(mcp_server.mcp, "run"),
            patch.object(mcp_server.mcp, "remove_tool"),
        ):
            mcp_server._run("streamable-http", "0.0.0.0", 9001)

        assert "UNAUTHENTICATED" in capsys.readouterr().err

    def test_stdio_ignores_remote_mode(self, capsys):
        with (
            patch.object(mcp_server, "_remote", None),
            patch.object(mcp_server.mcp, "run") as mock_run,
            patch.object(mcp_server.mcp, "remove_tool") as mock_remove,
        ):
            mcp_server._run("stdio", "127.0.0.1", 8000)

        mock_run.assert_called_once_with()
        mock_remove.assert_not_called()
        assert "UNAUTHENTICATED" not in capsys.readouterr().err