"""Tests for pm_benchmark.agent_bridge."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pm_benchmark.agent_bridge import (
    AgentBridgeError,
    _auto_name,
    create_agent,
)


class TestAutoName:
    def test_basic(self):
        name = _auto_name("claude-opus-4")
        assert name.startswith("bench-claude-opus-4-")
        # Timestamp part should be numeric
        ts_part = name.split("-")[-1]
        assert ts_part.isdigit()

    def test_slashes_replaced(self):
        name = _auto_name("openai/gpt-4o")
        assert "/" not in name
        assert "openai-gpt-4o" in name

    def test_colons_replaced(self):
        name = _auto_name("local:llama3")
        assert ":" not in name

    def test_long_model_truncated(self):
        name = _auto_name("a" * 50)
        # "bench-" + 30 chars max + "-" + timestamp
        model_part = name[len("bench-"):name.rfind("-")]
        assert len(model_part) <= 30


class TestCreateAgent:
    def test_sdk_not_installed(self):
        with patch.dict("sys.modules", {"pm_leaderboard_client": None}):
            with pytest.raises(AgentBridgeError, match="not installed"):
                create_agent("http://localhost:8000", "test-model")

    def test_new_agent(self):
        mock_agent = MagicMock()
        mock_module = MagicMock()
        mock_module.Agent.return_value = mock_agent

        with patch.dict("sys.modules", {"pm_leaderboard_client": mock_module}):
            agent = create_agent("http://localhost:8000", "claude-opus-4")

        assert agent is mock_agent
        mock_module.Agent.assert_called_once()
        call_args = mock_module.Agent.call_args
        assert call_args[0][0] == "http://localhost:8000"
        assert call_args[1]["model"] == "claude-opus-4"
        assert call_args[1]["name"].startswith("bench-claude-opus-4-")

    def test_custom_agent_name(self):
        mock_agent = MagicMock()
        mock_module = MagicMock()
        mock_module.Agent.return_value = mock_agent

        with patch.dict("sys.modules", {"pm_leaderboard_client": mock_module}):
            agent = create_agent(
                "http://localhost:8000", "test",
                agent_name="my-custom-bot",
            )

        call_args = mock_module.Agent.call_args
        assert call_args[1]["name"] == "my-custom-bot"

    def test_reconnect_with_api_key(self):
        mock_agent = MagicMock()
        mock_module = MagicMock()
        mock_module.Agent.return_value = mock_agent

        with patch.dict("sys.modules", {"pm_leaderboard_client": mock_module}):
            agent = create_agent(
                "http://localhost:8000", "test",
                api_key="lb_sk_existing",
            )

        call_args = mock_module.Agent.call_args
        assert call_args[1]["api_key"] == "lb_sk_existing"
        # Should NOT have name/model when reconnecting
        assert "name" not in call_args[1]

    def test_connection_error(self):
        mock_module = MagicMock()
        mock_module.Agent.side_effect = ConnectionError("refused")

        with patch.dict("sys.modules", {"pm_leaderboard_client": mock_module}):
            with pytest.raises(AgentBridgeError, match="Failed to create agent"):
                create_agent("http://localhost:8000", "test")

    def test_custom_account_name(self):
        mock_agent = MagicMock()
        mock_module = MagicMock()
        mock_module.Agent.return_value = mock_agent

        with patch.dict("sys.modules", {"pm_leaderboard_client": mock_module}):
            create_agent(
                "http://localhost:8000", "test",
                account_name="benchmark-eval",
            )

        call_args = mock_module.Agent.call_args
        assert call_args[1]["account_name"] == "benchmark-eval"
