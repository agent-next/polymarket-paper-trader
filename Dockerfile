# Runs the pm-trader MCP server over streamable-http for remote MCP clients
# (Cursor, Claude.ai connectors, Grok API remote MCP, ChatGPT apps).
#
# No isolation between callers: everyone who reaches the container shares
# its paper accounts (SQLite under ~/.pm-trader/ inside the container).
# Self-host only — do not expose it to multiple untrusted users. Mount a volume at /root/.pm-trader to persist
# the account across restarts.
#
# No authentication: the streamable-http endpoint has none. Publish the port
# to 127.0.0.1 only (`docker run -p 127.0.0.1:8000:8000 ...`) unless a real
# authenticating proxy sits in front. `backtest`/`pk_battle` (local file
# reads + local strategy-module execution) are stdio-only and not served
# here.
FROM python:3.12-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["pm-trader-mcp", "--transport", "streamable-http", "--host", "0.0.0.0"]
