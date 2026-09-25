"""Outsider smoke test: exercise an installed polymarket-paper-trader as a user would.

Run from outside the source tree, in a fresh environment where the package was
installed from a built wheel (CI) or from PyPI (post-release). Exits non-zero on
any failure.

    python scripts/outsider_smoke.py [EXPECTED_VERSION]
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

STDIO_TOOLS = 30
LOCAL_ONLY = {"backtest", "pk_battle"}  # not served over streamable-http
EXPECTED_VERSION = sys.argv[1] if len(sys.argv) > 1 else ""


def _check(cond: bool, msg: str) -> None:
    if not cond:
        sys.exit(f"FAIL: {msg}")
    print(f"ok: {msg}")


async def _exercise(session: ClientSession, label: str) -> None:
    init = await session.initialize()
    _check(init.server_info.name == "pm-trader", f"{label}: serverInfo.name")
    if EXPECTED_VERSION:
        _check(init.server_info.version == EXPECTED_VERSION, f"{label}: version {init.server_info.version}")
    _check(bool(init.instructions), f"{label}: server instructions present")
    tools = await session.list_tools()
    names = {t.name for t in tools.tools}
    if label == "stdio":
        _check(len(names) == STDIO_TOOLS and LOCAL_ONLY <= names, f"stdio: {len(names)} tools")
    else:
        _check(len(names) == STDIO_TOOLS - 2 and not LOCAL_ONLY & names, f"http: {len(names)} tools, local-only removed")
    prompts = await session.list_prompts()
    names = [p.name for p in prompts.prompts]
    _check("trading_playbook" in names, f"{label}: trading_playbook prompt listed")
    prompt = await session.get_prompt("trading_playbook")
    text = prompt.messages[0].content.text
    _check("Polymarket" in text and len(text) > 2000, f"{label}: playbook body ({len(text)} chars)")
    res = await session.call_tool("init_account", {"balance": 1234})
    body = json.loads(res.content[0].text)
    _check(body.get("ok") is True, f"{label}: init_account ok")
    res = await session.call_tool("get_balance", {})
    body = json.loads(res.content[0].text)
    _check(body["data"]["cash"] == 1234, f"{label}: get_balance cash == 1234")


async def _stdio(data_dir: str) -> None:
    params = StdioServerParameters(
        command="pm-trader-mcp", env={**os.environ, "PM_TRADER_DATA_DIR": data_dir}
    )
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await _exercise(s, "stdio")


async def _http(data_dir: str) -> None:
    from mcp.client.streamable_http import streamable_http_client

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    proc = subprocess.Popen(
        ["pm-trader-mcp", "--transport", "streamable-http", "--port", str(port)],
        env={**os.environ, "PM_TRADER_DATA_DIR": data_dir},
    )
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as (r, w, *_):
            async with ClientSession(r, w) as s:
                await _exercise(s, "http")
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _strategy_from_user_dir(work: str) -> None:
    """A user's own examples/ strategy runs from their working directory."""
    os.makedirs(os.path.join(work, "examples"))
    with open(os.path.join(work, "examples", "outsider_probe.py"), "w") as f:
        f.write("def run(engine):\n    pass\n")
    out = subprocess.run(
        ["pm-trader", "strategy", "run", "examples.outsider_probe.run", "--balance", "777"],
        capture_output=True, text=True, cwd=work,
    )
    body = json.loads(out.stdout or "{}")
    ok = body.get("ok") is True and body["data"]["starting_balance"] == 777
    _check(ok, "strategy run from user dir" + ("" if ok else f": {body or out.stderr[-300:]}"))


def main() -> None:
    out = subprocess.run(["pm-trader", "--help"], capture_output=True, text=True, check=True)
    _check("strategy" in out.stdout, "pm-trader --help lists commands")
    with tempfile.TemporaryDirectory() as d:
        _strategy_from_user_dir(d)
        asyncio.run(_stdio(os.path.join(d, "stdio")))
        asyncio.run(_http(os.path.join(d, "http")))
    print("OUTSIDER SMOKE PASSED")


if __name__ == "__main__":
    main()
