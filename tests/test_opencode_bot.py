"""Public /oc bot wiring — asserts the shipped workflow + opencode.json.

Reads the real files on disk. Does not reimplement OpenCode or mock YAML
into a parallel config.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "opencode.yml"
CONFIG = ROOT / "opencode.json"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _opencode_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _job_block(name: str) -> str:
    text = _workflow_text()
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [a-z0-9_-]+:|\Z)", text, re.M | re.S)
    assert match, f"job {name!r} missing from {WORKFLOW}"
    return match.group(0)


class TestPublicBotWiring:
    def test_shipped_files_exist(self) -> None:
        assert WORKFLOW.is_file(), WORKFLOW
        assert CONFIG.is_file(), CONFIG

    def test_provider_is_freeinference_with_qwen(self) -> None:
        cfg = _opencode_config()
        provider = cfg["provider"]["freeinference"]
        assert provider["options"]["baseURL"] == "https://freeinference.org/v1"
        assert "FREEINFERENCE_API_KEY" in provider["env"]
        assert "{env:FREEINFERENCE_API_KEY}" in provider["options"]["apiKey"]
        assert "qwen3.6-35b" in provider["models"]

    def test_workflow_model_is_freeinference_qwen(self) -> None:
        text = _workflow_text()
        assert re.search(r"^  OPENCODE_MODEL:\s*freeinference/qwen3.6-35b\s*$", text, re.M)
        assert "model: ${{ env.OPENCODE_MODEL }}" in text

    def test_comment_job_triggers_oc_and_opencode(self) -> None:
        text = _workflow_text()
        assert 'mentions: "/opencode,/oc"' in text
        assert "issue_comment:" in text

    def test_jobs_pass_freeinference_secret(self) -> None:
        text = _workflow_text()
        for job in ("comment", "triage", "review"):
            block = _job_block(job)
            assert "name: Preflight FreeInference key" in block, job
            assert "FREEINFERENCE_API_KEY: ${{ secrets.FREEINFERENCE_PUBLIC_KEY }}" in block, job
        implement = _job_block("implement")
        assert "FREEINFERENCE_API_KEY: ${{ secrets.FREEINFERENCE_API_KEY }}" in implement
        assert "secrets.OPENCODE_API_KEY" not in text

    def test_preflight_fails_closed_without_key(self) -> None:
        """Missing/empty secret must fail the job before OpenCode starts."""
        text = _workflow_text()
        assert text.count("name: Preflight FreeInference key") == 4
        assert "FREEINFERENCE_API_KEY is empty" in text
        assert "secrets.FREEINFERENCE_PUBLIC_KEY" in text
        # Local actions resolve from the checked-out tree; on PR review-comment
        # events that tree is PR-controlled, so preflight must stay inline.
        assert "uses: ./.github/actions/" not in text


class TestClosedLoopWiring:
    """Issue → gated implement → review. No self-merge; branch protection gates merges."""

    def test_implement_is_gated_not_every_issue(self) -> None:
        block = _job_block("implement")
        assert "bot:implement" in block
        assert "/oc implement" in block
        triage = _job_block("triage")
        assert "deepseek-v4-flash" not in triage
        assert "Do not write code or push" in triage

    def test_implement_uses_flash_not_qwen(self) -> None:
        text = _workflow_text()
        block = _job_block("implement")
        assert "model: ${{ env.OPENCODE_IMPLEMENT_MODEL }}" in block
        assert "OPENCODE_IMPLEMENT_MODEL: freeinference/deepseek-v4-flash" in text
        comment = _job_block("comment")
        assert "model: ${{ env.OPENCODE_MODEL }}" in comment

    def test_review_cannot_write_repo_contents(self) -> None:
        block = _job_block("review")
        assert re.search(r"contents:\s*read", block)
        assert not re.search(r"contents:\s*write", block)
        assert "Do not push commits" in block

    def test_implement_dispatches_tests_for_pushed_branch(self) -> None:
        """GITHUB_TOKEN pushes skip Tests; implement dispatches the ref it pushed."""
        block = _job_block("implement")
        assert "name: Dispatch Tests after GITHUB_TOKEN push" in block
        script = _dispatch_script()
        assert "git rev-parse --abbrev-ref HEAD" in script
        assert 'gh workflow run Tests --repo "${REPO}" --ref "${branch}"' in script
        assert "gh pr list" not in script  # never guess "the newest bot PR"
        assert "exit 1" in script
        tests = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
        assert "workflow_dispatch:" in tests

    def test_implement_passes_freeinference_to_preflight(self) -> None:
        block = _job_block("implement")
        assert "name: Preflight FreeInference key" in block
        assert "FREEINFERENCE_API_KEY: ${{ secrets.FREEINFERENCE_API_KEY }}" in block

    def test_implement_gated_to_trusted_actors(self) -> None:
        block = _job_block("implement")
        assert "name: Gate to trusted actors" in block
        assert "collaborators/${ACTOR}/permission" in block
        assert "admin|maintain|write" in block

    def test_implement_command_and_comment_exclusion_aligned(self) -> None:
        implement = _job_block("implement")
        comment = _job_block("comment")
        # Review comments must route to implement, not fall between both jobs.
        assert "pull_request_review_comment" in implement
        for block in (implement, comment):
            assert "startsWith(github.event.comment.body, '/oc implement')" in block

    def test_comment_job_has_no_contents_write(self) -> None:
        block = _job_block("comment")
        assert not re.search(r"contents:\s*write", block)
        assert re.search(r"issues:\s*write", block)
        assert re.search(r"pull-requests:\s*write", block)

    def test_no_self_merge_on_implement_or_review(self) -> None:
        for name in ("implement", "review"):
            block = _job_block(name)
            assert "gh pr merge" not in block
            assert "pulls/" not in block
            assert "/merge" not in block

    def test_exact_job_set_and_no_label_gate(self) -> None:
        """No label-reconciliation job exists; branch protection is the merge gate."""
        text = _workflow_text()
        jobs = re.findall(r"^  ([a-z0-9_-]+):$", text.split("jobs:", 1)[1], re.M)
        assert jobs == ["comment", "triage", "implement", "review"]
        assert "workflow_run" not in text
        assert "statuses:" not in text
        assert "checks:" not in text


DISPATCH_STUB_GIT = """#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
if args[:3] == ['rev-parse', '--abbrev-ref', 'HEAD']:
    print(os.environ.get('GIT_STUB_BRANCH', 'HEAD'))
    raise SystemExit(0)
raise SystemExit(2)
"""

DISPATCH_STUB_GH = """#!/usr/bin/env python3
import json, os, subprocess, sys
args = sys.argv[1:]
log = os.environ['GH_STUB_LOG']
with open(log, 'a', encoding='utf-8') as fh:
    fh.write(' '.join(args) + '\\n')
if args[:1] == ['api']:
    url = args[1]
    jq = args[args.index('--jq') + 1] if '--jq' in args else None
    marker = '/branches/'
    if marker in url:
        branch = url.split(marker, 1)[1]
        if branch != os.environ.get('GH_STUB_REMOTE_BRANCH', ''):
            raise SystemExit(1)
        data = {'name': branch}
    elif url == 'repos/' + os.environ['REPO']:
        data = {'default_branch': os.environ.get('GH_STUB_DEFAULT_BRANCH', 'main')}
    else:
        raise SystemExit(2)
    raw = json.dumps(data)
    if jq is None:
        print(raw)
        raise SystemExit(0)
    r = subprocess.run(['jq', '-r', jq], input=raw, text=True, capture_output=True)
    sys.stdout.write(r.stdout)
    raise SystemExit(r.returncode)
if args[:2] == ['workflow', 'run']:
    raise SystemExit(0)
raise SystemExit(0)
"""


class TestImplementDispatchScript:
    """Execute the shipped dispatch shell against stub git + gh."""

    def test_dispatches_the_branch_left_checked_out(self, tmp_path: Path) -> None:
        branch = "opencode/issue42-20260924T120000"
        proc, logged = _run_dispatch(tmp_path, branch, remote_branch=branch)
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert (
            "workflow run Tests --repo agent-next/polymarket-paper-trader --ref " + branch in logged
        )

    def test_dispatches_same_repo_pr_branch(self, tmp_path: Path) -> None:
        # /oc implement on a same-repo PR: the action checks out the PR head branch.
        proc, logged = _run_dispatch(tmp_path, "feat/foo", remote_branch="feat/foo")
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert "--ref feat/foo" in logged

    def test_fails_loud_on_detached_head(self, tmp_path: Path) -> None:
        proc, logged = _run_dispatch(tmp_path, "HEAD")
        assert proc.returncode == 1
        assert "workflow run" not in logged

    def test_fails_loud_on_default_branch(self, tmp_path: Path) -> None:
        proc, logged = _run_dispatch(tmp_path, "main", remote_branch="main")
        assert proc.returncode == 1
        assert "workflow run" not in logged

    def test_fails_loud_when_branch_not_on_origin(self, tmp_path: Path) -> None:
        # Fork-PR path: the action pushes to the fork remote, not this repo.
        proc, logged = _run_dispatch(tmp_path, "opencode/pr9-x", remote_branch="other-branch")
        assert proc.returncode == 1
        assert "workflow run" not in logged
        assert "cannot dispatch" in proc.stderr


def _run_dispatch(
    tmp_path: Path,
    head_branch: str,
    remote_branch: str = "",
    default_branch: str = "main",
) -> tuple[subprocess.CompletedProcess[str], str]:
    stub_log = tmp_path / "gh.log"
    for name, body in (("git", DISPATCH_STUB_GIT), ("gh", DISPATCH_STUB_GH)):
        stub = tmp_path / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}{os.pathsep}{env.get('PATH', '')}"
    env["GH_STUB_LOG"] = str(stub_log)
    env["GIT_STUB_BRANCH"] = head_branch
    env["GH_STUB_REMOTE_BRANCH"] = remote_branch
    env["GH_STUB_DEFAULT_BRANCH"] = default_branch
    env["GH_TOKEN"] = "test"
    env["REPO"] = "agent-next/polymarket-paper-trader"
    proc = subprocess.run(
        ["bash", "-c", _dispatch_script()],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    logged = stub_log.read_text(encoding="utf-8") if stub_log.exists() else ""
    return proc, logged


def _run_block(step_name: str) -> str:
    text = _workflow_text()
    match = re.search(
        rf"      - name: {re.escape(step_name)}\n"
        r"(?:        .*\n)*?"
        r"        run: \|\n"
        r"((?:          .*\n?)+)",
        text,
    )
    assert match, f"run block for {step_name!r} not found"
    return "\n".join(line[10:] for line in match.group(1).splitlines()) + "\n"


def _dispatch_script() -> str:
    return _run_block("Dispatch Tests after GITHUB_TOKEN push")


class TestPreflightFailsClosed:
    @pytest.mark.parametrize("var_name", ["FREEINFERENCE_API_KEY"])
    def test_empty_secret_exits_nonzero(self, var_name: str) -> None:
        proc = subprocess.run(
            ["bash", "-c", _run_block("Preflight FreeInference key")],
            env={"PATH": "/usr/bin:/bin", var_name: ""},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert proc.returncode != 0, (var_name, proc.stdout, proc.stderr)
        assert f"{var_name} is empty" in proc.stderr
