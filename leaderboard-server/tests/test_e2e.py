"""End-to-end smoke test: full agent flow."""
from __future__ import annotations

import pytest


def test_full_agent_flow(client_e2e):
    """Complete agent lifecycle: register -> account -> buy -> sell -> stats -> leaderboard."""
    client = client_e2e

    # 1. Register
    resp = client.post("/auth/register", json={"agent_name": "e2e-bot"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    api_key = data["api_key"]
    assert api_key.startswith("lb_sk_")
    headers = {"Authorization": f"Bearer {api_key}"}

    # 2. Create account
    resp = client.post("/accounts", json={"name": "main"}, headers=headers)
    assert resp.status_code == 200
    account_id = resp.json()["data"]["id"]
    assert resp.json()["data"]["cash"] == 10000.0

    # 3. Buy — spend $500 on YES
    resp = client.post("/trade/buy", json={
        "account_id": account_id,
        "market_slug": "e2e-test-market",
        "outcome": "yes",
        "amount_usd": 500,
        "order_type": "fok",
    }, headers=headers)
    assert resp.status_code == 200
    buy_data = resp.json()["data"]
    assert buy_data["trade"]["side"] == "buy"
    shares_bought = buy_data["trade"]["shares"]
    assert shares_bought > 0

    # 4. Check portfolio — should have 1 position
    resp = client.get(f"/accounts/{account_id}/portfolio", headers=headers)
    assert resp.status_code == 200
    portfolio = resp.json()["data"]
    assert len(portfolio) == 1
    assert portfolio[0]["outcome"] == "yes"
    assert portfolio[0]["shares"] > 0

    # 5. Check balance — cash should be reduced
    resp = client.get(f"/accounts/{account_id}/balance", headers=headers)
    assert resp.status_code == 200
    balance = resp.json()["data"]
    assert balance["cash"] < 10000
    assert balance["positions_value"] > 0

    # 6. Buy more (different trades to build up count)
    for _ in range(8):
        resp = client.post("/trade/buy", json={
            "account_id": account_id,
            "market_slug": "e2e-test-market",
            "outcome": "yes",
            "amount_usd": 100,
            "order_type": "fok",
        }, headers=headers)
        assert resp.status_code == 200

    # 7. Check history — should have 9 trades (1 + 8)
    resp = client.get(f"/accounts/{account_id}/history", headers=headers)
    assert resp.status_code == 200
    history = resp.json()["data"]
    assert len(history) == 9

    # 8. Sell some shares
    resp = client.post("/trade/sell", json={
        "account_id": account_id,
        "market_slug": "e2e-test-market",
        "outcome": "yes",
        "shares": 50,
        "order_type": "fok",
    }, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["trade"]["side"] == "sell"

    # 9. Check stats — should have 10 trades now
    resp = client.get(f"/accounts/{account_id}/stats", headers=headers)
    assert resp.status_code == 200
    stats = resp.json()["data"]
    assert stats["total_trades"] == 10

    # 10. Check leaderboard — should appear (10 trades qualifies)
    resp = client.get("/leaderboard")
    assert resp.status_code == 200
    lb = resp.json()["data"]
    assert len(lb) >= 1
    assert any(e["agent_name"] == "e2e-bot" for e in lb)

    # 11. Check user profile
    resp = client.get("/leaderboard/users/e2e-bot")
    assert resp.status_code == 200
    assert resp.json()["data"]["agent_name"] == "e2e-bot"

    # 12. Check website homepage
    resp = client.get("/")
    assert resp.status_code == 200
    assert "e2e-bot" in resp.text

    # 13. Check user page
    resp = client.get("/u/e2e-bot")
    assert resp.status_code == 200
    assert "e2e-bot" in resp.text

    # 14. Check account page
    resp = client.get(f"/a/{account_id}")
    assert resp.status_code == 200
    assert "main" in resp.text

    # 15. Place a limit order
    resp = client.post(f"/accounts/{account_id}/orders", json={
        "market_slug": "e2e-test-market",
        "market_condition_id": "0xe2e",
        "outcome": "yes",
        "side": "buy",
        "amount": 200,
        "limit_price": 0.60,
    }, headers=headers)
    assert resp.status_code == 200
    order_id = resp.json()["data"]["id"]

    # 16. List orders
    resp = client.get(f"/accounts/{account_id}/orders", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 1

    # 17. Cancel order
    resp = client.delete(f"/accounts/{account_id}/orders/{order_id}", headers=headers)
    assert resp.status_code == 200


def test_multi_user_leaderboard(client_e2e):
    """Multiple agents compete, leaderboard ranks them."""
    client = client_e2e

    agents = []
    for i in range(3):
        name = f"multi-bot-{i}"
        resp = client.post("/auth/register", json={"agent_name": name})
        api_key = resp.json()["data"]["api_key"]
        headers = {"Authorization": f"Bearer {api_key}"}
        resp = client.post("/accounts", json={"name": "default"}, headers=headers)
        account_id = resp.json()["data"]["id"]
        agents.append((name, account_id, headers))

    # Each agent makes 10 trades
    for name, account_id, headers in agents:
        for _ in range(10):
            client.post("/trade/buy", json={
                "account_id": account_id,
                "market_slug": "e2e-test-market",
                "outcome": "yes",
                "amount_usd": 100,
                "order_type": "fok",
            }, headers=headers)

    # All should appear on leaderboard
    resp = client.get("/leaderboard")
    assert resp.status_code == 200
    lb = resp.json()["data"]
    assert len(lb) == 3

    # PK comparison
    _, acc_a, _ = agents[0]
    _, acc_b, _ = agents[1]
    resp = client.get(f"/leaderboard/pk/{acc_a}/{acc_b}")
    assert resp.status_code == 200
    pk = resp.json()["data"]
    assert "a" in pk and "b" in pk
