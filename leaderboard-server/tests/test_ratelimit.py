"""Test the per-client-IP rate limiter."""
from __future__ import annotations

from server.ratelimit import RateLimiter
from tests.conftest import _headers, _register


def _limit(client, limit, **kw):
    client.app.state.rate_limiter = RateLimiter(limit, **kw)


class TestLimiter:
    def test_fixed_window(self):
        rl = RateLimiter(2, window=60)
        assert rl.allow("s", "a", now=0)
        assert rl.allow("s", "a", now=1)
        assert not rl.allow("s", "a", now=2)
        assert rl.allow("s", "b", now=2)  # other client
        assert rl.allow("t", "a", now=2)  # other scope
        assert rl.allow("s", "a", now=60)  # new window

    def test_zero_disables(self):
        rl = RateLimiter(0)
        assert all(rl.allow("s", "a") for _ in range(100))

    def test_sweeps_expired_windows(self, monkeypatch):
        monkeypatch.setattr("server.ratelimit._PRUNE_AT", 2)
        rl = RateLimiter(1, window=10)
        rl.allow("s", "a", now=0)
        rl.allow("s", "b", now=1)
        rl.allow("s", "c", now=100)
        assert set(rl._hits) == {("s", "c")}

    def test_client_ip_without_client(self):
        class Req:
            client = None
            headers: dict = {}

        assert RateLimiter(1).client_ip(Req()) == "unknown"


class TestRoutes:
    def test_register_limited_with_envelope(self, client):
        _limit(client, 2)
        for i in range(2):
            assert client.post("/auth/register", json={"agent_name": f"b{i}"}).status_code == 200
        resp = client.post("/auth/register", json={"agent_name": "b9"})
        assert resp.status_code == 429
        detail = resp.json()["detail"]
        assert detail["code"] == "RATE_LIMITED"
        assert detail["error"]

    def test_account_creation_limited_separately(self, client):
        user = _register(client, "acct-bot")
        _limit(client, 1)
        h = _headers(user["api_key"])
        assert client.post("/accounts", json={"name": "a"}, headers=h).status_code == 200
        resp = client.post("/accounts", json={"name": "b"}, headers=h)
        assert resp.status_code == 429
        assert resp.json()["detail"]["code"] == "RATE_LIMITED"
        # register has its own bucket
        assert client.post("/auth/register", json={"agent_name": "other"}).status_code == 200

    def test_disabled_never_limits(self, client):
        _limit(client, 0)
        for i in range(15):
            assert client.post("/auth/register", json={"agent_name": f"n{i}"}).status_code == 200

    def test_forwarded_for_ignored_without_trust_proxy(self, client):
        _limit(client, 1)
        r1 = client.post("/auth/register", json={"agent_name": "x1"},
                         headers={"X-Forwarded-For": "1.1.1.1"})
        r2 = client.post("/auth/register", json={"agent_name": "x2"},
                         headers={"X-Forwarded-For": "2.2.2.2"})
        assert (r1.status_code, r2.status_code) == (200, 429)

    def test_forwarded_for_used_with_trust_proxy(self, client):
        _limit(client, 1, trust_proxy=True)

        def reg(name, ip):
            return client.post("/auth/register", json={"agent_name": name},
                               headers={"X-Forwarded-For": f"9.9.9.9, {ip}"}).status_code

        assert reg("y1", "1.1.1.1") == 200
        assert reg("y2", "2.2.2.2") == 200  # different proxy-appended client
        assert reg("y3", "1.1.1.1") == 429
        # trust_proxy on but no header: falls back to the socket peer
        assert client.post("/auth/register", json={"agent_name": "y4"}).status_code == 200
