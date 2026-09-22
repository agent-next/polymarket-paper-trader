"""Tests for pm_benchmark.cli."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from pm_benchmark.cli import main
from pm_benchmark.runner import EvalRun, MarketResult
from pm_benchmark.scoring import BenchmarkScores


def test_main_module_import():
    """Ensure __main__.py is importable."""
    import pm_benchmark.__main__  # noqa: F401


@pytest.fixture
def cli_runner():
    return CliRunner()


class TestListSets:
    def test_lists_bundled_sets(self, cli_runner):
        result = cli_runner.invoke(main, ["list-sets"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        names = [s["name"] for s in data["data"]]
        assert "mini" in names
        assert "mixed" in names

    def test_includes_market_count(self, cli_runner):
        result = cli_runner.invoke(main, ["list-sets"])
        data = json.loads(result.output)
        mini = next(s for s in data["data"] if s["name"] == "mini")
        assert mini["markets"] == 3

    def test_handles_broken_yaml(self, cli_runner, monkeypatch):
        """list-sets gracefully handles corrupt YAML."""
        def _broken_load(name: str):
            raise RuntimeError("corrupt yaml")

        monkeypatch.setattr("pm_benchmark.cli.load_market_set", _broken_load)
        result = cli_runner.invoke(main, ["list-sets"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        for s in data["data"]:
            assert s["description"] == "?"


class TestRunCmd:
    def test_run_success(self, cli_runner, tmp_path, monkeypatch):
        eval_run = EvalRun(
            model="test-model",
            market_set="mini",
            budget=10000.0,
            timestamp="2025-01-01T00:00:00Z",
        )
        eval_run.market_results = [
            MarketResult(slug="m1", question="Q?", model_probability=0.6, action="buy_yes", amount_usd=100),
        ]

        mock_instance = MagicMock()
        mock_instance.run.return_value = eval_run
        mock_cls = MagicMock(return_value=mock_instance)
        monkeypatch.setattr("pm_benchmark.cli.Runner", mock_cls)

        out_dir = tmp_path / "out"
        result = cli_runner.invoke(main, [
            "run", "--model", "test-model", "--market-set", "mini",
            "--output-dir", str(out_dir),
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert "composite_score" in data["data"]
        assert "alpha_score" in data["data"]

    def test_run_verbose_llm_only(self, cli_runner, tmp_path, monkeypatch):
        eval_run = EvalRun(model="m", market_set="mini", budget=10000.0)
        eval_run.market_results = [
            MarketResult(slug="m1", question="Q?", model_probability=0.5),
        ]
        mock_instance = MagicMock()
        mock_instance.run.return_value = eval_run
        mock_cls = MagicMock(return_value=mock_instance)
        monkeypatch.setattr("pm_benchmark.cli.Runner", mock_cls)

        result = cli_runner.invoke(main, [
            "run", "--model", "m", "--output-dir", str(tmp_path / "out"), "--verbose",
        ])
        assert result.exit_code == 0
        assert "Model: m" in result.output
        assert "LLM-only" in result.output

    def test_run_with_leaderboard(self, cli_runner, tmp_path, monkeypatch):
        """With --leaderboard-url, agent is created and passed to Runner."""
        eval_run = EvalRun(model="m", market_set="mini", budget=10000.0)
        eval_run.market_results = [
            MarketResult(slug="m1", question="Q?", model_probability=0.6),
        ]
        mock_runner_instance = MagicMock()
        mock_runner_instance.run.return_value = eval_run
        mock_runner_cls = MagicMock(return_value=mock_runner_instance)
        monkeypatch.setattr("pm_benchmark.cli.Runner", mock_runner_cls)

        mock_agent = MagicMock()
        mock_agent.api_key = "lb_sk_test"
        mock_create = MagicMock(return_value=mock_agent)
        monkeypatch.setattr(
            "pm_benchmark.agent_bridge.create_agent",
            mock_create,
        )

        result = cli_runner.invoke(main, [
            "run", "--model", "m",
            "--leaderboard-url", "http://lb:8000",
            "--output-dir", str(tmp_path / "out"),
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["agent_api_key"] == "lb_sk_test"
        # Verify agent was passed to Runner
        runner_call = mock_runner_cls.call_args
        assert runner_call[1]["agent"] is mock_agent
        assert mock_create.call_args[1]["account_name"] == "default"

    def test_run_with_api_key_reconnect(self, cli_runner, tmp_path, monkeypatch):
        """With --api-key, reconnects to existing agent."""
        eval_run = EvalRun(model="m", market_set="mini", budget=10000.0)
        eval_run.market_results = [
            MarketResult(slug="m1", question="Q?", model_probability=0.5),
        ]
        mock_runner_instance = MagicMock()
        mock_runner_instance.run.return_value = eval_run
        monkeypatch.setattr("pm_benchmark.cli.Runner", MagicMock(return_value=mock_runner_instance))

        mock_agent = MagicMock()
        mock_agent.api_key = "lb_sk_existing"
        mock_create = MagicMock(return_value=mock_agent)
        monkeypatch.setattr("pm_benchmark.agent_bridge.create_agent", mock_create)

        result = cli_runner.invoke(main, [
            "run", "--model", "m",
            "--leaderboard-url", "http://lb:8000",
            "--api-key", "lb_sk_existing",
            "--output-dir", str(tmp_path / "out"),
        ])
        assert result.exit_code == 0
        # Verify api_key was passed to create_agent
        mock_create.assert_called_once()
        assert mock_create.call_args[1]["api_key"] == "lb_sk_existing"

    def test_run_with_custom_account_name(self, cli_runner, tmp_path, monkeypatch):
        eval_run = EvalRun(model="m", market_set="mini", budget=10000.0)
        eval_run.market_results = [
            MarketResult(slug="m1", question="Q?", model_probability=0.5),
        ]
        mock_runner_instance = MagicMock()
        mock_runner_instance.run.return_value = eval_run
        mock_runner_cls = MagicMock(return_value=mock_runner_instance)
        monkeypatch.setattr("pm_benchmark.cli.Runner", mock_runner_cls)

        mock_agent = MagicMock()
        mock_agent.api_key = "lb_sk_custom"
        mock_create = MagicMock(return_value=mock_agent)
        monkeypatch.setattr("pm_benchmark.agent_bridge.create_agent", mock_create)

        result = cli_runner.invoke(main, [
            "run", "--model", "m",
            "--leaderboard-url", "http://lb:8000",
            "--account-name", "benchmark-eval",
            "--output-dir", str(tmp_path / "out"),
        ])
        assert result.exit_code == 0
        assert mock_create.call_args[1]["account_name"] == "benchmark-eval"

        cfg = mock_runner_cls.call_args[0][0]
        assert cfg.leaderboard.account_name == "benchmark-eval"

    def test_run_agent_error(self, cli_runner, monkeypatch):
        """Agent creation failure exits with error."""
        from pm_benchmark.agent_bridge import AgentBridgeError
        monkeypatch.setattr(
            "pm_benchmark.agent_bridge.create_agent",
            MagicMock(side_effect=AgentBridgeError("SDK not installed")),
        )

        result = cli_runner.invoke(main, [
            "run", "--model", "m", "--leaderboard-url", "http://lb:8000",
        ])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["ok"] is False
        assert data["code"] == "AGENT_ERROR"

    def test_run_with_blind_and_rounds(self, cli_runner, tmp_path, monkeypatch):
        """--blind and --n-rounds flags are passed to config."""
        eval_run = EvalRun(model="m", market_set="mini", budget=10000.0)
        eval_run.market_results = [
            MarketResult(slug="m1", question="Q?", model_probability=0.5),
        ]
        mock_instance = MagicMock()
        mock_instance.run.return_value = eval_run
        mock_cls = MagicMock(return_value=mock_instance)
        monkeypatch.setattr("pm_benchmark.cli.Runner", mock_cls)

        result = cli_runner.invoke(main, [
            "run", "--model", "m", "--output-dir", str(tmp_path / "out"),
            "--blind", "--n-rounds", "2",
        ])
        assert result.exit_code == 0
        # Verify config was passed correctly
        config = mock_cls.call_args[0][0]
        assert config.blind_mode is True
        assert config.n_rounds == 2

    def test_run_with_timeout_seed_and_trade_limit(self, cli_runner, tmp_path, monkeypatch):
        eval_run = EvalRun(model="m", market_set="mini", budget=10000.0)
        eval_run.market_results = [
            MarketResult(slug="m1", question="Q?", model_probability=0.5),
        ]
        mock_instance = MagicMock()
        mock_instance.run.return_value = eval_run
        mock_cls = MagicMock(return_value=mock_instance)
        monkeypatch.setattr("pm_benchmark.cli.Runner", mock_cls)

        result = cli_runner.invoke(main, [
            "run", "--model", "m", "--output-dir", str(tmp_path / "out"),
            "--timeout", "12.5", "--seed", "123", "--max-trades-per-market", "2",
        ])
        assert result.exit_code == 0
        config = mock_cls.call_args[0][0]
        assert config.timeout == 12.5
        assert config.seed == 123
        assert config.max_trades_per_market == 2

    def test_run_error(self, cli_runner, monkeypatch):
        monkeypatch.setattr("pm_benchmark.cli.Runner", MagicMock(side_effect=RuntimeError("Setup failed")))
        result = cli_runner.invoke(main, ["run", "--model", "m"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["ok"] is False


class TestScoreCmd:
    def _write_results(self, path: Path) -> None:
        data = {
            "model": "test",
            "market_set": "mini",
            "budget": 10000,
            "timestamp": "2025-01-01",
            "scores": {
                "composite_score": 50.0,
                "brier_score": 0.25,
                "calibration_error": 0.1,
                "roi_pct": 5.0,
                "sharpe_ratio": 0.5,
                "win_rate": 0.5,
                "max_drawdown": 0.05,
                "skip_rate": 0.1,
                "alpha_score": 0.0,
                "details": {},
            },
            "market_results": [
                {
                    "slug": "m1", "question": "Q?",
                    "model_probability": 0.6, "action": "buy_yes",
                    "confidence": "high", "amount_usd": 100,
                    "reasoning": "good", "trade_result": None,
                    "latency_seconds": 1.0, "error": None,
                    "skipped_reason": None,
                    "market_price_yes": 0.65,
                },
            ],
        }
        path.write_text(json.dumps(data))

    def test_score(self, cli_runner, tmp_path):
        results_path = tmp_path / "run.json"
        self._write_results(results_path)
        result = cli_runner.invoke(main, ["score", "--results", str(results_path)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert "composite_score" in data["data"]

    def test_score_with_resolved(self, cli_runner, tmp_path):
        results_path = tmp_path / "run.json"
        self._write_results(results_path)
        resolved_path = tmp_path / "resolved.json"
        resolved_path.write_text(json.dumps({"m1": 1.0}))
        result = cli_runner.invoke(main, [
            "score", "--results", str(results_path),
            "--resolved", str(resolved_path),
        ])
        assert result.exit_code == 0

    def test_score_invalid_json(self, cli_runner, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json")
        result = cli_runner.invoke(main, ["score", "--results", str(bad_file)])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["ok"] is False


class TestCompareCmd:
    def _write_run(self, path: Path, model: str, score: float) -> None:
        data = {
            "model": model,
            "market_set": "mini",
            "scores": {
                "composite_score": score,
                "brier_score": 0.2,
                "roi_pct": 5.0,
            },
        }
        path.write_text(json.dumps(data))

    def test_compare(self, cli_runner, tmp_path):
        p1 = tmp_path / "run1.json"
        p2 = tmp_path / "run2.json"
        self._write_run(p1, "model-a", 70.0)
        self._write_run(p2, "model-b", 80.0)

        result = cli_runner.invoke(main, [
            "compare", "--results", str(p1), "--results", str(p2),
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        # Should be sorted by score descending
        assert data["data"][0]["model"] == "model-b"

    def test_compare_with_output(self, cli_runner, tmp_path):
        p1 = tmp_path / "run1.json"
        self._write_run(p1, "model-a", 70.0)
        out = tmp_path / "comparison.md"

        result = cli_runner.invoke(main, [
            "compare", "--results", str(p1), "--output", str(out),
        ])
        assert result.exit_code == 0
        assert out.exists()
        assert "# Benchmark Comparison" in out.read_text()

    def test_compare_invalid_json(self, cli_runner, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json")
        result = cli_runner.invoke(main, ["compare", "--results", str(bad)])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["ok"] is False


class TestExportCmd:
    def _write_results(self, path: Path) -> None:
        data = {
            "model": "test",
            "market_set": "mini",
            "budget": 10000,
            "timestamp": "2025-01-01",
            "scores": {
                "composite_score": 50.0,
                "brier_score": 0.25,
                "calibration_error": 0.1,
                "roi_pct": 5.0,
                "sharpe_ratio": 0.5,
                "win_rate": 0.5,
                "max_drawdown": 0.05,
                "skip_rate": 0.1,
                "alpha_score": 0.0,
                "details": {},
            },
            "market_results": [
                {
                    "slug": "m1", "question": "Q?",
                    "model_probability": 0.6, "action": "buy_yes",
                    "confidence": "high", "amount_usd": 100,
                    "reasoning": "good", "trade_result": None,
                    "latency_seconds": 1.0, "error": None,
                    "skipped_reason": None,
                },
            ],
        }
        path.write_text(json.dumps(data))

    def test_export_huggingface(self, cli_runner, tmp_path):
        results_path = tmp_path / "run.json"
        self._write_results(results_path)
        out_path = tmp_path / "export.jsonl"
        result = cli_runner.invoke(main, [
            "export", "--results", str(results_path),
            "--output", str(out_path),
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert out_path.exists()

    def test_run_creates_history(self, cli_runner, tmp_path, monkeypatch):
        """Run command auto-appends to history.jsonl."""
        eval_run = EvalRun(model="m", market_set="mini", budget=10000.0)
        eval_run.market_results = [
            MarketResult(slug="m1", question="Q?", model_probability=0.5),
        ]
        mock_instance = MagicMock()
        mock_instance.run.return_value = eval_run
        monkeypatch.setattr("pm_benchmark.cli.Runner", MagicMock(return_value=mock_instance))

        out_dir = tmp_path / "out"
        result = cli_runner.invoke(main, [
            "run", "--model", "m", "--output-dir", str(out_dir),
        ])
        assert result.exit_code == 0
        history_path = out_dir / "history.jsonl"
        assert history_path.exists()

    def test_export_invalid_json(self, cli_runner, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json")
        result = cli_runner.invoke(main, [
            "export", "--results", str(bad), "--output", str(tmp_path / "x.jsonl"),
        ])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["ok"] is False


class TestHistoryCmd:
    def test_history(self, cli_runner, tmp_path):
        path = tmp_path / "history.jsonl"
        record = {
            "model": "test-model",
            "market_set": "mini",
            "timestamp": "2025-01-01",
            "scores": {"composite_score": 65.0, "brier_score": 0.2, "alpha_score": -0.01},
            "completed": 3,
        }
        path.write_text(json.dumps(record) + "\n")

        result = cli_runner.invoke(main, ["history", "--path", str(path)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert len(data["data"]) == 1
        assert data["data"][0]["model"] == "test-model"
        assert data["data"][0]["composite_score"] == 65.0
        assert data["data"][0]["alpha_score"] == -0.01

    def test_history_empty(self, cli_runner, tmp_path):
        path = tmp_path / "history.jsonl"
        path.write_text("")
        result = cli_runner.invoke(main, ["history", "--path", str(path)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"] == []

    def test_history_invalid(self, cli_runner, tmp_path):
        path = tmp_path / "bad.jsonl"
        path.write_text("not json\n")
        result = cli_runner.invoke(main, ["history", "--path", str(path)])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["ok"] is False


class TestBackfillCmd:
    def _write_results(self, path: Path) -> None:
        data = {
            "model": "test",
            "market_set": "mini",
            "budget": 10000,
            "timestamp": "2025-01-01",
            "scores": {
                "composite_score": 50.0,
                "brier_score": 0.25,
            },
            "market_results": [
                {
                    "slug": "m1", "question": "Q1?",
                    "model_probability": 0.7, "action": "buy_yes",
                    "confidence": "high", "amount_usd": 100,
                    "reasoning": "good", "trade_result": None,
                    "latency_seconds": 1.0, "error": None,
                    "skipped_reason": None,
                    "market_price_yes": 0.65,
                },
                {
                    "slug": "m2", "question": "Q2?",
                    "model_probability": 0.3, "action": "buy_no",
                    "confidence": "medium", "amount_usd": 50,
                    "reasoning": "weak", "trade_result": None,
                    "latency_seconds": 0.5, "error": None,
                    "skipped_reason": None,
                    "market_price_yes": 0.40,
                },
            ],
        }
        path.write_text(json.dumps(data))

    def test_backfill_with_resolved(self, cli_runner, tmp_path, monkeypatch):
        results_path = tmp_path / "run.json"
        self._write_results(results_path)

        # m1 resolved Yes, m2 not resolved
        def mock_resolution(slug, **kwargs):
            if slug == "m1":
                return 1.0
            return None

        monkeypatch.setattr("pm_benchmark.cli.fetch_resolution", mock_resolution)

        out_path = tmp_path / "backfilled.json"
        result = cli_runner.invoke(main, [
            "backfill", "--results", str(results_path),
            "--output", str(out_path),
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["resolved"] == 1
        assert "composite_score" in data["data"]

        backfilled = json.loads(out_path.read_text())
        assert backfilled["resolved_outcomes"] == {"m1": 1.0}
        assert "brier_score" in backfilled["scores"]

    def test_backfill_no_resolved(self, cli_runner, tmp_path, monkeypatch):
        results_path = tmp_path / "run.json"
        self._write_results(results_path)

        monkeypatch.setattr("pm_benchmark.cli.fetch_resolution", lambda slug, **kw: None)

        result = cli_runner.invoke(main, [
            "backfill", "--results", str(results_path),
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["resolved"] == 0

    def test_backfill_overwrites_input(self, cli_runner, tmp_path, monkeypatch):
        results_path = tmp_path / "run.json"
        self._write_results(results_path)

        monkeypatch.setattr("pm_benchmark.cli.fetch_resolution", lambda slug, **kw: 1.0)

        result = cli_runner.invoke(main, [
            "backfill", "--results", str(results_path),
        ])
        assert result.exit_code == 0
        # Should overwrite the input file
        backfilled = json.loads(results_path.read_text())
        assert "resolved_outcomes" in backfilled

    def test_backfill_api_error_skips(self, cli_runner, tmp_path, monkeypatch):
        """Markets that fail to fetch are silently skipped."""
        results_path = tmp_path / "run.json"
        self._write_results(results_path)

        from pm_benchmark.market_info import MarketInfoError

        call_count = 0

        def mock_resolution(slug, **kwargs):
            nonlocal call_count
            call_count += 1
            if slug == "m1":
                raise MarketInfoError("API down")
            return 1.0  # m2 resolves

        monkeypatch.setattr("pm_benchmark.cli.fetch_resolution", mock_resolution)

        result = cli_runner.invoke(main, [
            "backfill", "--results", str(results_path),
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["resolved"] == 1  # only m2

    def test_backfill_invalid_json(self, cli_runner, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json")
        result = cli_runner.invoke(main, ["backfill", "--results", str(bad)])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["ok"] is False
