# Kalshi: spec and read-only market data

What the Kalshi side of a second market would look like, and the public read
endpoints verified for it. This page is the contract; `pm_trader/kalshi_api.py`
implements exactly the endpoints listed here and nothing else.

Polymarket facts live in [agent-reference.md](agent-reference.md) ("Verified
upstream contract facts"). Same discipline applies here: the wire wins over the
docs, and every claim below was probed live on 2026-10-14.

## Goal

Let the paper engine read Kalshi market data — list markets, fetch one market,
fetch its order book — and return the existing `Market` / `OrderBook` models, so
a later change can point the same fill simulation at a Kalshi book.

## Non-goals

- **No trading.** No order placement, cancellation, amendment, portfolio or
  balance calls. No API keys, no request signing, no account.
- **No CLI or MCP surface.** Nothing in `cli.py` / `mcp_server.py` changes; the
  client is importable only.
- **No Kalshi fees, settlement or resolution logic** in the engine. The data a
  later change needs is captured here (see Fees / Settlement), not implemented.
- **No scalar (`market_type: "scalar"`) markets.** The engine is binary YES/NO;
  the client parses scalar payloads but they must not be traded.
- **No WebSocket feed, no historical/candlestick data, no FX or funding.**

## Environments and base URL

| Environment | Base URL | Status |
|-------------|----------|--------|
| Production (recommended for API traders) | `https://external-api.kalshi.com/trade-api/v2` | verified live |
| Production (shared host, also supported) | `https://api.elections.kalshi.com/trade-api/v2` | verified live, identical payloads |
| Demo | `https://external-api.demo.kalshi.co/trade-api/v2` | `(unverified)` — not probed |

Source: <https://docs.kalshi.com/getting_started/api_environments>. Despite the
`elections` subdomain the production Trade API serves all Kalshi markets. The
client uses the recommended `external-api` host.

## Endpoints used

All three are public and unauthenticated (`security: []` in the OpenAPI spec).

| Call | Path | Parameters | Envelope |
|------|------|------------|----------|
| List markets | `GET /markets` | `limit` (0–1000, default 100), `cursor`, `status` (`unopened\|open\|paused\|closed\|settled`), `event_ticker`, `series_ticker`, `tickers` (comma-separated) | `{"cursor": str, "markets": [...]}` |
| One market | `GET /markets/{ticker}` | — | `{"market": {...}}` |
| Order book | `GET /markets/{ticker}/orderbook` | `depth` (0 or negative = all levels, 1–100) | `{"orderbook_fp": {"yes_dollars": [[price, count], ...], "no_dollars": [[price, count], ...]}}` |

Sources: <https://docs.kalshi.com/api-reference/market/get-markets>,
<https://docs.kalshi.com/api-reference/market/get-market>,
<https://docs.kalshi.com/api-reference/market/get-market-orderbook>,
<https://docs.kalshi.com/getting_started/pagination>.

### Live probes (trimmed)

`BASE` below is `https://external-api.kalshi.com/trade-api/v2`.

```
$ curl -s "BASE/markets?limit=2&status=open&series_ticker=KXNBAGAME" | jq -c ...
{"cursor":"CgwIhMem1gYQyKaDgAISG0tYTkJBR0FNRS0yNk9DVDE2REVOTEFMLURFTg","tickers":["KXNBAGAME-26OCT16DENLAL-LAL","KXNBAGAME-26OCT16DENLAL-DEN"]}

$ curl -s --get --data-urlencode "cursor=<page-1 cursor>" --data "limit=2&status=open&series_ticker=KXNBAGAME" "BASE/markets" | jq -c ...
{"cursor":"CgwI-rim1gYQgKCBhwESG0tYTkJBR0FNRS0yNk9DVDE2VVRBUEhYLVBIWA","tickers":["KXNBAGAME-26OCT16UTAPHX-UTA","KXNBAGAME-26OCT16UTAPHX-PHX"]}

$ curl -s "BASE/markets/KXNBAGAME-26OCT16DENLAL-LAL" | jq -c ...
{"ticker":"KXNBAGAME-26OCT16DENLAL-LAL","title":"Los Angeles L wins","status":"active","market_type":"binary","yes_bid_dollars":"0.5800","yes_ask_dollars":"0.6000","no_bid_dollars":"0.4000","last_price_dollars":"0.5900","volume_fp":"304.24","open_interest_fp":"253.20","price_level_structure":"linear_cent","price_ranges":[{"end":"1.0000","start":"0.0000","step":"0.0100"}],"result":"","close_time":"2026-10-19T02:30:00Z"}

$ curl -s "BASE/markets/KXNBAGAME-26OCT16DENLAL-LAL/orderbook?depth=2" | jq -c .
{"orderbook_fp":{"no_dollars":[["0.3800","470.52"],["0.4000","521.00"]],"yes_dollars":[["0.5700","482.00"],["0.5800","21.00"]]}}

$ curl -s -w "\n%{http_code}" "BASE/markets/NOPE"
{"error":{"code":"not_found","message":"not found"}}
404

$ curl -s -w "\n%{http_code}" "BASE/markets?limit=1&status=bogus"
{"error":{"code":"bad_request","message":"bad request","details":"invalid status filter"}}
400

$ curl -s "BASE/markets?limit=2&status=open&event_ticker=KXNOTAREALEVENT" | jq -c .
{"cursor":"","markets":[]}

$ for L in 1 200 1000 0; do ... done      # markets per page
limit=1 -> 1 markets
limit=200 -> 200 markets
limit=1000 -> 1000 markets
limit=0 -> 100 markets
```

Full responses (the ones the tests replay) are recorded by
`tests/fixtures/capture_kalshi_fixtures.py`; re-record before trusting them:

```
$ python3 tests/fixtures/capture_kalshi_fixtures.py
wrote kalshi_markets_page1.json (5115 bytes)
wrote kalshi_markets_page2.json (5061 bytes)
wrote kalshi_markets_empty.json (36 bytes)
wrote kalshi_market.json (2432 bytes)
wrote kalshi_orderbook.json (287 bytes)
ticker: KXNBAGAME-26OCT16DENLAL-LAL
```

### Error shape

`{"error": {"code": ..., "message": ..., "details"?: ...}}` with HTTP 400
(`bad_request`, `details` says which filter) and 404 (`not_found`).
`pm_trader/kalshi_api.py` maps 404 on a single-market lookup to
`MarketNotFoundError` and every other HTTP/transport failure to `ApiError`
(carrying `status_code`), exactly like `api.py`.

## Price units and the price grid

- **Prices are dollars as fixed-point strings**, not cents: `"0.5800"` means
  $0.58, and 50 cents is `"0.5000"`. The older integer-cent fields
  (`yes_bid`, `no_bid`, `last_price`) are **gone from the wire** — every price
  is a `*_dollars` string. Order book levels are `[dollars_string, fp]`.
- **Contract quantities are `*_fp` fixed-point strings** with 2 decimals
  (`"304.24"` contracts, 0.01 granularity), also replacing the old integer
  `volume` / `open_interest` fields.
- **`price_ranges` is the source of truth for valid prices** — an array of
  `{start, end, step}` bands in dollars. `price_level_structure` is a
  human-readable label (`linear_cent`, `deci_cent`, `tapered_deci_cent`,
  `center_{whole|half|quint|deci}_edge_{...}_cent`) and the docs explicitly say
  *not* to key pricing logic off the name. Structures range from whole-cent
  grids (`linear_cent`, step `0.0100`) to sub-cent ones
  (`center_deci_edge_centi_cent`: step `0.0001` below $0.01 and above $0.99,
  `0.0010` in between — verified live on a multivariate market).

Sources: <https://docs.kalshi.com/getting_started/fixed_point_migration>,
<https://docs.kalshi.com/api-reference/market/get-markets>.

The engine's `Market.tick_size` is a single number, so the client uses the
**finest step across a market's `price_ranges`**. That is safe for validation
(off-grid prices are rejected by the exchange and by the engine) but is *not*
sufficient for placing an order: a later change must snap inside the band that
contains the price, or a $0.0001 tick under $0.10 is rejected on a `linear_cent`
band. Whole-cent prices are valid in every structure.

## Fees

Kalshi's fee is **not** a bps rate. The official Fee Schedule
(<https://kalshi.com/docs/kalshi-fee-schedule.pdf>, linked as "the math behind
the fees" from <https://help.kalshi.com/en/articles/13823805-fees>) states:

```
fees = round up(M x 0.07      x C x P x (1 - P))   # taker, M = multiplier (default 1)
fees = round up(M x 0.0175    x C x P x (1 - P))   # maker, M = 0 unless indicated
P = the price of a contract in dollars (50 cents is 0.5)
C = the number of contracts being traded
```

Verified 2026-10-14 against two Kalshi primary sources:

- Taker: Kalshi's fee schedule as filed with the CFTC
  (<https://www.cftc.gov/sites/default/files/filings/orgrules/22/09/rule091222kexdcm003.pdf>)
  gives `fees = round up(0.07 x C x P x (1-P))` ("rounds to the next cent"), and
  its table matches it (100 contracts at $0.50 cost $1.75; at $0.10, $0.63).
  The same filing lists a 0.035 coefficient for the INX/NASDAQ100 series, which is
  why the per-series `fee_multiplier` below must be read, not assumed.
- Maker and current rounding: the current schedule ("Fee Schedule for July 2026 -
  7.7.26 Update", the PDF above) gives `round up(M x 0.0175 x C x P x (1-P))` and
  rounds up "such that the fee + positionCost is rounded to a centicent". kalshi.com
  answers automated fetches with HTTP 429, so this line is quoted from the search
  index's copy of that PDF. It matches the API's fee-rounding doc
  (<https://docs.kalshi.com/getting_started/fee_rounding>): the model fee is rounded
  up to $0.000001, then the balance change is aligned to $0.0001 (a centicent) for
  direct members or $0.01 for FCM-cleared members via a rounding fee and rebate.

What the API does publish, verified live on `GET /series/{ticker}`:

```
$ curl -s "BASE/series/KXNBAGAME" | jq -c '.series | {ticker, fee_type, fee_multiplier, category}'
{"ticker":"KXNBAGAME","fee_type":"quadratic_with_maker_fees","fee_multiplier":1,"category":"Sports"}
```

- Fee *type* per series: `quadratic`, `quadratic_with_maker_fees`,
  `quadratic_with_combo_maker_fees`, `flat`
  (<https://docs.kalshi.com/api-reference/exchange/get-series-fee-changes>).
- `fee_multiplier` is the `M` above. Per-series overrides are also published via
  `GET /series/fee_changes` and `GET /events/{event_ticker}/fee_changes`.
- Fee shape is quadratic in price and symmetric around $0.50 — structurally the
  same curve as the engine's `calculate_fee_schedule` (`rate x p x (1-p)`), but
  with different rounding (`ceil` to $0.000001, plus a rounding fee and a rebate
  per <https://docs.kalshi.com/getting_started/fee_rounding>) and a non-zero
  maker leg on some series.
- **Therefore the client does not populate `Market.fee_rate_bps` /
  `maker_base_fee_bps` / `taker_base_fee_bps` / `fee_schedule`.** Mapping 0.07
  into `fee_rate_bps` would make the engine charge
  `(bps/10000) x min(p, 1-p) x size` — a flat rate on the wrong side of $0.50.
  A later change needs an explicit Kalshi fee curve (per-series
  `fee_type` + `fee_multiplier` + `ceil` to 1e-6), not a reuse of the bps path.
- The client does not call `/series/{ticker}`; that would be an extra endpoint
  for a fee feature that is out of scope here.

## Settlement and resolution

- A market pays **$1 per winning contract** ($1 for YES, $1 for NO), only net
  positions settle, and settlement timing varies by market type and data-source
  availability (<https://docs.kalshi.com/getting_started/market_settlement>).
- Lifecycle statuses returned on both endpoints
  (<https://docs.kalshi.com/getting_started/market_lifecycle>): `initialized`
  → `active` → `inactive` (paused) → `closed` → `determined` → `disputed` →
  `amended` → `finalized` (terminal). The `status=` **filter** values are a
  different, smaller set: `unopened` = `initialized`, `open` = `active`,
  `paused` = `inactive`, `closed` = past `close_time` and not `finalized`,
  `settled` = `finalized`.
- After determination the payload carries `result` (`yes` | `no` | `scalar` |
  `""`) and `settlement_value_dollars` — the settlement value of the **YES**
  side. Verified over 40 settled markets:

```
$ curl -s "BASE/markets?status=settled&limit=40" | jq -c ...
[{"result":"no","n":32,"settlement_values":["0.0000"]},{"result":"yes","n":8,"settlement_values":["1.0000"]}]
```

- Resolution terms are per-market text, not machine-readable: `rules_primary`
  (plain-language outcome definition) and `rules_secondary` (postponement /
  cancellation clauses), plus `expiration_value`, `expiration_time` and
  `settlement_timer_seconds` (how long after determination settlement waits).
- **There is no `umaResolutionStatus` equivalent.** The Polymarket engine's
  "a closing price is not a resolution" rule maps onto Kalshi's
  `status in {determined, disputed, amended, finalized}` plus a non-empty
  `result`; a market can carry `last_price_dollars: "0.0000"` while still
  trading (a multivariate market with no trades yet, verified live).
- Settlement bounds (`settlement_bounds_type`, `settlement_floor_dollars`) allow
  partial pre-settlement payout on some markets
  (<https://docs.kalshi.com/getting_started/settlement_bounds>) — not modelled
  here.

## Field mapping onto the engine's models

### `Market`

| Kalshi field | Engine field | Note |
|--------------|--------------|------|
| `ticker` | `condition_id`, `slug` | Kalshi has no condition id; the ticker is the only stable key. Positions and trades are keyed on it. |
| `title` | `question` | Kalshi's `title` is the *outcome* ("Los Angeles L wins"), not a question; the question lives on the event (`GET /events/{ticker}`), which is out of scope. Documented compromise. |
| `rules_primary` (+ `rules_secondary` on the event) | `description` | Plain-language resolution criteria. |
| — | `outcomes` | Always `["Yes", "No"]` (binary markets). |
| `settlement_value_dollars` else `last_price_dollars` | `outcome_prices[0]` | Determined markets settle to `1.0000`/`0.0000`, so the engine's `>= 0.99` winner test reads correctly. |
| `1 - outcome_prices[0]` | `outcome_prices[1]` | NO is the complement. |
| — | `tokens` | Both entries carry the ticker (`{"token_id": ticker, "outcome": "Yes"/"No"}`): Kalshi has no per-side token id, so `get_token_id` cannot distinguish sides. A trading change needs side-aware plumbing. |
| `status == "active"` | `active`, `accepting_orders` | Kalshi has no separate "accepting orders" flag. |
| `status in {closed, determined, disputed, amended, finalized}` | `closed` | Pre-open (`initialized`, `inactive`) is neither active nor closed, so the engine's tradability gate rejects it as inactive. |
| `volume_fp` | `volume` | Contract count, not dollars. |
| `close_time` | `end_date` | ISO-8601, same shape as Polymarket's `end_date_iso`. |
| min `step` across `price_ranges` | `tick_size` | See "Price units and the price grid". |
| — | `liquidity` | **No Kalshi field** — stays `0.0`. Market ranking must not sort on it (`open_interest_fp` is a different quantity and is deliberately not substituted). |
| — | `fee_rate_bps`, `maker_base_fee_bps`, `taker_base_fee_bps`, `fee_schedule`, `neg_risk`, `min_order_size`, `uma_resolution_status` | **Do not map.** Fees are a different curve, neg-risk and UMA status have no Kalshi equivalent, and there is no minimum order size. |
| `market_type`, `event_ticker`, `result`, `settlement_value_dollars`, `price_level_structure` | — | **No field to hold them.** Reachable from the raw payload; a resolution feature will need new fields on `Market`. |

### `OrderBook`

The wire carries **bids only** — `yes_dollars` and `no_dollars`, each a ladder
of `[price_dollars, size_fp]` pairs sorted **ascending by price** (the best
level is last). Kalshi is explicit that a bid for yes at price X *is* an ask for
no at `1 - X`, so there is no ask ladder to fetch
(<https://docs.kalshi.com/api-reference/market/get-market-orderbook>).

| Wire | Engine `OrderBook` | Note |
|------|--------------------|------|
| `orderbook_fp.yes_dollars` | `bids` | Reversed to best-first (descending); prices are already dollars. |
| `orderbook_fp.no_dollars` | `asks` | A no bid at `p` is a yes ask at `1 - p`; reversed to ascending. |
| — | — | Only the **yes** side is reconstructed. A Kalshi "NO" position sells into the yes bid ladder, which the engine's sell path already does. |

`depth` is passed through (`None` → omit → the full book). Prices converted from
the no ladder are rounded to 4 decimals — the finest grid any Kalshi market
accepts — so float noise (`1 - 0.39` = `0.6100000000000001`) cannot produce an
off-grid price.

## Client conventions (`pm_trader/kalshi_api.py`)

Same rules as `api.py`, so the two clients read alike:

- `httpx` only (no new runtime dependency), 10 s timeout, one `httpx.Client`.
- Metadata is cached in the shared SQLite `market_cache` table for
  `CACHE_TTL_SECONDS = 300` (imported from `api.py`, so the TTL cannot drift);
  cache keys are namespaced `kalshi:market:<ticker>` and
  `kalshi:markets:<filters+limit>`, so they can never collide with a Polymarket
  key. Order books are **never** cached.
- An empty result is **never** cached (`if collected:` guard before
  `_set_cached`), so a miss is re-queried instead of pinned for 5 minutes.
- `MAX_RESULTS = 100` caps `list_markets`, page size 100, `limit <= 0` → no
  request at all. Cursors are followed until the cap, an empty `cursor`
  (Kalshi sends `""`, not `null`), or a cursor that repeats — the wire answers
  an unusable cursor with page 1, so the replay guard is what stops the loop.
- `_parse_market` / `_parse_order_book` raise `ApiError` on a payload that is
  not a market (`ticker` missing), not an order book (`orderbook_fp` missing or
  a missing ladder), or carries a malformed level: silent defaults would key a
  position on an empty string or fill against a garbage book.
- No engine, CLI or MCP wiring; the client is constructed with a `Database` and
  `close()`d, like `PolymarketClient`.

## Verified wire-vs-doc facts (2026-10-14)

- The `Market` schema in the docs has no `series_ticker` field, and the wire has
  none either: `series_ticker` is a **filter only**. Same for the removed
  `subtitle`.
- Pagination docs say "continue until the cursor is `null`"; the wire terminates
  with `cursor: ""` (verified on a two-market event).
- A **malformed cursor is not an error**: `cursor=not-a-cursor` returns page 1
  with HTTP 200, so a paging loop can silently restart instead of failing.
- `limit` is 0–1000 (docs and wire agree); `limit=0` is not "no results", it is
  the default page size (100).
- An unknown `event_ticker` / `series_ticker` returns **HTTP 200 with
  `{"cursor": "", "markets": []}`**, not 404. Only `GET /markets/{ticker}` 404s.
- `orderbook_fp.yes_dollars` / `no_dollars` pairs are `[dollars, contracts]` —
  the second element is a **quantity**, not a price (the OpenAPI description is
  easy to misread).
- The order book is documented as "organized from best to worst prices" but the
  wire sends each ladder **ascending**; the best level is the last element.
- `status=bogus` → 400 with `details: "invalid status filter"`, so a bad filter
  is detectable instead of silently ignored.
- `settlement_value_dollars` is the YES side (1.0000/0.0000 for binary), and it
  is absent until determination — `null`, not `"0.0000"`.
- `open_interest_fp` is "contracts bought, disconsidering netting", not open
  interest in dollars.

## Open questions

- `(unverified)` Whether `market_type: "scalar"` markets ever need to reach an
  agent. Parsing works; resolution does not (the engine assumes a binary
  YES/NO payout).
- `(unverified)` Whether Kalshi's `exchange_index` sharding (markets now arrive
  with `exchange_index: 1`/`3`) affects a public reader. Both hosts answered
  every probe here, so the client ignores the field; confirm if a later change
  adds writes.
- A Kalshi "event" (the question, the strike, the full rules text) needs
  `GET /events/{event_ticker}`. The `question` field will stay a compromise
  until that endpoint is added.
- Whether the later trading change should simulate Kalshi maker fees
  (`fee_type: quadratic_with_maker_fees` on some series) or only taker fees.
- Multi-leg (multivariate, `mve_collection_ticker`) markets parse into a single
  binary `Market` today; their `custom_strike` / `mve_selected_legs` payload has
  no engine representation.
