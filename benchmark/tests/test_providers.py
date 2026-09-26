"""Tests for pm_benchmark.providers."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pm_benchmark.config import LLMConfig
from pm_benchmark.jev import JevError
from pm_benchmark.providers import (
    LLMError,
    MarketDecision,
    _validate_decision,
    parse_decision,
    query_model,
)


VALID_JSON = (
    '{"probability": 0.65, "action": "buy_yes", '
    '"confidence": "high", "amount_usd": 250.0, '
    '"reasoning": "Strong momentum"}'
)


class TestParseDecision:
    def test_plain_json(self):
        d = parse_decision(VALID_JSON)
        assert d.probability == 0.65
        assert d.action == "buy_yes"
        assert d.confidence == "high"
        assert d.amount_usd == 250.0
        assert d.reasoning == "Strong momentum"

    def test_with_markdown_fences(self):
        text = "Here's my analysis:\n```json\n" + VALID_JSON + "\n```\n"
        d = parse_decision(text)
        assert d.action == "buy_yes"

    def test_with_surrounding_text(self):
        text = "I think we should buy.\n" + VALID_JSON + "\nGood luck!"
        d = parse_decision(text)
        assert d.action == "buy_yes"

    def test_no_json(self):
        with pytest.raises(LLMError, match="No JSON object found"):
            parse_decision("no json here")

    def test_invalid_json(self):
        with pytest.raises(LLMError, match="Invalid JSON"):
            parse_decision("{bad json}")


class TestValidateDecision:
    def test_skip_action(self):
        d = _validate_decision({
            "probability": 0.5,
            "action": "skip",
            "confidence": "low",
            "amount_usd": 0,
            "reasoning": "Too uncertain",
        })
        assert d.action == "skip"
        assert d.amount_usd == 0

    def test_buy_no(self):
        d = _validate_decision({
            "probability": 0.2,
            "action": "buy_no",
            "confidence": "high",
            "amount_usd": 500,
            "reasoning": "Very unlikely",
        })
        assert d.action == "buy_no"

    def test_missing_probability(self):
        with pytest.raises(LLMError, match="Missing 'probability'"):
            _validate_decision({"action": "skip"})

    def test_probability_out_of_range(self):
        with pytest.raises(LLMError, match="probability must be"):
            _validate_decision({"probability": 1.5, "action": "skip"})

    def test_invalid_action(self):
        with pytest.raises(LLMError, match="Invalid action"):
            _validate_decision({"probability": 0.5, "action": "hold"})

    def test_invalid_confidence(self):
        with pytest.raises(LLMError, match="Invalid confidence"):
            _validate_decision({
                "probability": 0.5, "action": "skip", "confidence": "very high",
            })

    def test_zero_amount_for_buy(self):
        with pytest.raises(LLMError, match="amount_usd must be positive"):
            _validate_decision({
                "probability": 0.5, "action": "buy_yes",
                "confidence": "high", "amount_usd": 0,
            })

    def test_defaults(self):
        d = _validate_decision({"probability": 0.5})
        assert d.action == "skip"
        assert d.confidence == "medium"
        assert d.reasoning == ""


class TestQueryModel:
    @patch("pm_benchmark.providers.litellm.completion")
    def test_success(self, mock_completion):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = VALID_JSON
        mock_completion.return_value = mock_response

        cfg = LLMConfig(model="test-model")
        result = query_model(cfg, "analyze", "system")
        assert result == VALID_JSON
        mock_completion.assert_called_once()
        _, kwargs = mock_completion.call_args
        assert "timeout" not in kwargs
        assert "seed" not in kwargs

    @patch("pm_benchmark.providers.litellm.completion")
    def test_with_api_key_env(self, mock_completion, monkeypatch):
        monkeypatch.setenv("TEST_KEY", "sk-test123")
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "ok"
        mock_completion.return_value = mock_response

        cfg = LLMConfig(model="test-model", api_key_env="TEST_KEY")
        result = query_model(cfg, "analyze", "system")
        assert result == "ok"
        _, kwargs = mock_completion.call_args
        assert kwargs["api_key"] == "sk-test123"

    @patch("pm_benchmark.providers.litellm.completion")
    def test_passes_timeout_and_seed(self, mock_completion):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "ok"
        mock_completion.return_value = mock_response

        cfg = LLMConfig(model="test-model")
        result = query_model(cfg, "analyze", "system", timeout=12.5, seed=7)
        assert result == "ok"
        _, kwargs = mock_completion.call_args
        assert kwargs["timeout"] == 12.5
        assert kwargs["seed"] == 7

    @patch("pm_benchmark.providers.litellm.completion")
    def test_passes_api_base(self, mock_completion):
        """LLMConfig.api_base is forwarded to litellm (GitHub Models et al.)."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "ok"
        mock_completion.return_value = mock_response

        cfg = LLMConfig(
            model="openai/gpt-4.1",
            api_base="https://models.github.ai/inference",
        )
        result = query_model(cfg, "analyze", "system")
        assert result == "ok"
        _, kwargs = mock_completion.call_args
        assert kwargs["api_base"] == "https://models.github.ai/inference"

    @patch("pm_benchmark.providers.litellm.completion")
    def test_passes_num_retries(self, mock_completion):
        """LLMConfig.num_retries is forwarded to litellm (backoff on 429s)."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "ok"
        mock_completion.return_value = mock_response

        cfg = LLMConfig(model="openai/gpt-4.1", num_retries=3)
        result = query_model(cfg, "analyze", "system")
        assert result == "ok"
        _, kwargs = mock_completion.call_args
        assert kwargs["num_retries"] == 3

    @patch("pm_benchmark.providers.litellm.completion")
    def test_num_retries_omitted_by_default(self, mock_completion):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "ok"
        mock_completion.return_value = mock_response

        result = query_model(LLMConfig(model="openai/gpt-4.1"), "p", "s")
        assert result == "ok"
        _, kwargs = mock_completion.call_args
        assert "num_retries" not in kwargs

    def test_missing_api_key_env(self, monkeypatch):
        monkeypatch.delenv("MISSING_KEY", raising=False)
        cfg = LLMConfig(model="test-model", api_key_env="MISSING_KEY")
        with pytest.raises(LLMError, match="MISSING_KEY not set"):
            query_model(cfg, "analyze", "system")

    @patch("pm_benchmark.providers.litellm.completion")
    def test_llm_error(self, mock_completion):
        mock_completion.side_effect = RuntimeError("API down")
        cfg = LLMConfig(model="test-model")
        with pytest.raises(LLMError, match="LLM call failed"):
            query_model(cfg, "analyze", "system")

    @patch("pm_benchmark.providers.litellm.completion")
    def test_empty_response(self, mock_completion):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None
        mock_completion.return_value = mock_response

        cfg = LLMConfig(model="test-model")
        result = query_model(cfg, "analyze", "system")
        assert result == ""


class TestJevRouting:
    """query_model routes Jev models to the System One provider."""

    @staticmethod
    def _answers(probability=0.6, action="buy_yes", confidence="high", size="medium"):
        return {
            "probability": {"type": "noul", "noul": probability},
            "action": {"type": "choice", "choice": action},
            "confidence": {"type": "choice", "choice": confidence},
            "size": {"type": "choice", "choice": size},
        }

    @patch("pm_benchmark.providers.query_jev")
    def test_routes_and_maps(self, mock_jev):
        mock_jev.return_value = self._answers()
        cfg = LLMConfig(model="opencode/jev-1.13-free")
        raw = query_model(cfg, "analyze this market", "system")
        d = parse_decision(raw)
        assert d.probability == 0.6
        assert d.action == "buy_yes"
        assert d.confidence == "high"
        assert d.amount_usd == 250.0
        assert d.reasoning == "Jev opencode/jev-1.13-free"
        assert mock_jev.call_args.args[0] == "opencode/jev-1.13-free"

    @patch("pm_benchmark.providers.query_jev")
    def test_passes_timeout(self, mock_jev):
        mock_jev.return_value = self._answers()
        cfg = LLMConfig(model="jev-1.13")
        query_model(cfg, "p", "s", timeout=7.5)
        assert mock_jev.call_args.kwargs["timeout"] == 7.5

    @patch("pm_benchmark.providers.query_jev")
    def test_small_and_large_sizes(self, mock_jev):
        cfg = LLMConfig(model="jev-1.13")
        mock_jev.return_value = self._answers(size="small")
        assert parse_decision(query_model(cfg, "p", "s")).amount_usd == 125.0
        mock_jev.return_value = self._answers(size="large")
        assert parse_decision(query_model(cfg, "p", "s")).amount_usd == 500.0

    @patch("pm_benchmark.providers.query_jev")
    def test_none_size_downgrades_to_skip(self, mock_jev):
        mock_jev.return_value = self._answers(action="buy_yes", size="none")
        cfg = LLMConfig(model="jev-1.13-free")
        d = parse_decision(query_model(cfg, "p", "s"))
        assert d.action == "skip"
        assert d.amount_usd == 0.0

    @patch("pm_benchmark.providers.query_jev")
    def test_unknown_size_downgrades_to_skip(self, mock_jev):
        mock_jev.return_value = self._answers(action="buy_no", size="weird")
        cfg = LLMConfig(model="jev-1.13")
        d = parse_decision(query_model(cfg, "p", "s"))
        assert d.action == "skip"
        assert d.amount_usd == 0.0

    @patch("pm_benchmark.providers.query_jev")
    def test_jev_error_wrapped(self, mock_jev):
        mock_jev.side_effect = JevError("boom")
        cfg = LLMConfig(model="jev-1.13-free")
        with pytest.raises(LLMError, match="Jev call failed"):
            query_model(cfg, "p", "s")
