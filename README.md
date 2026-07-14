# Prediction Markets Weekly Tracker — Kalshi vs Polymarket

Portfolio-monitoring tool for the team. Every Monday morning it pulls prediction-market
volume from Dune, compares Kalshi against Polymarket, and posts a digest to Slack.

## What it reports
- **Volume** (USD notional): Kalshi vs Polymarket, trailing 7 days, with week-over-week change
- **Kalshi share** of the two-venue total (current vs prior week)
- **Activity**: active markets + open interest (Kalshi); active markets + trade count (Polymarket)
- **Kalshi category leaders** (Sports, Exotics, Crypto, …)
- **Top markets** on each venue for the week

## How it works
`fetch_and_report.py` executes four saved Dune queries via the Dune REST API, computes the
comparison, formats a Slack `mrkdwn` message, and POSTs it to the Slack Log Relay.

- `dune_client.py` — minimal Dune API client (stdlib only, with retry for free-tier rate limits)
- `config/secrets.env` — Dune API key + Slack relay URL/auth/channel (keep private)
- `config/queries.json` — the four Dune query IDs
- `queries/draft_queries.sql` — the SQL, for reference

### Dune queries (public, on your Dune account)
| Purpose | Query ID |
|---|---|
| Polymarket weekly volume & activity | 7971842 |
| Kalshi weekly volume & category mix | 7971846 |
| Polymarket top markets | 7971861 |
| Kalshi top markets | 7971862 |

## Run manually
```
cd kalshi-tracker
python3 fetch_and_report.py --dry-run   # fetch + print, do not post
python3 fetch_and_report.py             # fetch + post to Slack
```

## Schedule / hosting
Runs automatically **every Monday**. Two ways to host it:
- **GitHub Actions (cloud, recommended)** — runs on GitHub's servers, independent of your
  desktop. See `SETUP_GITHUB.md` for the one-time setup. Credentials come from repo secrets.
- **Cowork scheduled task** (`prediction-markets-weekly`) — runs while the desktop app is open.
  Use as a fallback; disable it once GitHub Actions is confirmed to avoid double-posting.

Config resolves credentials from environment variables first (GitHub Secrets), then falls back
to `config/secrets.env` for local runs.

## Kalshi-direct modules (Kalshi's own public API, no auth)
Two modules pull straight from Kalshi for signals Dune can't provide:

- `kalshi_direct.py` — **flow pulse** over a bounded recent window: taker $ volume,
  block-trade (institutional) share, yes/no flow direction, avg/median trade size,
  and top markets with real titles. Windowed because a full week is millions of trades.
- `kalshi_snapshot.py` — **weekly catalog snapshot**: pages the full `/markets` catalog
  (~400k active markets), drops zero-volume markets, aggregates to the event level, and
  computes trailing-24h volume, open interest, category mix (via `/series`), event
  concentration, and **true weekly volume** by diffing this week's lifetime volume against
  last week's snapshot. Runs to completion on the GitHub runner (`kalshi-snapshot.yml`,
  Mondays 13:00 UTC); state persists via Actions cache, not git.

Note: Kalshi trades are anonymous, so **account concentration is not derivable** from the
API — that requires Kalshi-internal data (request from the company).

## Important caveats
- **Kalshi volume is a reported floor.** The free `kalshi.market_report` table only covers the
  markets Kalshi publishes in its CFTC reports — not necessarily full platform volume. Kalshi's
  real share may be higher than shown.
- **Fees & resolution economics** (true "performance") require Dune Enterprise. This build tracks
  volume, share, activity, category mix, and top markets on the free tier.
- Kalshi and Polymarket are the only two venues in the Dune collection, so "competitors" = Polymarket.

## Upgrade path (if you get Dune Enterprise)
Swap the free tables for the unified `prediction_markets.*` schema for apples-to-apples cross-venue
volume, add Kalshi per-fill fee data, and add resolution-outcome accuracy metrics.

# Kalshi-Weekly-Update
