# Runs the pm-trader MCP server over streamable-http for remote MCP clients
# (Cursor, Claude.ai connectors, Grok API remote MCP, ChatGPT apps).
#
# Single-tenant: the container holds ONE paper-trading account (SQLite at
# ~/.pm-trader/default/paper.db inside the container). Self-host only — do
# not expose a shared container to multiple untrusted users; they would all
# trade the same paper account. Mount a volume at /root/.pm-trader to persist
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
