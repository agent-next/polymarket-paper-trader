# Remote MCP: authenticated multi-user mode

Spec for the opt-in remote-MCP mode of `pm-trader-mcp`. Written before the code.

## Goal

Make `pm-trader-mcp` servable as a multi-user remote MCP endpoint, so a hosted
deployment could be added by URL in ChatGPT, Claude.ai and other remote-MCP
hosts. A remote host completes an OAuth flow against an *external* issuer, then
calls the tools with `Authorization: Bearer <access token>`; every verified
token subject gets its own paper accounts.

## Non-goals

- **No hosting or deployment.** No container image, no ingress, no deploy
  workflow. Operators run the process themselves.
- **No authorization server.** pm-trader is a *resource server*: it validates
  bearer tokens minted by an external OAuth 2.1 / OIDC issuer and never issues,
  refreshes or revokes one.
- **No change to the default.** stdio and the unauthenticated loopback
  streamable-HTTP behaviour are byte-identical; every flag below is opt-in.
- **No local-only tools over HTTP.** `backtest` and `pk_battle` read local files
  and execute local strategy modules; they stay removed on every network
  transport, authenticated or not.
- No token issuance or token revocation. Abuse limits are in "Abuse limits" below.

## Threat model

The asset is each user's paper account data under `~/.pm-trader/`. Every dollar
is simulated, but account state is still the user's own; "trading" means
resetting and resolving another user's positions.

| Threat | Control |
|---|---|
| Anonymous caller reaching a public bind | Bearer required on every HTTP request; missing/invalid token -> `401` + `WWW-Authenticate` (RFC 9728 §5.1) |
| Forged / replayed / expired token | RS/ES/PS signature verified against the issuer's JWKS; `iss`, `aud`, `exp` and `sub` required |
| `alg: none` or HS-signed token forged with the public key | Only asymmetric `RS*`/`PS*`/`ES*` algorithms are accepted; anything else is rejected before key lookup |
| Token minted for another resource (audience confusion) | `aud` must equal `--auth-audience` |
| Cross-user reads/writes (user A passing user B's `account`) | Per-user root directory derived from `sha256(issuer, sub)`; `account` is only a sub-account inside it |
| Path traversal through `sub` or `account` | `sub` never appears in a path (hash only); `account` still goes through `_validate_account_name()` |
| Remote caller enumerating local accounts | `leaderboard_card` scans the *session's* root, not `~/.pm-trader/` |
| Discovery: client cannot find the issuer | Protected Resource Metadata (RFC 9728) at the well-known path, `authorization_servers` set |
| Reading local files / executing local code over the network | `backtest`/`pk_battle` removed on HTTP transports |

Out of scope: transport encryption (terminate TLS at the edge), issuer-side
policy, host compromise, denial of service. Local-only mode keeps today's
`UNAUTHENTICATED` warning on a non-loopback bind; remote mode does not print it
because the transport is authenticated.

## Interface

```bash
pm-trader-mcp --transport streamable-http --host 0.0.0.0 --port 8000 \
  --auth-issuer https://login.example.com \
  --auth-audience https://pm.example.com/mcp \
  --resource-url https://pm.example.com/mcp
```

Environment equivalents (flag wins): `PM_TRADER_AUTH_ISSUER`,
`PM_TRADER_AUTH_AUDIENCE`, `PM_TRADER_RESOURCE_URL`. The three are all-or-none;
supplying one without the others, or supplying any of them on stdio, is a
startup error. `--auth-issuer` must be `https://` (`http://` is accepted only
for `localhost`/`127.0.0.1`, so tests and local IdPs work).

No extra install: the MCP SDK already depends on `PyJWT[crypto]`, which brings
the RSA/EC (`cryptography`) backend used to verify issuer signatures.

### Abuse limits

Authenticated remote mode only; stdio and unauthenticated HTTP are never
limited. Each limit is per verified subject (keyed by its `user_root` digest).

| Limit | Default | Flag | Env | Error code |
|---|---|---|---|---|
| Tool calls per minute (in-process sliding window, never sleeps) | 60 | `--rate-limit-per-minute` | `PM_TRADER_RATE_LIMIT_PER_MINUTE` | `RATE_LIMITED` |
| Distinct accounts per subject (existing accounts keep working) | 20 | `--max-accounts` | `PM_TRADER_MAX_ACCOUNTS` | `ACCOUNT_LIMIT` |
| Retention, in-server: idle subject directories pruned at startup and every 24 h (0 disables) | 90 days | `--prune-idle-days` | `PM_TRADER_PRUNE_IDLE_DAYS` | |
| Retention, offline: `pm-trader-mcp prune-remote --idle-days N [--dry-run]` | 90 days | | | |

Both limits must be at least 1. The rate-limit window lives in process memory,
so a restart clears it.

A remote server holds an exclusive `flock` on `~/.pm-trader/remote/.server.lock`
for its whole lifetime; a second remote server on the same root refuses to
start. The in-server prune runs on the same single worker thread that serializes
tool calls, so it never interleaves with one, and it closes the cached engine
first when that engine lives in a directory it is about to delete.

`prune-remote` takes the same lock without blocking. While a server is running
it exits 1 with `server running; use the in-server prune` and deletes nothing
(deleting an open SQLite database would silently lose writes). Otherwise it
deletes every `~/.pm-trader/remote/<digest>/` whose newest file is older than
`--idle-days`, prints each directory it removed (or `would remove` with
`--dry-run`), and touches nothing else: only real hashed directories directly
under the remote users root qualify, symlinks are skipped, and `prune_remote()`
raises for any other root.

`ACCOUNT_LIMIT` and `RATE_LIMITED` are never relabelled by a tool's own error
code (for example `get_balance` on a new name at the cap).

### Token validation

1. Read the unverified header; reject unless `alg` is in `RS256 RS384 RS512
   PS256 PS384 PS512 ES256 ES384 ES512`.
2. Fetch `{issuer}/.well-known/openid-configuration`, read `jwks_uri`, fetch the
   JWKS; keys are cached in-process for 5 minutes and refreshed after that. A
   discovery/JWKS failure keeps the last good key set for at most one hour (none
   on a cold start, so every token is a `401`) and the issuer is not asked again
   for 30 seconds; it never falls back to "no signature check".
3. Select the key by `kid`; decode with `issuer`, `audience` and
   `require=["exp","iss","aud","sub"]`.
4. Any failure returns `401` with `WWW-Authenticate: Bearer error="invalid_token"`.

### Protected resource metadata

`GET https://pm.example.com/.well-known/oauth-protected-resource/mcp` (the
well-known prefix is inserted before the resource path, RFC 9728 §3.1) returns:

```json
{"resource": "https://pm.example.com/mcp",
 "authorization_servers": ["https://login.example.com"],
 "bearer_methods_supported": ["header"]}
```

### Per-user data directories

```
~/.pm-trader/remote/<sha256(issuer + "\x1f" + sub)[:32]>/<account>/paper.db
```

The directory name is a hex digest, so no part of `sub` reaches the filesystem.
One Engine is cached per data directory and tool calls stay serialized on the
single MCP worker thread, so two users never share a SQLite handle.

## Acceptance oracle

Offline, no network: the tests generate an RSA keypair, sign tokens with it and
serve the JWKS through a stub.

```bash
python3 -m pytest tests/test_remote_auth.py -q
```

- valid token -> `tools/call` succeeds and the database appears under that
  subject's hash directory;
- expired / wrong `aud` / wrong `iss` / bad signature / `alg: none` / `HS256` /
  missing token -> `401` + `WWW-Authenticate`;
- two subjects using the *same* `account` name land in different directories and
  cannot see each other's trades;
- `account="../escape"` stays rejected;
- metadata endpoint returns the three fields above;
- `pm-trader-mcp --help` lists the three flags; the
  `UNAUTHENTICATED` warning still prints on a non-loopback bind without
  `--auth-issuer` and is absent with it;
- full gate: `make check` (100% coverage for the root package).