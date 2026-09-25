"""Public /oc bot wiring — asserts the shipped workflow + opencode.json.

Parses the real workflow YAML (yaml.safe_load) and executes the shipped
shell steps against stub git/gh binaries. Does not reimplement OpenCode or
mock YAML into a parallel config.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS_DIR / "opencode.yml"
TEST_WORKFLOW = WORKFLOWS_DIR / "test.yml"
CONFIG = ROOT / "opencode.json"

STRICT_REF_RE = r"^[A-Za-z0-9][A-Za-z0-9._/-]*$"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _opencode_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _triggers(wf: dict) -> dict:
    # YAML 1.1 parses the bare `on:` key as boolean True.
    return wf.get(True) or wf.get("on") or {}


def _job(name: str) -> dict:
    job = _workflow().get("jobs", {}).get(name)
    assert isinstance(job, dict), f"job {name!r} missing from {WORKFLOW}"
    return job


def _step(job_name: str, step_name: str) -> dict:
    for step in _job(job_name).get("steps") or []:
        if step.get("name") == step_name:
            return step
    raise AssertionError(f"step {step_name!r} missing from job {job_name!r}")


def _step_run(job_name: str, step_name: str) -> str:
    run = _step(job_name, step_name).get("run")
    assert isinstance(run, str) and run.strip(), (job_name, step_name)
    return run


def _opencode_step(job_name: str) -> dict:
    for step in _job(job_name).get("steps") or []:
        if str(step.get("uses", "")).startswith("anomalyco/opencode"):
            return step
    raise AssertionError(f"opencode step missing from job {job_name!r}")


def _secret_refs(value: object) -> list[str]:
    return re.findall(r"secrets\.[A-Za-z0-9_]+", str(value))


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
        wf = _workflow()
        assert wf["env"]["OPENCODE_MODEL"] == "freeinference/qwen3.6-35b"
        assert wf["env"]["OPENCODE_IMPLEMENT_MODEL"] == "freeinference/deepseek-v4-flash"
        for job_name in ("comment", "triage", "review"):
            assert _opencode_step(job_name)["with"]["model"] == "${{ env.OPENCODE_MODEL }}"

    def test_comment_job_triggers_oc_and_opencode(self) -> None:
        wf = _workflow()
        assert "issue_comment" in _triggers(wf)
        assert _opencode_step("comment")["with"]["mentions"] == "/opencode,/oc"

    def test_jobs_pass_freeinference_secret(self) -> None:
        for job_name in ("comment", "triage", "review"):
            env = _step(job_name, "Preflight FreeInference key").get("env") or {}
            assert env.get("FREEINFERENCE_API_KEY") == "${{ secrets.FREEINFERENCE_PUBLIC_KEY }}"
            oc_env = _opencode_step(job_name).get("env") or {}
            assert oc_env.get("FREEINFERENCE_API_KEY") == "${{ secrets.FREEINFERENCE_PUBLIC_KEY }}"
        impl_env = _step("implement", "Preflight FreeInference key").get("env") or {}
        assert impl_env.get("FREEINFERENCE_API_KEY") == "${{ secrets.FREEINFERENCE_API_KEY }}"
        assert "secrets.OPENCODE_API_KEY" not in _workflow_text()

    def test_preflight_fails_closed_without_key(self) -> None:
        """Missing/empty secret must fail the job before OpenCode starts."""
        wf = _workflow()
        preflight_runs = [
            step["run"]
            for job in wf["jobs"].values()
            for step in job.get("steps") or []
            if step.get("name") == "Preflight FreeInference key"
        ]
        assert len(preflight_runs) == 4
        for run in preflight_runs:
            assert "FREEINFERENCE_API_KEY is empty" in run
            assert "exit 1" in run
        # Local actions resolve from the checked-out tree; on PR review-comment
        # events that tree is PR-controlled, so preflight must stay inline.
        assert "uses: ./.github/actions/" not in _workflow_text()


class TestClosedLoopWiring:
    """Issue → gated implement → review. No self-merge; branch protection gates merges."""

    def test_implement_is_gated_not_every_issue(self) -> None:
        impl_if = str(_job("implement").get("if", ""))
        assert "bot:implement" in impl_if
        assert "/oc implement" in impl_if
        triage_prompt = str(_opencode_step("triage").get("with", {}).get("prompt", ""))
        assert "Do not write code or push" in triage_prompt
        assert _opencode_step("triage")["with"]["model"] == "${{ env.OPENCODE_MODEL }}"

    def test_implement_uses_flash_not_qwen(self) -> None:
        assert _workflow()["env"]["OPENCODE_IMPLEMENT_MODEL"] == "freeinference/deepseek-v4-flash"
        assert _opencode_step("implement")["with"]["model"] == "${{ env.OPENCODE_IMPLEMENT_MODEL }}"
        assert _opencode_step("comment")["with"]["model"] == "${{ env.OPENCODE_MODEL }}"

    def test_review_cannot_write_repo_contents(self) -> None:
        perms = _job("review").get("permissions") or {}
        assert perms.get("contents") == "read"
        prompt = str(_opencode_step("review").get("with", {}).get("prompt", ""))
        assert "Do not push commits" in prompt

    def test_implement_gated_to_trusted_actors(self) -> None:
        run = _step_run("implement", "Gate to trusted actors")
        assert "collaborators/${ACTOR}/permission" in run
        assert "admin|maintain|write" in run

    def test_implement_command_and_comment_exclusion_aligned(self) -> None:
        # Review comments must route to implement, not fall between both jobs.
        impl_if = str(_job("implement").get("if", ""))
        comment_if = str(_job("comment").get("if", ""))
        assert "pull_request_review_comment" in impl_if
        for cond in (impl_if, comment_if):
            assert "startsWith(github.event.comment.body, '/oc implement')" in cond

    def test_comment_job_has_no_contents_write(self) -> None:
        perms = _job("comment").get("permissions") or {}
        assert perms.get("contents") != "write"
        assert perms.get("issues") == "write"
        assert perms.get("pull-requests") == "write"

    def test_no_self_merge_on_implement_or_review(self) -> None:
        for name in ("implement", "review"):
            blob = json.dumps(_job(name))
            assert "gh pr merge" not in blob
            assert "pulls/" not in blob
            assert "/merge" not in blob

    def test_exact_job_set_and_no_label_gate(self) -> None:
        """No label-reconciliation job exists; branch protection is the merge gate."""
        jobs = list(_workflow()["jobs"].keys())
        assert jobs == ["comment", "triage", "implement", "dispatch-tests", "review"]
        text = _workflow_text()
        assert "workflow_run" not in text
        assert "statuses:" not in text
        assert "checks:" not in text


class TestDispatchPrivilegeSeparation:
    """The model job must never hold a token that can dispatch workflows."""

    def test_implement_permissions_lack_actions(self) -> None:
        assert _job("implement")["permissions"] == {
            "contents": "write",
            "issues": "write",
            "pull-requests": "write",
        }

    def test_dispatch_tests_job_isolated(self) -> None:
        job = _job("dispatch-tests")
        assert job["needs"] == "implement"
        assert job["permissions"] == {"actions": "write", "contents": "read"}
        steps = job.get("steps") or []
        assert steps, "dispatch-tests must have steps"
        for step in steps:
            uses = str(step.get("uses", ""))
            assert not uses.startswith("actions/checkout")
            assert not uses.startswith("anomalyco/opencode")
            for value in (step.get("env") or {}).values():
                for ref in _secret_refs(value):
                    assert ref == "secrets.GITHUB_TOKEN"

    def test_implement_publishes_branch_output(self) -> None:
        outputs = _job("implement").get("outputs") or {}
        assert outputs.get("branch") == "${{ steps.publish-branch.outputs.branch }}"
        step = _step("implement", "Publish pushed branch")
        assert step.get("id") == "publish-branch"
        run = step["run"]
        assert STRICT_REF_RE in run
        assert '>> "$GITHUB_OUTPUT"' in run

    def test_dispatch_step_consumes_validated_output(self) -> None:
        step = _step("dispatch-tests", "Dispatch Tests on the validated branch")
        env = step.get("env") or {}
        assert env.get("BRANCH") == "${{ needs.implement.outputs.branch }}"
        run = step["run"]
        assert "gh workflow run Tests" in run
        assert '--ref "${BRANCH}"' in run
        # No expressions inside the run block: the branch arrives via env only,
        # so a hostile ref can never inject workflow-expression evaluation.
        assert "${{" not in run

    def test_no_other_job_dispatches_or_holds_actions_scope(self) -> None:
        for name, job in _workflow()["jobs"].items():
            if name == "dispatch-tests":
                continue
            for step in job.get("steps") or []:
                assert "gh workflow run" not in str(step.get("run", "")), name
            assert "actions" not in (job.get("permissions") or {}), name

    def test_tests_workflow_is_dispatchable_and_read_only(self) -> None:
        wf = yaml.safe_load(TEST_WORKFLOW.read_text(encoding="utf-8"))
        assert wf["permissions"] == {"contents": "read"}
        assert "secrets." not in TEST_WORKFLOW.read_text(encoding="utf-8")
        assert "workflow_dispatch" in _triggers(wf)


@pytest.mark.parametrize(
    "path",
    sorted(WORKFLOWS_DIR.glob("*.yml")),
    ids=lambda p: p.name,
)
def test_workflow_yaml_parses(path: Path) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), path
    assert "jobs" in data, path


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
    contents = '/contents/'
    if contents in url:
        key = 'GH_STUB_BRANCH_SHA' if '?ref=' in url else 'GH_STUB_DEFAULT_SHA'
        data = {'sha': os.environ.get(key, '')}
    elif marker in url:
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


def _write_stubs(tmp_path: Path) -> Path:
    stub_log = tmp_path / "gh.log"
    for name, body in (("git", DISPATCH_STUB_GIT), ("gh", DISPATCH_STUB_GH)):
        stub = tmp_path / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return stub_log


def _stub_env(tmp_path: Path, stub_log: Path, extra: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}{os.pathsep}{env.get('PATH', '')}"
    env["GH_STUB_LOG"] = str(stub_log)
    env["GH_TOKEN"] = "test"
    env["REPO"] = "agent-next/polymarket-paper-trader"
    env.update(extra)
    return env


class TestPublishBranchScript:
    """Execute the shipped publish step against stub git + gh."""

    def _run(
        self,
        tmp_path: Path,
        head_branch: str,
        remote_branch: str = "",
        default_branch: str = "main",
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        stub_log = _write_stubs(tmp_path)
        github_output = tmp_path / "github_output"
        github_output.touch()
        env = _stub_env(
            tmp_path,
            stub_log,
            {
                "GIT_STUB_BRANCH": head_branch,
                "GH_STUB_REMOTE_BRANCH": remote_branch,
                "GH_STUB_DEFAULT_BRANCH": default_branch,
                "GITHUB_OUTPUT": str(github_output),
            },
        )
        proc = subprocess.run(
            ["bash", "-c", _step_run("implement", "Publish pushed branch")],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return proc, github_output.read_text(encoding="utf-8")

    def test_publishes_the_branch_left_checked_out(self, tmp_path: Path) -> None:
        branch = "opencode/issue42-20260924T120000"
        proc, output = self._run(tmp_path, branch, remote_branch=branch)
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert f"branch={branch}" in output.splitlines()

    def test_publishes_same_repo_pr_branch(self, tmp_path: Path) -> None:
        # /oc implement on a same-repo PR: the action checks out the PR head branch.
        proc, output = self._run(tmp_path, "feat/foo", remote_branch="feat/foo")
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert "branch=feat/foo" in output.splitlines()

    def test_fails_loud_on_detached_head(self, tmp_path: Path) -> None:
        proc, output = self._run(tmp_path, "HEAD")
        assert proc.returncode == 1
        assert "branch=" not in output

    def test_fails_loud_on_default_branch(self, tmp_path: Path) -> None:
        proc, output = self._run(tmp_path, "main", remote_branch="main")
        assert proc.returncode == 1
        assert "branch=" not in output

    def test_fails_loud_when_branch_not_on_origin(self, tmp_path: Path) -> None:
        # Fork-PR path: the action pushes to the fork remote, not this repo.
        proc, output = self._run(
            tmp_path, "opencode/pr9-x", remote_branch="other-branch"
        )
        assert proc.returncode == 1
        assert "branch=" not in output
        assert "cannot dispatch" in proc.stderr

    @pytest.mark.parametrize(
        "bad_branch",
        ["-evil", "has space", "a;b", "$(touch PWNED)"],
        ids=["leading-dash", "space", "semicolon", "command-substitution"],
    )
    def test_refuses_unsafe_refs(self, tmp_path: Path, bad_branch: str) -> None:
        proc, output = self._run(tmp_path, bad_branch, remote_branch=bad_branch)
        assert proc.returncode == 1
        assert "branch=" not in output
        assert not (tmp_path / "PWNED").exists()


class TestDispatchScript:
    """Execute the shipped dispatch step against stub gh."""

    def _run(
        self,
        tmp_path: Path,
        branch: str,
        default_sha: str = "abc123",
        branch_sha: str = "abc123",
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        stub_log = _write_stubs(tmp_path)
        env = _stub_env(
            tmp_path,
            stub_log,
            {
                "GIT_STUB_BRANCH": branch,
                "GH_STUB_DEFAULT_SHA": default_sha,
                "GH_STUB_BRANCH_SHA": branch_sha,
                "BRANCH": branch,
                "WORKFLOW_PATH": ".github/workflows/test.yml",
            },
        )
        proc = subprocess.run(
            [
                "bash",
                "-c",
                _step_run("dispatch-tests", "Dispatch Tests on the validated branch"),
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        logged = stub_log.read_text(encoding="utf-8") if stub_log.exists() else ""
        return proc, logged

    def test_dispatches_when_workflow_file_is_identical(self, tmp_path: Path) -> None:
        branch = "opencode/issue7-20260924T120000"
        proc, logged = self._run(tmp_path, branch)
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert (
            "workflow run Tests --repo agent-next/polymarket-paper-trader --ref "
            + branch
            in logged
        )

    def test_refuses_when_workflow_file_differs(self, tmp_path: Path) -> None:
        # A pushed branch that rewrote test.yml must never be dispatched —
        # it would run with repository secrets attached.
        proc, logged = self._run(tmp_path, "feat/x", branch_sha="deadbeef")
        assert proc.returncode == 1
        assert "workflow run" not in logged

    def test_refuses_when_workflow_file_missing_on_branch(self, tmp_path: Path) -> None:
        proc, logged = self._run(tmp_path, "feat/x", branch_sha="")
        assert proc.returncode == 1
        assert "workflow run" not in logged

    @pytest.mark.parametrize(
        "bad_branch",
        ["-rf", "a b", "", "x;rm -rf"],
        ids=["leading-dash", "space", "empty", "semicolon"],
    )
    def test_refuses_unsafe_branch_output(self, tmp_path: Path, bad_branch: str) -> None:
        proc, logged = self._run(tmp_path, bad_branch)
        assert proc.returncode == 1
        assert "workflow run" not in logged


class TestPreflightFailsClosed:
    def test_empty_secret_exits_nonzero(self) -> None:
        proc = subprocess.run(
            ["bash", "-c", _step_run("comment", "Preflight FreeInference key")],
            env={"PATH": "/usr/bin:/bin", "FREEINFERENCE_API_KEY": ""},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert proc.returncode != 0, (proc.stdout, proc.stderr)
        assert "FREEINFERENCE_API_KEY is empty" in proc.stderr
