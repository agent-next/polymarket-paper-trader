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
        # The key comes from a file staged outside the workspace; an {env:}
        # reference would put it in the opencode process env, which the
        # agent's shell tools inherit.
        assert provider["options"]["apiKey"] == "{file:~/.freeinference-api-key}"
        assert "FREEINFERENCE_API_KEY" not in (provider.get("env") or [])
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

    def test_provider_key_never_enters_agent_env(self) -> None:
        # The action runs `opencode github run` with the step env, which the
        # agent's shell tools inherit. The provider key must reach the model
        # via the {file:} reference in opencode.json, not the step env.
        for job_name in ("comment", "triage", "implement", "review"):
            oc_env = _opencode_step(job_name).get("env") or {}
            assert "FREEINFERENCE_API_KEY" not in oc_env, job_name
            assert _secret_refs(json.dumps(oc_env)) == ["secrets.GITHUB_TOKEN"], job_name
        assert "secrets.OPENCODE_API_KEY" not in _workflow_text()

    def test_staging_step_holds_key_and_writes_owner_only_file(self) -> None:
        for job_name in ("comment", "triage", "review"):
            env = _step(job_name, "Stage FreeInference key").get("env") or {}
            assert env.get("FREEINFERENCE_API_KEY") == "${{ secrets.FREEINFERENCE_PUBLIC_KEY }}"
        impl_env = _step("implement", "Stage FreeInference key").get("env") or {}
        assert impl_env.get("FREEINFERENCE_API_KEY") == "${{ secrets.FREEINFERENCE_API_KEY }}"

    def test_staged_key_file_matches_opencode_config(self) -> None:
        api_key = _opencode_config()["provider"]["freeinference"]["options"]["apiKey"]
        m = re.fullmatch(r"\{file:~/([^}]+)\}", api_key)
        assert m, f"apiKey must be a {{file:~/name}} reference, got {api_key!r}"
        for job_name in ("comment", "triage", "implement", "review"):
            run = _step_run(job_name, "Stage FreeInference key")
            assert f'"$HOME/{m.group(1)}"' in run, job_name
            assert "umask 077" in run, job_name

    def test_staged_key_removed_after_opencode(self) -> None:
        for job_name in ("comment", "triage", "implement", "review"):
            steps = _job(job_name).get("steps") or []
            names = [s.get("name") for s in steps]
            oc_idx = next(
                i
                for i, s in enumerate(steps)
                if str(s.get("uses", "")).startswith("anomalyco/opencode")
            )
            rm_idx = names.index("Remove staged provider key")
            assert rm_idx > oc_idx, job_name
            cleanup = steps[rm_idx]
            assert cleanup.get("if") == "always()"
            assert ".freeinference-api-key" in (cleanup.get("run") or "")

    def test_stage_key_fails_closed_without_key(self) -> None:
        """Missing/empty secret must fail the job before OpenCode starts."""
        wf = _workflow()
        stage_runs = [
            step["run"]
            for job in wf["jobs"].values()
            for step in job.get("steps") or []
            if step.get("name") == "Stage FreeInference key"
        ]
        assert len(stage_runs) == 4
        for run in stage_runs:
            assert "FREEINFERENCE_API_KEY is empty" in run
            assert "exit 1" in run
        # Local actions resolve from the checked-out tree; on PR review-comment
        # events that tree is PR-controlled, so staging must stay inline.
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

    def test_review_runs_on_pull_request_same_repo_only(self) -> None:
        # review feeds a PR diff to a secret-bearing model step. pull_request
        # already withholds secrets from fork PRs, and the head-repo guard
        # keeps fork diffs out of the job entirely; pull_request_target would
        # hand base-repo secrets to runs triggered by untrusted forks.
        triggers = _triggers(_workflow())
        assert "pull_request" in triggers
        assert "pull_request_target" not in triggers
        assert "pull_request_target" not in _workflow_text()
        review_if = str(_job("review").get("if", ""))
        assert "github.event_name == 'pull_request'" in review_if
        assert (
            "github.event.pull_request.head.repo.full_name == github.repository"
            in review_if
        )
        assert "github.event.pull_request.draft == false" in review_if

    def test_review_never_checks_out_pr_code(self) -> None:
        # The workspace stays on trusted base code; the diff reaches the model
        # as text via the event payload, never as checked-out PR code.
        checkout_steps = [
            step
            for step in _job("review").get("steps") or []
            if str(step.get("uses", "")).startswith("actions/checkout")
        ]
        assert checkout_steps, "review must checkout the base tree"
        for step in checkout_steps:
            assert (step.get("with") or {}).get("ref") == (
                "${{ github.event.pull_request.base.sha }}"
            )
            assert "pull_request.head" not in json.dumps(step)

    def test_review_permissions_are_minimal(self) -> None:
        # pull-requests stays read: write would let the model submit reviews
        # (including approving ones). issues: write still lets the action
        # post its comment via the issues API.
        assert _job("review").get("permissions") == {
            "contents": "read",
            "pull-requests": "read",
            "issues": "write",
        }

    def test_review_tolerates_cosmetic_reaction_403(self) -> None:
        # The action reacts to its own comment, which needs pull-requests:
        # write the read-only review token lacks (403, issue #94). Only the
        # advisory opencode step is non-fatal; the key-staging step and the
        # job permissions stay strict.
        assert _opencode_step("review").get("continue-on-error") is True
        assert _step("review", "Stage FreeInference key").get("continue-on-error") is None
        assert _job("review").get("permissions", {}).get("pull-requests") == "read"
        for name in ("comment", "triage", "implement"):
            assert _opencode_step(name).get("continue-on-error") is None

    def test_review_cannot_approve_pull_requests(self) -> None:
        # Submitting a review requires pull-requests: write; a read token
        # makes an injected APPROVE impossible regardless of the prompt.
        perms = _job("review").get("permissions") or {}
        assert perms.get("pull-requests") == "read"
        prompt = str(_opencode_step("review").get("with", {}).get("prompt", ""))
        assert "Do not approve" in prompt

    def test_every_secret_bearing_prompt_forbids_printing_secrets(self) -> None:
        for name, job in _workflow()["jobs"].items():
            for step in job.get("steps") or []:
                if not str(step.get("uses", "")).startswith("anomalyco/opencode"):
                    continue
                if not _secret_refs(json.dumps(step.get("env") or {})):
                    continue
                prompt = str(step.get("with", {}).get("prompt", ""))
                assert "Never print secrets" in prompt, name

    def test_implement_gated_to_trusted_actors(self) -> None:
        # The gate must be step 0, ahead of checkout and every secret env:
        # no secret is injected before it passes.
        steps = _job("implement").get("steps") or []
        assert steps, "implement must have steps"
        gate = steps[0]
        assert gate.get("name") == "Gate to trusted actors"
        assert _secret_refs(json.dumps(gate)) == ["secrets.GITHUB_TOKEN"]
        run = gate.get("run") or ""
        assert "collaborators/${ACTOR}/permission" in run
        assert "admin|maintain|write" in run
        assert "exit 1" in run

    def test_comment_and_triage_gated_to_collaborators(self) -> None:
        # issue_comment / issues events always run with base-repo secrets, so
        # an untrusted commenter or issue opener could otherwise feed hostile
        # prompt text to the secret-bearing model step. Same gate as
        # implement, first: no secret is injected before it passes.
        for name in ("comment", "triage"):
            steps = _job(name).get("steps") or []
            assert steps, name
            gate = steps[0]
            assert gate.get("name") == "Gate to trusted actors", name
            assert _secret_refs(json.dumps(gate)) == ["secrets.GITHUB_TOKEN"], name
            run = gate.get("run") or ""
            assert "collaborators/${ACTOR}/permission" in run, name
            assert "admin|maintain|write" in run, name
            assert "exit 1" in run, name

    def test_gates_fail_closed_on_api_errors(self) -> None:
        # `|| echo none` degrades any gh api failure to 'none', which hits the
        # exit-1 branch. Pin it in every gate: a regression to a pass-through
        # value (e.g. 'admin') would fail open while keeping every other
        # substring assertion green.
        for name in ("comment", "triage", "implement"):
            run = _step_run(name, "Gate to trusted actors")
            assert "|| echo none" in run, name
            assert "admin|maintain|write" in run, name

    def test_pr_comment_gates_resolve_head_repo_fail_closed(self) -> None:
        # PR-comment triggers make the agent check out the PR head, so the
        # head repo must be this repository. issue_comment payloads carry no
        # head repo, so the gate resolves it via the API — fail closed.
        for name in ("comment", "implement"):
            gate = _step(name, "Gate to trusted actors")
            env = gate.get("env") or {}
            assert env.get("PR_NUMBER"), name
            assert env.get("PR_HEAD_REPO"), name
            run = gate.get("run") or ""
            assert "pulls/${PR_NUMBER}" in run, name
            assert "head.repo.full_name" in run, name
            assert run.count("|| echo none") >= 2, name

    def test_pr_comment_triggers_require_same_repo_head(self) -> None:
        # pull_request_review_comment carries head.repo in the payload, so the
        # job `if` refuses fork PRs before any step runs.
        for name in ("comment", "implement"):
            cond = str(_job(name).get("if", ""))
            assert (
                "github.event.pull_request.head.repo.full_name == github.repository"
                in cond
            ), name

    def test_pr_context_checkouts_pin_base_sha(self) -> None:
        # On pull_request_review_comment the default actions/checkout ref is
        # the PR merge commit; pin to base so the pre-agent tree is base code.
        for name in ("comment", "implement"):
            checkouts = [
                step
                for step in _job(name).get("steps") or []
                if str(step.get("uses", "")).startswith("actions/checkout")
            ]
            assert checkouts, name
            for step in checkouts:
                assert (step.get("with") or {}).get("ref") == (
                    "${{ github.event.pull_request.base.sha || github.sha }}"
                ), name

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
            assert "/merge" not in blob
        # implement's gate legitimately reads pulls/ metadata; review may not.
        assert "pulls/" not in json.dumps(_job("review"))

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
    collab = '/collaborators/'
    pulls = '/pulls/'
    if collab in url:
        if os.environ.get('GH_STUB_PERM_FAIL'):
            raise SystemExit(3)
        data = {'permission': os.environ.get('GH_STUB_PERM', 'none')}
    elif pulls in url:
        if os.environ.get('GH_STUB_PULLS_FAIL'):
            raise SystemExit(3)
        data = {'head': {'repo': {'full_name': os.environ.get('GH_STUB_HEAD_REPO', '')}}}
    elif contents in url:
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


class TestTrustedActorGate:
    """Execute the shipped gate scripts against stub git + gh."""

    SAME_REPO = "agent-next/polymarket-paper-trader"

    def _run(
        self,
        tmp_path: Path,
        job: str,
        perm: str = "admin",
        pr_number: str = "",
        pr_head_repo: str = "",
        stub_head_repo: str = "",
        perm_fail: bool = False,
        pulls_fail: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        stub_log = _write_stubs(tmp_path)
        extra = {
            "ACTOR": "octocat",
            "GH_STUB_PERM": perm,
            "GH_STUB_HEAD_REPO": stub_head_repo,
        }
        if pr_number:
            extra["PR_NUMBER"] = pr_number
        if pr_head_repo:
            extra["PR_HEAD_REPO"] = pr_head_repo
        if perm_fail:
            extra["GH_STUB_PERM_FAIL"] = "1"
        if pulls_fail:
            extra["GH_STUB_PULLS_FAIL"] = "1"
        env = _stub_env(tmp_path, stub_log, extra)
        return subprocess.run(
            ["bash", "-c", _step_run(job, "Gate to trusted actors")],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )

    @pytest.mark.parametrize("job", ["comment", "triage", "implement"])
    def test_allows_trusted_actor(self, tmp_path: Path, job: str) -> None:
        proc = self._run(tmp_path, job)
        assert proc.returncode == 0, proc.stderr

    @pytest.mark.parametrize("job", ["comment", "triage", "implement"])
    def test_blocks_untrusted_actor(self, tmp_path: Path, job: str) -> None:
        proc = self._run(tmp_path, job, perm="none")
        assert proc.returncode == 1
        assert "lacks write access" in proc.stderr

    @pytest.mark.parametrize("job", ["comment", "triage", "implement"])
    def test_blocks_when_permission_lookup_fails(self, tmp_path: Path, job: str) -> None:
        proc = self._run(tmp_path, job, perm_fail=True)
        assert proc.returncode == 1

    @pytest.mark.parametrize("job", ["comment", "implement"])
    def test_allows_same_repo_pr_via_payload(self, tmp_path: Path, job: str) -> None:
        proc = self._run(tmp_path, job, pr_number="9", pr_head_repo=self.SAME_REPO)
        assert proc.returncode == 0, proc.stderr

    @pytest.mark.parametrize("job", ["comment", "implement"])
    def test_blocks_fork_pr_via_payload(self, tmp_path: Path, job: str) -> None:
        proc = self._run(tmp_path, job, pr_number="9", pr_head_repo="someone/fork")
        assert proc.returncode == 1
        assert "fork" in proc.stderr or "not" in proc.stderr

    @pytest.mark.parametrize("job", ["comment", "implement"])
    def test_allows_same_repo_pr_via_api(self, tmp_path: Path, job: str) -> None:
        # issue_comment on a PR: payload has no head repo, gate asks the API.
        proc = self._run(tmp_path, job, pr_number="9", stub_head_repo=self.SAME_REPO)
        assert proc.returncode == 0, proc.stderr

    @pytest.mark.parametrize("job", ["comment", "implement"])
    def test_blocks_fork_pr_via_api(self, tmp_path: Path, job: str) -> None:
        proc = self._run(tmp_path, job, pr_number="9", stub_head_repo="someone/fork")
        assert proc.returncode == 1

    @pytest.mark.parametrize("job", ["comment", "implement"])
    def test_blocks_pr_when_head_lookup_fails(self, tmp_path: Path, job: str) -> None:
        proc = self._run(tmp_path, job, pr_number="9", pulls_fail=True)
        assert proc.returncode == 1

    @pytest.mark.parametrize("job", ["comment", "implement"])
    def test_blocks_empty_head_repo_via_api(self, tmp_path: Path, job: str) -> None:
        proc = self._run(tmp_path, job, pr_number="9", stub_head_repo="")
        assert proc.returncode == 1

    @pytest.mark.parametrize("job", ["comment", "implement"])
    def test_blocks_nonnumeric_pr_number(self, tmp_path: Path, job: str) -> None:
        proc = self._run(tmp_path, job, pr_number="9;id", stub_head_repo=self.SAME_REPO)
        assert proc.returncode == 1


class TestStageFreeInferenceKey:
    """Execute the shipped key-staging step; the file must be owner-only."""

    def _run(
        self, tmp_path: Path, job: str, key: str
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        proc = subprocess.run(
            ["bash", "-c", _step_run(job, "Stage FreeInference key")],
            env={
                "PATH": "/usr/bin:/bin",
                "FREEINFERENCE_API_KEY": key,
                "HOME": str(home),
            },
            capture_output=True,
            text=True,
            timeout=10,
        )
        return proc, home

    @pytest.mark.parametrize("job", ["comment", "triage", "implement", "review"])
    def test_writes_key_file_owner_only(self, tmp_path: Path, job: str) -> None:
        proc, home = self._run(tmp_path, job, "sk-test-key")
        assert proc.returncode == 0, proc.stderr
        key_file = home / ".freeinference-api-key"
        assert key_file.read_text(encoding="utf-8") == "sk-test-key"
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600

    @pytest.mark.parametrize("job", ["comment", "triage", "implement", "review"])
    def test_empty_secret_exits_nonzero(self, tmp_path: Path, job: str) -> None:
        proc, home = self._run(tmp_path, job, "")
        assert proc.returncode != 0, (proc.stdout, proc.stderr)
        assert "FREEINFERENCE_API_KEY is empty" in proc.stderr
        assert not (home / ".freeinference-api-key").exists()
