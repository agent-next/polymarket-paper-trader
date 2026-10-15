"""Abuse limits of the authenticated remote MCP mode (rate limit, account cap, prune)."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from mcp.server.auth.provider import AccessToken

from pm_trader import mcp_server
from pm_trader.remote_auth import (
    AccountLimitError,
    RateLimiter,
    RemoteAuthConfigError,
    acquire_server_lock,
    check_account_cap,
    prune_remote,
    remote_root,
    user_root,
)
from tests.test_remote_auth import (
    AUDIENCE,
    ISSUER,
    RESOURCE_URL,
    _as_user,
    _config,
    _jwk,
    _McpClient,
    _remote_server,
    _serve_issuer,
    _token,
    key,  # noqa: F401  (module-scoped issuer key fixture)
)
from pm_trader.remote_auth import RemoteAuthConfig

DAY = 86400.0


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _user(sub: str) -> AccessToken:
    return AccessToken(
        token="t", client_id="c", scopes=[], subject=sub, claims={"iss": ISSUER}
    )


def _remote(tmp_path: Path, **limits: int):
    """Patch in remote mode over a tmp home, with a fresh engine slot."""
    config = RemoteAuthConfig(
        issuer=ISSUER, audience=AUDIENCE, resource_url=RESOURCE_URL, **limits
    )
    return config, (
        patch.object(Path, "home", return_value=tmp_path),
        patch.object(mcp_server, "_remote", config),
        patch.object(mcp_server, "_engine", None),
    )


def _code(envelope: str) -> str | None:
    return json.loads(envelope).get("code")


class TestRateLimiter:
    def test_sliding_window_with_fake_clock(self):
        clock = FakeClock()
        limiter = RateLimiter(3, clock=clock)
        assert [limiter.allow("a") for _ in range(4)] == [True, True, True, False]
        clock.now += 59.0
        assert limiter.allow("a") is False  # still inside the 60 s window
        clock.now += 1.0  # the three t=1000 calls are now exactly 60 s old
        assert [limiter.allow("a") for _ in range(4)] == [True, True, True, False]

    def test_window_slides_call_by_call(self):
        clock = FakeClock()
        limiter = RateLimiter(2, clock=clock)
        assert limiter.allow("a")
        clock.now += 30.0
        assert limiter.allow("a")
        assert not limiter.allow("a")
        clock.now += 30.0  # first call expires, second still counts
        assert limiter.allow("a")
        assert not limiter.allow("a")

    def test_keys_are_independent(self):
        limiter = RateLimiter(1, clock=FakeClock())
        assert limiter.allow("a")
        assert not limiter.allow("a")
        assert limiter.allow("b")

    def test_rejected_calls_are_not_recorded(self):
        clock = FakeClock()
        limiter = RateLimiter(1, clock=clock)
        assert limiter.allow("a")
        for _ in range(10):
            assert not limiter.allow("a")
        clock.now += 60.0
        assert limiter.allow("a")


class TestConfigValidation:
    def test_defaults(self):
        config = _config()
        assert (config.rate_limit_per_minute, config.max_accounts) == (60, 20)
        assert config.limiter.limit == 60

    @pytest.mark.parametrize(
        "field", ["rate_limit_per_minute", "max_accounts"]
    )
    def test_limits_must_be_positive(self, field):
        with pytest.raises(RemoteAuthConfigError, match="at least 1"):
            RemoteAuthConfig(
                issuer=ISSUER, audience=AUDIENCE, resource_url=RESOURCE_URL,
                **{field: 0},
            )


class TestRateLimitGate:
    def test_over_limit_returns_rate_limited_and_never_sleeps(self, tmp_path):
        config, patches = _remote(tmp_path, rate_limit_per_minute=3)
        clock = FakeClock()
        config.limiter = RateLimiter(3, clock=clock)
        with (
            patches[0], patches[1], patches[2], _as_user(_user("alice")),
            patch.object(time, "sleep", side_effect=AssertionError("slept")),
        ):
            results = [mcp_server._rate_limit_error() for _ in range(4)]
            assert results[:3] == [None, None, None]
            assert _code(results[3]) == "RATE_LIMITED"
            assert json.loads(results[3]) == {
                "ok": False,
                "error": "Rate limit exceeded: at most 3 tool calls per minute",
                "code": "RATE_LIMITED",
            }
            clock.now += 60.0
            assert mcp_server._rate_limit_error() is None

    def test_limit_is_per_subject(self, tmp_path):
        config, patches = _remote(tmp_path, rate_limit_per_minute=1)
        config.limiter = RateLimiter(1, clock=FakeClock())
        with patches[0], patches[1], patches[2]:
            with _as_user(_user("alice")):
                assert mcp_server._rate_limit_error() is None
                assert _code(mcp_server._rate_limit_error()) == "RATE_LIMITED"
            with _as_user(_user("bob")):
                assert mcp_server._rate_limit_error() is None

    def test_keyed_by_the_user_root_digest(self, tmp_path):
        config, patches = _remote(tmp_path)
        seen: list[str] = []
        config.limiter.allow = lambda key: seen.append(key) or True  # type: ignore[method-assign]
        with patches[0], patches[1], patches[2], _as_user(_user("alice")):
            mcp_server._rate_limit_error()
            expected = user_root(ISSUER, "alice").name
        assert seen == [expected]

    def test_unverified_call_is_left_to_the_data_root_check(self, tmp_path):
        _, patches = _remote(tmp_path, rate_limit_per_minute=1)
        with patches[0], patches[1], patches[2]:
            assert mcp_server._rate_limit_error() is None
            assert mcp_server._rate_limit_error() is None

    def test_local_mode_is_never_limited(self, tmp_path):
        """Negative control: same calls, no remote mode, never RATE_LIMITED."""
        with (
            patch.object(Path, "home", return_value=tmp_path),
            patch.object(mcp_server, "_remote", None),
            patch.object(mcp_server, "_engine", None),
            _as_user(_user("alice")),
        ):
            assert [mcp_server._rate_limit_error() for _ in range(500)] == [None] * 500

    def test_over_http_the_tool_is_not_run(self, tmp_path, key, httpx_mock):  # noqa: F811
        _serve_issuer(httpx_mock, key)
        with _remote_server(tmp_path) as client:
            mcp_server._remote = RemoteAuthConfig(
                issuer=ISSUER, audience=AUDIENCE, resource_url=RESOURCE_URL,
                rate_limit_per_minute=1,
            )
            mcp = _McpClient(client)
            token = _token(key, sub="alice")
            first = mcp.call("get_balance", {"account": "one"}, token)
            second = mcp.call("get_balance", {"account": "two"}, token)
            other = mcp.call("get_balance", {"account": "one"}, _token(key, sub="bob"))

        assert first.get("code") != "RATE_LIMITED"
        assert second["code"] == "RATE_LIMITED"
        assert other.get("code") != "RATE_LIMITED"
        # The limited call never reached the engine: no "two" account directory.
        users = tmp_path / ".pm-trader" / "remote"
        assert not (users / user_root(ISSUER, "alice").name / "two").exists()


class TestAccountCap:
    def test_account_21_is_refused_and_existing_keep_working(self, tmp_path):
        _, patches = _remote(tmp_path)
        with patches[0], patches[1], patches[2], _as_user(_user("alice")):
            for n in range(20):
                assert json.loads(mcp_server.init_account(account=f"a{n}"))["ok"]
            refused = json.loads(mcp_server.init_account(account="a20"))
            assert refused["ok"] is False
            assert refused["code"] == "ACCOUNT_LIMIT"
            assert "20" in refused["error"]
            assert not (user_root(ISSUER, "alice") / "a20").exists()
            # Existing accounts keep working, including re-init.
            assert json.loads(mcp_server.get_balance(account="a0"))["ok"]
            assert json.loads(mcp_server.init_account(account="a19"))["ok"]
            # Another subject has its own budget.
        with patches[0], patches[1], patches[2], _as_user(_user("bob")):
            assert json.loads(mcp_server.init_account(account="a20"))["ok"]

    def test_cap_is_configurable(self, tmp_path):
        _, patches = _remote(tmp_path, max_accounts=1)
        with patches[0], patches[1], patches[2], _as_user(_user("alice")):
            assert json.loads(mcp_server.init_account(account="one"))["ok"]
            assert _code(mcp_server.init_account(account="two")) == "ACCOUNT_LIMIT"

    def test_check_account_cap_direct(self, tmp_path):
        root = tmp_path / "missing"
        check_account_cap(root, "x", 1)  # no root yet: first account fine
        (tmp_path / "a").mkdir()
        (tmp_path / "stray.txt").write_text("not an account")
        check_account_cap(tmp_path, "a", 1)  # existing passes
        with pytest.raises(AccountLimitError) as exc:
            check_account_cap(tmp_path, "b", 1)
        assert exc.value.code == "ACCOUNT_LIMIT"

    def test_default_is_outside_the_cap(self, tmp_path):
        # A cache-only default dir does not use up a named slot ...
        (tmp_path / "default").mkdir()
        check_account_cap(tmp_path, "a", 1)
        (tmp_path / "a").mkdir()
        with pytest.raises(AccountLimitError):
            check_account_cap(tmp_path, "b", 1)
        # ... and default itself is always allowed, even at the cap.
        (tmp_path / "default").rmdir()
        check_account_cap(tmp_path, "default", 1)

    def test_local_mode_has_no_account_cap(self, tmp_path):
        """Negative control: 25 accounts in local mode, all fine."""
        with (
            patch.object(Path, "home", return_value=tmp_path),
            patch.object(mcp_server, "_remote", None),
            patch.object(mcp_server, "_engine", None),
        ):
            codes = [
                json.loads(mcp_server.init_account(account=f"a{n}")).get("code")
                for n in range(25)
            ]
            mcp_server._engine.close()
        assert codes == [None] * 25
        assert len(list((tmp_path / ".pm-trader").iterdir())) == 25


def _make_user(root: Path, name: str, age_days: float, now: float) -> Path:
    d = root / name
    (d / "acct").mkdir(parents=True)
    f = d / "acct" / "paper.db"
    f.write_text("x")
    stamp = now - age_days * DAY
    os.utime(f, (stamp, stamp))
    os.utime(d / "acct", (stamp, stamp))
    os.utime(d, (stamp, stamp))
    return d


HASH_OLD = "a" * 32
HASH_NEW = "b" * 32
HASH_EMPTY = "c" * 32


class TestPrune:
    def _setup(self, tmp_path: Path):
        now = time.time()
        root = tmp_path / ".pm-trader" / "remote"
        old = _make_user(root, HASH_OLD, 100, now)
        new = _make_user(root, HASH_NEW, 10, now)
        return now, root, old, new

    def test_deletes_only_idle_users(self, tmp_path):
        now, root, old, new = self._setup(tmp_path)
        with patch.object(Path, "home", return_value=tmp_path):
            removed = prune_remote(90, now=now)
        assert removed == [old]
        assert not old.exists()
        assert new.exists()

    def test_newest_file_decides(self, tmp_path):
        now, root, old, _ = self._setup(tmp_path)
        fresh = old / "acct" / "extra.json"
        fresh.write_text("y")  # mtime = now, so the user is active
        with patch.object(Path, "home", return_value=tmp_path):
            assert prune_remote(90, now=now) == []
        assert old.exists()

    def test_dry_run_deletes_nothing(self, tmp_path):
        now, root, old, _ = self._setup(tmp_path)
        with patch.object(Path, "home", return_value=tmp_path):
            assert prune_remote(90, dry_run=True, now=now) == [old]
        assert (old / "acct" / "paper.db").exists()

    def test_empty_dir_uses_its_own_mtime(self, tmp_path):
        now = time.time()
        root = tmp_path / ".pm-trader" / "remote"
        empty = root / HASH_EMPTY
        empty.mkdir(parents=True)
        os.utime(empty, (now - 200 * DAY, now - 200 * DAY))
        with patch.object(Path, "home", return_value=tmp_path):
            assert prune_remote(90, now=now) == [empty]

    def test_missing_root_is_a_noop(self, tmp_path):
        with patch.object(Path, "home", return_value=tmp_path):
            assert prune_remote(90) == []

    def test_default_now_is_wall_clock(self, tmp_path):
        _, root, old, new = self._setup(tmp_path)
        with patch.object(Path, "home", return_value=tmp_path):
            assert prune_remote(90) == [old]

    def test_skips_foreign_entries_and_symlinks(self, tmp_path):
        now, root, old, _ = self._setup(tmp_path)
        outside = tmp_path / "outside"
        _make_user(tmp_path, "outside", 500, now)
        (root / ("d" * 32)).symlink_to(outside, target_is_directory=True)
        stray = _make_user(root, "not-a-hash", 500, now)
        (root / ("e" * 32)).write_text("file, not dir")
        with patch.object(Path, "home", return_value=tmp_path):
            removed = prune_remote(90, now=now)
        assert removed == [old]
        assert (outside / "acct" / "paper.db").exists()
        assert stray.exists()
        assert (root / ("e" * 32)).is_file()

    @pytest.mark.parametrize("target", ["other", ".pm-trader", ".pm-trader/default"])
    def test_refuses_any_other_root(self, tmp_path, target):
        now = time.time()
        bait = _make_user(tmp_path / target, HASH_OLD, 500, now)
        with patch.object(Path, "home", return_value=tmp_path):
            with pytest.raises(RemoteAuthConfigError, match="refusing to prune"):
                prune_remote(0, root=tmp_path / target, now=now)
        assert (bait / "acct" / "paper.db").exists()

    def test_accepts_the_real_root_explicitly(self, tmp_path):
        now, root, old, _ = self._setup(tmp_path)
        with patch.object(Path, "home", return_value=tmp_path):
            assert prune_remote(90, root=remote_root(), now=now) == [old]

    def test_negative_idle_days_refused(self, tmp_path):
        with patch.object(Path, "home", return_value=tmp_path):
            with pytest.raises(RemoteAuthConfigError, match="negative"):
                prune_remote(-1)


class TestPruneCli:
    def test_dry_run_prints_and_keeps(self, tmp_path, capsys):
        now = time.time()
        root = tmp_path / ".pm-trader" / "remote"
        old = _make_user(root, HASH_OLD, 100, now)
        with patch.object(Path, "home", return_value=tmp_path):
            mcp_server.main(["prune-remote", "--idle-days", "90", "--dry-run"])
        out = capsys.readouterr().out.splitlines()
        assert out == [f"would remove {old}", "would remove 1 idle user directory"]
        assert old.exists()

    def test_real_run_removes(self, tmp_path, capsys):
        now = time.time()
        root = tmp_path / ".pm-trader" / "remote"
        old = _make_user(root, HASH_OLD, 100, now)
        _make_user(root, HASH_NEW, 100, now)
        with patch.object(Path, "home", return_value=tmp_path):
            mcp_server.main(["prune-remote"])  # default --idle-days 90
        out = capsys.readouterr().out.splitlines()
        assert out[-1] == "removed 2 idle user directories"
        assert not old.exists()

    def test_reads_sys_argv_by_default(self, tmp_path, capsys):
        with (
            patch.object(Path, "home", return_value=tmp_path),
            patch("sys.argv", ["pm-trader-mcp", "prune-remote", "--dry-run"]),
        ):
            mcp_server.main()
        assert capsys.readouterr().out == "would remove 0 idle user directories\n"

    def test_bad_days_is_a_usage_error(self, tmp_path, capsys):
        with patch.object(Path, "home", return_value=tmp_path):
            with pytest.raises(SystemExit) as exc:
                mcp_server.main(["prune-remote", "--idle-days", "-5"])
        assert exc.value.code == 2
        assert "negative" in capsys.readouterr().err


class TestFlags:
    def test_flags_reach_the_config(self):
        args = mcp_server._build_arg_parser().parse_args(
            ["--auth-issuer", ISSUER, "--auth-audience", AUDIENCE,
             "--resource-url", RESOURCE_URL,
             "--rate-limit-per-minute", "7", "--max-accounts", "3"]
        )
        config = mcp_server._auth_config(args)
        assert (config.rate_limit_per_minute, config.max_accounts) == (7, 3)

    def test_env_vars_and_defaults(self, monkeypatch):
        monkeypatch.delenv("PM_TRADER_RATE_LIMIT_PER_MINUTE", raising=False)
        monkeypatch.delenv("PM_TRADER_MAX_ACCOUNTS", raising=False)
        args = mcp_server._build_arg_parser().parse_args([])
        assert (args.rate_limit_per_minute, args.max_accounts) == (60, 20)
        monkeypatch.setenv("PM_TRADER_RATE_LIMIT_PER_MINUTE", "11")
        monkeypatch.setenv("PM_TRADER_MAX_ACCOUNTS", "4")
        args = mcp_server._build_arg_parser().parse_args([])
        assert (args.rate_limit_per_minute, args.max_accounts) == (11, 4)

    def test_zero_limit_is_a_startup_error(self, capsys):
        with pytest.raises(SystemExit):
            mcp_server.main(
                ["--transport", "streamable-http", "--auth-issuer", ISSUER,
                 "--auth-audience", AUDIENCE, "--resource-url", RESOURCE_URL,
                 "--max-accounts", "0"]
            )
        assert "--max-accounts must be at least 1" in capsys.readouterr().err


class TestLimitCodesSurvive:
    def test_account_limit_code_survives_a_tool_override(self):
        """get_balance labels errors not_initialized; the cap code must win."""
        envelope = json.loads(
            mcp_server._err_from(AccountLimitError("cap"), "not_initialized")
        )
        assert envelope["code"] == "ACCOUNT_LIMIT"

    def test_other_errors_still_take_the_override(self):
        envelope = json.loads(
            mcp_server._err_from(ValueError("x"), "not_initialized")
        )
        assert envelope["code"] == "not_initialized"


class TestServerLock:
    def test_missing_fcntl_gives_a_clear_error(self, tmp_path):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "fcntl":
                raise ImportError("no fcntl")
            return real_import(name, *args, **kwargs)

        with (
            patch.object(Path, "home", return_value=tmp_path),
            patch.object(builtins, "__import__", fake_import),
            pytest.raises(RemoteAuthConfigError, match="requires a POSIX host"),
        ):
            acquire_server_lock()

    def test_fcntl_is_not_a_module_level_import(self):
        """Importing remote_auth (hence local stdio mode) must not need fcntl."""
        import ast

        import pm_trader.remote_auth as module

        tree = ast.parse(Path(module.__file__).read_text())
        top = [
            alias.name
            for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        ] + [n.module for n in tree.body if isinstance(n, ast.ImportFrom)]
        assert "fcntl" not in top

    def test_second_holder_is_refused(self, tmp_path):
        with patch.object(Path, "home", return_value=tmp_path):
            first = acquire_server_lock()
            try:
                with pytest.raises(RemoteAuthConfigError, match="already|another"):
                    acquire_server_lock()
            finally:
                first.close()
            acquire_server_lock().close()  # released on close

    def test_run_takes_the_lock_and_fails_startup_if_held(self, tmp_path):
        config = _config()
        with (
            patch.object(Path, "home", return_value=tmp_path),
            patch.object(mcp_server, "_remote", None),
            patch.object(mcp_server, "_server_lock", None),
            patch.object(mcp_server, "_schedule_prune") as schedule,
            patch.object(mcp_server.mcp, "run") as run,
            patch.object(mcp_server.mcp, "remove_tool"),
            patch.object(mcp_server.mcp.settings, "auth", None),
            patch.object(mcp_server.mcp, "_token_verifier", None),
        ):
            mcp_server._run("streamable-http", "127.0.0.1", 8000, config)
            held = mcp_server._server_lock
            assert held is not None and not held.closed
            schedule.assert_called_once_with(90)
            with pytest.raises(RemoteAuthConfigError):
                mcp_server._run("streamable-http", "127.0.0.1", 8000, config)
            assert run.call_count == 1
            held.close()

    def test_main_reports_a_held_lock_as_usage_error(self, tmp_path, capsys):
        with patch.object(Path, "home", return_value=tmp_path):
            held = acquire_server_lock()
            try:
                with (
                    patch.object(mcp_server, "_remote", None),
                    patch.object(mcp_server.mcp, "remove_tool"),
                    patch.object(mcp_server.mcp.settings, "auth", None),
                    patch.object(mcp_server.mcp, "_token_verifier", None),
                    pytest.raises(SystemExit) as exc,
                ):
                    mcp_server.main(
                        ["--transport", "streamable-http", "--auth-issuer", ISSUER,
                         "--auth-audience", AUDIENCE, "--resource-url", RESOURCE_URL]
                    )
            finally:
                held.close()
        assert exc.value.code == 2
        assert "another remote pm-trader-mcp server" in capsys.readouterr().err

    def test_prune_cli_refuses_while_server_holds_the_lock(self, tmp_path, capsys):
        now = time.time()
        root = tmp_path / ".pm-trader" / "remote"
        old = _make_user(root, HASH_OLD, 100, now)
        with patch.object(Path, "home", return_value=tmp_path):
            held = acquire_server_lock()
            try:
                with pytest.raises(SystemExit) as exc:
                    mcp_server.main(["prune-remote", "--idle-days", "90"])
            finally:
                held.close()
        assert exc.value.code == 1
        assert "server running; use the in-server prune" in capsys.readouterr().err
        assert (old / "acct" / "paper.db").exists()

    def test_prune_cli_releases_the_lock_after_running(self, tmp_path):
        with patch.object(Path, "home", return_value=tmp_path):
            mcp_server.main(["prune-remote"])
            acquire_server_lock().close()  # would raise if the CLI kept it


class TestInServerPrune:
    def test_closes_a_cached_engine_inside_a_pruned_dir(self, tmp_path):
        now = time.time()
        root = tmp_path / ".pm-trader" / "remote"
        old = _make_user(root, HASH_OLD, 100, now)
        new = _make_user(root, HASH_NEW, 10, now)

        class Db:
            data_dir = old / "acct"

        class Eng:
            db = Db()
            closed = False

            def close(self):
                self.closed = True

        engine = Eng()
        with (
            patch.object(Path, "home", return_value=tmp_path),
            patch.object(mcp_server, "_engine", engine),
        ):
            removed = mcp_server._prune_idle_users(90)
            assert mcp_server._engine is None
        assert engine.closed
        assert removed == [old] and not old.exists() and new.exists()

    def test_keeps_an_engine_outside_pruned_dirs(self, tmp_path):
        now = time.time()
        root = tmp_path / ".pm-trader" / "remote"
        old = _make_user(root, HASH_OLD, 100, now)
        new = _make_user(root, HASH_NEW, 10, now)

        class Db:
            data_dir = new / "acct"

        class Eng:
            db = Db()

        engine = Eng()
        with (
            patch.object(Path, "home", return_value=tmp_path),
            patch.object(mcp_server, "_engine", engine),
        ):
            mcp_server._prune_idle_users(90)
            assert mcp_server._engine is engine
        assert not old.exists()

    def test_no_engine_cached(self, tmp_path):
        now = time.time()
        old = _make_user(tmp_path / ".pm-trader" / "remote", HASH_OLD, 100, now)
        with (
            patch.object(Path, "home", return_value=tmp_path),
            patch.object(mcp_server, "_engine", None),
        ):
            assert mcp_server._prune_idle_users(90) == [old]

    def test_an_os_error_is_reported_not_raised(self, tmp_path, capsys):
        with patch.object(mcp_server, "prune_remote", side_effect=OSError("boom")):
            assert mcp_server._prune_idle_users(90) == []
        assert "remote prune failed: boom" in capsys.readouterr().err

    def test_schedule_runs_on_the_tool_executor_and_rearms(self):
        seen: dict[str, str] = {}

        def fake_prune(days):
            seen["thread"] = threading.current_thread().name
            seen["days"] = days
            return []

        with (
            patch.object(mcp_server, "_prune_idle_users", fake_prune),
            patch.object(threading, "Timer") as timer,
        ):
            mcp_server._schedule_prune(7).result(timeout=10)
        assert seen["thread"].startswith("pm-trader-mcp")
        assert seen["days"] == 7
        timer.assert_called_once_with(
            86400.0, mcp_server._schedule_prune, (7, 86400.0)
        )
        assert timer.return_value.daemon is True
        timer.return_value.start.assert_called_once_with()

    def test_zero_disables_the_schedule(self, tmp_path):
        config = RemoteAuthConfig(
            issuer=ISSUER, audience=AUDIENCE, resource_url=RESOURCE_URL,
            prune_idle_days=0,
        )
        with (
            patch.object(Path, "home", return_value=tmp_path),
            patch.object(mcp_server, "_remote", None),
            patch.object(mcp_server, "_server_lock", None),
            patch.object(mcp_server, "_schedule_prune") as schedule,
            patch.object(mcp_server.mcp, "run"),
            patch.object(mcp_server.mcp, "remove_tool"),
            patch.object(mcp_server.mcp.settings, "auth", None),
            patch.object(mcp_server.mcp, "_token_verifier", None),
        ):
            mcp_server._run("streamable-http", "127.0.0.1", 8000, config)
            mcp_server._server_lock.close()
        schedule.assert_not_called()

    def test_flag_env_and_validation(self, monkeypatch):
        monkeypatch.delenv("PM_TRADER_PRUNE_IDLE_DAYS", raising=False)
        parser = mcp_server._build_arg_parser()
        assert parser.parse_args([]).prune_idle_days == 90
        assert parser.parse_args(["--prune-idle-days", "0"]).prune_idle_days == 0
        monkeypatch.setenv("PM_TRADER_PRUNE_IDLE_DAYS", "30")
        assert mcp_server._build_arg_parser().parse_args([]).prune_idle_days == 30
        with pytest.raises(RemoteAuthConfigError, match="negative"):
            RemoteAuthConfig(
                issuer=ISSUER, audience=AUDIENCE, resource_url=RESOURCE_URL,
                prune_idle_days=-1,
            )

    def test_before_delete_not_called_for_dry_run_or_nothing(self, tmp_path):
        now = time.time()
        old = _make_user(tmp_path / ".pm-trader" / "remote", HASH_OLD, 100, now)
        calls: list = []
        with patch.object(Path, "home", return_value=tmp_path):
            prune_remote(90, dry_run=True, before_delete=calls.append)
            prune_remote(900, before_delete=calls.append)
        assert calls == [] and old.exists()


def _market():
    from pm_trader.models import Market

    return Market(
        condition_id="0xabc", slug="m", question="Q?", description="",
        outcomes=["Yes", "No"], outcome_prices=[0.5, 0.5], tokens=[],
        active=True, closed=False,
    )


class TestCapOnCreationOnly:
    def _full(self, tmp_path, cap=2):
        _, patches = _remote(tmp_path, max_accounts=cap)
        return patches

    def test_creation_over_cap_refused_reads_dont_create(self, tmp_path):
        patches = self._full(tmp_path)
        with patches[0], patches[1], patches[2], _as_user(_user("alice")):
            root = user_root(ISSUER, "alice")
            for n in range(2):
                assert json.loads(mcp_server.init_account(account=f"a{n}"))["ok"]
            assert _code(mcp_server.init_account(account="a2")) == "ACCOUNT_LIMIT"
            typo = json.loads(mcp_server.get_balance(account="typo"))
            assert typo["code"] == "not_initialized"
            assert not (root / "typo").exists()
            assert not (root / "a2").exists()
            assert sorted(p.name for p in root.iterdir()) == ["a0", "a1"]

    def test_failed_reads_do_not_consume_quota(self, tmp_path):
        patches = self._full(tmp_path)
        with patches[0], patches[1], patches[2], _as_user(_user("alice")):
            for n in range(5):
                mcp_server.get_balance(account=f"typo{n}")
            assert json.loads(mcp_server.init_account(account="real"))["ok"]

    def test_market_tools_work_at_the_cap(self, tmp_path):
        from unittest.mock import MagicMock

        patches = self._full(tmp_path)
        with patches[0], patches[1], patches[2], _as_user(_user("alice")):
            for n in range(2):
                mcp_server.init_account(account=f"a{n}")
            engine = mcp_server._get_engine()  # implicit default, at the cap
            engine.api.search_markets = MagicMock(return_value=[_market()])
            engine.api.list_markets = MagicMock(return_value=[_market()])
            for out in (
                mcp_server.search_markets("q"),
                mcp_server.list_markets(),
            ):
                assert json.loads(out)["ok"] is True
