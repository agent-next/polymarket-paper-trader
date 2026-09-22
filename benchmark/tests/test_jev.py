"""Tests for pm_benchmark.jev."""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx

from pm_benchmark.jev import (
    DEFAULT_BASE_URL,
    JevError,
    choice_value,
    is_jev_model,
    noul_probability,
    query_jev,
)


ANSWERS = {"probability": {"type": "noul", "noul": 0.42}}


class FakeResponse:
    def __init__(self, status_code: int, body: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._body = body
        self.text = text or (str(body) if body is not None else "")

    def json(self) -> dict:
        assert self._body is not None
        return self._body


class FakeClient:
    """Minimal duck-typed httpx.Client that records calls and close()."""

    def __init__(self, responses: list[FakeResponse]):
        self._responses = list(responses)
        self.closed = False
        self.calls: list[tuple] = []

    def post(self, url, *, json=None, headers=None):  # noqa: A002 - mirror httpx API
        self.calls.append((url, json, headers))
        return self._responses.pop(0)

    def close(self) -> None:
        self.closed = True


class TestIsJevModel:
    @pytest.mark.parametrize(
        "model",
        [
            "jev",
            "jev-1.13",
            "jev-1.13-free",
            "opencode/jev-1.13-free",
            "typesafe/jev-1.13",
            "OPENCODE/JEV-1.13",
        ],
    )
    def test_jev_ids(self, model):
        assert is_jev_model(model) is True

    @pytest.mark.parametrize(
        "model",
        ["claude-opus-4", "gpt-5", "jevity", "notjev", "opencode/gpt-5"],
    )
    def test_non_jev_ids(self, model):
        assert is_jev_model(model) is False


class TestQueryJev:
    def test_success_default_client(self):
        with respx.mock:
            respx.post(DEFAULT_BASE_URL).mock(
                return_value=httpx.Response(200, json={"answers": ANSWERS})
            )
            answers = query_jev("opencode/jev-1.13-free", "state text", {"probability": {}})
        assert answers == ANSWERS

    def test_uses_env_base_url_and_key(self, monkeypatch):
        monkeypatch.setenv("JEV_BASE_URL", "https://example.test/systemone")
        monkeypatch.setenv("OPENCODE_API_KEY", "sk-test")
        client = FakeClient([FakeResponse(200, {"answers": ANSWERS})])
        answers = query_jev("jev-1.13", "s", {"probability": {}}, http_client=client)
        assert answers == ANSWERS
        assert client.calls[0][0] == "https://example.test/systemone"
        assert client.calls[0][2]["Authorization"] == "Bearer sk-test"
        assert client.calls[0][1]["model"] == "jev-1.13"
        assert client.closed is False

    def test_explicit_args_win_and_strip_prefix(self):
        client = FakeClient([FakeResponse(200, {"answers": ANSWERS})])
        query_jev(
            "opencode/jev-1.13",
            "s",
            {"probability": {}},
            base_url="https://explicit.test/systemone",
            api_key="sk-explicit",
            http_client=client,
        )
        url, payload, headers = client.calls[0]
        assert url == "https://explicit.test/systemone"
        assert payload["model"] == "jev-1.13"
        assert headers["Authorization"] == "Bearer sk-explicit"

    def test_no_key_omits_authorization(self, monkeypatch):
        monkeypatch.delenv("JEV_API_KEY", raising=False)
        monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
        client = FakeClient([FakeResponse(200, {"answers": ANSWERS})])
        query_jev("jev-1.13-free", "s", {"probability": {}}, http_client=client)
        assert "Authorization" not in client.calls[0][2]

    def test_retries_on_429_then_succeeds(self):
        client = FakeClient(
            [FakeResponse(429), FakeResponse(200, {"answers": ANSWERS})]
        )
        with patch("pm_benchmark.jev.time.sleep") as sleep:
            answers = query_jev("jev-1.13", "s", {"probability": {}}, http_client=client)
        assert answers == ANSWERS
        sleep.assert_called_once_with(1)

    def test_exhausts_retries_on_529(self):
        client = FakeClient([FakeResponse(529), FakeResponse(529)])
        with patch("pm_benchmark.jev.time.sleep"):
            with pytest.raises(JevError, match="after retries"):
                query_jev(
                    "jev-1.13", "s", {"probability": {}}, http_client=client, max_retries=2
                )

    def test_transport_error_retries_then_raises(self):
        class BoomClient(FakeClient):
            def post(self, url, *, json=None, headers=None):  # noqa: A002
                self.calls.append((url, json, headers))
                raise httpx.ConnectError("boom")

        client = BoomClient([])
        with patch("pm_benchmark.jev.time.sleep") as sleep:
            with pytest.raises(JevError, match="request failed"):
                query_jev(
                    "jev-1.13", "s", {"probability": {}}, http_client=client, max_retries=2
                )
        sleep.assert_called_once_with(1)

    def test_http_error_not_retried(self):
        client = FakeClient([FakeResponse(401, text="unauthorized")])
        with pytest.raises(JevError, match="HTTP 401"):
            query_jev("jev-1.13", "s", {"probability": {}}, http_client=client)

    def test_missing_answers(self):
        client = FakeClient([FakeResponse(200, {"model": "jev-1.13"})])
        with pytest.raises(JevError, match="missing 'answers'"):
            query_jev("jev-1.13", "s", {"probability": {}}, http_client=client)


class TestNoulProbability:
    def test_valid(self):
        assert noul_probability({"p": {"noul": 0.9}}, "p") == 0.9

    def test_missing_question(self):
        with pytest.raises(JevError, match="Missing noul answer"):
            noul_probability({}, "p")

    def test_not_a_dict(self):
        with pytest.raises(JevError, match="Missing noul answer"):
            noul_probability({"p": "nope"}, "p")

    def test_non_numeric(self):
        with pytest.raises(JevError, match="Non-numeric noul"):
            noul_probability({"p": {"noul": "abc"}}, "p")

    def test_out_of_range(self):
        with pytest.raises(JevError, match="out of range"):
            noul_probability({"p": {"noul": 1.5}}, "p")


class TestChoiceValue:
    def test_valid(self):
        assert choice_value({"a": {"choice": "buy_yes"}}, "a") == "buy_yes"

    def test_missing(self):
        with pytest.raises(JevError, match="Missing choice answer"):
            choice_value({}, "a")

    def test_non_string(self):
        with pytest.raises(JevError, match="Missing choice answer"):
            choice_value({"a": {"choice": 5}}, "a")
