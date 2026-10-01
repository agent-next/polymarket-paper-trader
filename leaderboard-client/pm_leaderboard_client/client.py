"""Agent SDK client for Polymarket Leaderboard."""
from __future__ import annotations

import httpx


class AgentError(Exception):
    """Raised when the leaderboard API returns an error."""

    def __init__(self, message: str, code: str | None = None, api_key: str | None = None):
        super().__init__(message)
        self.code = code
        self.api_key = api_key


class Agent:
    """Polymarket Leaderboard agent.

    Two modes:
        # Register new agent
        agent = Agent("http://localhost:8000", name="my-bot", model="claude-opus-4")

        # Reconnect with existing API key
        agent = Agent("http://localhost:8000", api_key="lb_sk_...")
    """

    def __init__(
        self,
        base_url: str,
        *,
        name: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        account_name: str = "default",
        http_client: httpx.Client | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._http = http_client or httpx.Client(base_url=self._base_url, timeout=30.0)
        self._owns_http = http_client is None
        self._api_key: str | None = None
        self._account_id: int | None = None
        self._agent_name: str | None = None

        if api_key is not None:
            self._api_key = api_key
            self._resolve_account(account_name)
        elif name is not None:
            self._register(name, model, account_name)
        else:
            raise AgentError("Provide either name (new agent) or api_key (reconnect)")

    # -- Properties --

    @property
    def api_key(self) -> str:
        assert self._api_key is not None
        return self._api_key

    @property
    def account_id(self) -> int:
        assert self._account_id is not None
        return self._account_id

    @property
    def agent_name(self) -> str | None:
        return self._agent_name

    # -- Trading --

    def buy(
        self,
        market_slug: str,
        outcome: str,
        amount: float,
        order_type: str = "fok",
    ) -> dict:
        return self._post("/trade/buy", {
            "account_id": self.account_id,
            "market_slug": market_slug,
            "outcome": outcome,
            "amount_usd": amount,
            "order_type": order_type,
        })

    def sell(
        self,
        market_slug: str,
        outcome: str,
        shares: float,
        order_type: str = "fok",
    ) -> dict:
        return self._post("/trade/sell", {
            "account_id": self.account_id,
            "market_slug": market_slug,
            "outcome": outcome,
            "shares": shares,
            "order_type": order_type,
        })

    # -- Portfolio --

    def portfolio(self) -> list[dict]:
        return self._get(f"/accounts/{self.account_id}/portfolio")

    def balance(self) -> dict:
        return self._get(f"/accounts/{self.account_id}/balance")

    def history(self, limit: int = 50) -> list[dict]:
        return self._get(f"/accounts/{self.account_id}/history", params={"limit": limit})

    def stats(self) -> dict:
        return self._get(f"/accounts/{self.account_id}/stats")

    # -- Leaderboard --

    def leaderboard(self) -> list[dict]:
        resp = self._http.get("/leaderboard")
        body = resp.json()
        if not body.get("ok"):
            raise AgentError(body.get("error", "Unknown error"), body.get("code"))
        return body["data"]

    # -- Internals --

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _register(self, name: str, model: str | None, account_name: str) -> None:
        payload: dict = {"agent_name": name}
        if model is not None:
            payload["model"] = model
        resp = self._http.post("/auth/register", json=payload)
        body = resp.json()
        if resp.status_code == 409:
            raise AgentError("Agent name already taken", "AGENT_NAME_TAKEN")
        if not body.get("ok"):
            raise AgentError(body.get("error", "Registration failed"))
        self._api_key = body["data"]["api_key"]
        self._agent_name = body["data"]["agent_name"]
        try:
            self._create_account(account_name)
        except Exception:
            # The first POST may have landed before failing: find-or-create.
            try:
                self._resolve_account(account_name)
            except Exception as e:
                raise AgentError(
                    f"Registered, but account creation failed: {e}. "
                    "Reconnect with api_key=... to retry.",
                    getattr(e, "code", None),
                    api_key=self._api_key,
                ) from e

    def _create_account(self, account_name: str) -> None:
        resp = self._http.post(
            "/accounts",
            json={"name": account_name},
            headers=self._headers(),
        )
        body = resp.json()
        if not body.get("ok"):
            raise AgentError(body.get("error", "Account creation failed"))
        self._account_id = body["data"]["id"]

    def _resolve_account(self, account_name: str) -> None:
        resp = self._http.get("/accounts", headers=self._headers())
        if resp.status_code == 401:
            raise AgentError("Invalid API key", "INVALID_API_KEY")
        body = resp.json()
        if not body.get("ok"):
            raise AgentError(body.get("error", "Failed to list accounts"))
        accounts = body["data"]
        for acc in accounts:
            if acc["name"] == account_name:
                self._account_id = acc["id"]
                return
        # No matching account — create it
        self._create_account(account_name)

    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = self._http.get(path, headers=self._headers(), params=params)
        return self._unwrap(resp)

    def _post(self, path: str, payload: dict) -> dict:
        resp = self._http.post(path, json=payload, headers=self._headers())
        return self._unwrap(resp)

    @staticmethod
    def _unwrap(resp: httpx.Response) -> dict:
        try:
            body = resp.json()
        except ValueError:
            raise AgentError(
                f"HTTP {resp.status_code}: {resp.text[:200] or 'Request failed'}"
            ) from None
        if not isinstance(body, dict):
            raise AgentError("Request failed")
        if "detail" in body:
            # FastAPI HTTPException / validation envelope: {"detail": ...}
            detail = body["detail"]
            if isinstance(detail, dict):
                raise AgentError(
                    detail.get("error", "Request failed"), detail.get("code")
                )
            if isinstance(detail, list):
                msgs = "; ".join(
                    str(d.get("msg", d)) if isinstance(d, dict) else str(d)
                    for d in detail
                )
                raise AgentError(msgs or "Request failed", "VALIDATION_ERROR")
            raise AgentError(str(detail))
        if not body.get("ok"):
            raise AgentError(
                body.get("error", "Request failed"),
                body.get("code"),
            )
        return body["data"]

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> Agent:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
