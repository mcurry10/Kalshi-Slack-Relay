"""
Kalshi investor pulse -> Anthos Slack Log Relay.

Reports, from Kalshi's PUBLIC market-data API (no auth):
  - Platform pulse: open events/markets, open interest, 24h volume, top categories.
  - WoW / MoM volume: exact contract volume over the trailing ~7d and ~30d,
    computed by diffing this run's lifetime volume against stored snapshots.
  - Block trades: institutional block-trade share of taker $ volume, sampled
    over a bounded recent window.
Optionally appends full-history parity metrics pulled from the public Kalshi
Dune dashboard (dune.com/kalshi/kalshi) when DUNE_API_KEY is set.

SAFETY / DESIGN:
  - Relay token read from env SLACK_RELAY_TOKEN; never hardcoded. No token -> refuse to send.
  - Dry run by default; actual POST only with `--send`.
  - Destination channel is a required CLI arg.

Usage:
  python3 slack_relay.py --channel C0XXXXXXX                       # dry run
  SLACK_RELAY_TOKEN=... python3 slack_relay.py --channel C0XXXXXXX --send
"""
import os, sys, json, time, argparse, requests
from collections import defaultdict
from datetime import datetime, timezone

RELAY_URL = "https://647891eb-2a47-4a3b-ac32-bb8df7ee4b8c.trayapp.io"
BASE = "https://api.elections.kalshi.com/trade-api/v2"
DEFAULT_HISTORY = "config/kalshi_snapshot_history.json"

S = requests.Session()
S.headers.update({"Accept": "application/json"})


# ---------- platform snapshot (all open events) ----------
def pull_platform(max_pages=400):
    cursor, events = None, []
    for _ in range(max_pages):
        params = {"limit": 200, "status": "open", "with_nested_markets": "true"}
        if cursor:
            params["cursor"] = cursor
        d = S.get(f"{BASE}/events", params=params, timeout=30).json()
        batch = d.get("events", [])
        events.extend(batch)
        cursor = d.get("cursor")
        if not cursor or not batch:
            break
        time.sleep(0.05)
    cats = defaultdict(lambda: {"vol24": 0.0, "oi": 0.0})
    tot_vol = vol24 = oi = 0.0
    n_markets = 0
    for ev in events:
        cat = ev.get("category") or "Uncategorized"
        for m in ev.get("markets") or []:
            n_markets += 1
            v24 = float(m.get("volume_24h_fp") or 0)
            oim = float(m.get("open_interest_fp") or 0)
            tot_vol += float(m.get("volume_fp") or 0)   # lifetime cumulative -> used for diffs
            vol24 += v24
            oi += oim
            cats[cat]["vol24"] += v24
            cats[cat]["oi"] += oim
    return {
        "ts": int(time.time()),
        "iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_vol": tot_vol, "vol24": vol24, "oi": oi,
        "n_events": len(events), "n_markets": n_markets,
    }, cats


# ---------- block-trade share (bounded recent sample) ----------
def sample_block_share(max_pages=120):
    trades, cursor = [], None
    for _ in range(max_pages):
        params = {"limit": 1000}
        if cursor:
            params["cursor"] = cursor
        d = S.get(f"{BASE}/markets/trades", params=params, timeout=30).json()
        batch = d.get("trades", [])
        trades.extend(batch)
        cursor = d.get("cursor")
        if not cursor or not batch:
            break
    if not trades:
        return None
    total_usd = block_usd = 0.0
    for t in trades:
        price = float(t["yes_price_dollars"] if t["taker_side"] == "yes" else t["no_price_dollars"])
        usd = float(t["count_fp"]) * price
        total_usd += usd
        if t.get("is_block_trade"):
            block_usd += usd
    times = sorted(t["created_time"] for t in trades)
    span_min = (datetime.fromisoformat(times[-1].replace("Z", "+00:00"))
                - datetime.fromisoformat(times[0].replace("Z", "+00:00"))).total_seconds() / 60
    return {
        "n_trades": len(trades), "taker_usd": total_usd,
        "block_share": (block_usd / total_usd) if total_usd else 0.0,
        "span_min": span_min,
    }


# ---------- snapshot history (WoW / MoM) ----------
def load_history(path):
    try:
        with open(path) as f:
            return json.load(f).get("snapshots", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_history(path, snaps):
    cutoff = int(time.time()) - 75 * 86400  # keep ~75 days
    snaps = [s for s in snaps if s["ts"] >= cutoff]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump({"snapshots": snaps}, f)


def nearest(snaps, now_ts, target_days, min_days, max_days):
    lo, hi = now_ts - max_days * 86400, now_ts - min_days * 86400
    cands = [s for s in snaps if lo <= s["ts"] <= hi]
    if not cands:
        return None
    tgt = now_ts - target_days * 86400
    return min(cands, key=lambda s: abs(s["ts"] - tgt))


def volume_windows(cur, snaps):
    now = cur["ts"]
    s7 = nearest(snaps, now, 7, 5, 10)
    s14 = nearest(snaps, now, 14, 12, 18)
    s30 = nearest(snaps, now, 30, 25, 38)
    s60 = nearest(snaps, now, 60, 52, 70)
    out = {"w7": None, "w30": None, "wow": None, "mom": None}
    if s7:
        out["w7"] = cur["total_vol"] - s7["total_vol"]
        if s14:
            prior = s7["total_vol"] - s14["total_vol"]
            if prior > 0:
                out["wow"] = 100 * (out["w7"] / prior - 1)
    if s30:
        out["w30"] = cur["total_vol"] - s30["total_vol"]
        if s60:
            prior = s30["total_vol"] - s60["total_vol"]
            if prior > 0:
                out["mom"] = 100 * (out["w30"] / prior - 1)
    return out


# ---------- message ----------
def build_message(cur, cats, win, block, dune_lines=None):
    tot24 = cur["vol24"] or 1
    tot_oi = cur["oi"] or 1
    top = sorted(cats.items(), key=lambda kv: -kv[1]["vol24"])[:5]
    L = [
        f"*Kalshi platform pulse*  —  {cur['n_events']:,} open events / {cur['n_markets']:,} markets",
        f"Open interest: *{cur['oi']:,.0f}* contracts   |   24h volume: *{cur['vol24']:,.0f}* contracts",
        "",
        "*Volume traded (contracts):*",
    ]
    def _fmt(vol, pct, label):
        if vol is None:
            return f"  • {label}: _building history…_"
        chg = f"  ({pct:+.1f}% vs prior period)" if pct is not None else "  (baseline)"
        return f"  • {label}: *{vol:,.0f}*{chg}"
    L.append(_fmt(win["w7"], win["wow"], "Trailing 7d (WoW)"))
    L.append(_fmt(win["w30"], win["mom"], "Trailing 30d (MoM)"))
    L.append("")
    if block:
        L.append(
            f"*Block trades (institutional):* *{block['block_share']*100:.1f}%* of taker $ volume  "
            f"(_sample: {block['n_trades']:,} trades over ~{block['span_min']:.0f} min, "
            f"${block['taker_usd']/1e6:.1f}M_)"
        )
    else:
        L.append("*Block trades:* _no recent trade sample available_")
    L += ["", "*Top categories by 24h volume:*"]
    for cat, a in top:
        L.append(f"  • {cat}: {100*a['vol24']/tot24:.1f}% of vol  (_{100*a['oi']/tot_oi:.1f}% of open interest_)")
    if dune_lines:
        L += [""] + dune_lines
    L += [
        "",
        "_Volume is contract count (notional double-counts both sides; not fee revenue). "
        "WoW/MoM via snapshot diff — periods fill in as history accrues. "
        "Block share is a bounded recent sample, not a full-week figure._",
    ]
    return "\n".join(L)


def make_payload(message, channel, reporting_app):
    return {
        "message": message, "channelID": channel, "reportingApp": reporting_app,
        "headline": "Kalshi Investor Pulse", "icon_emoji": ":chart_with_upwards_trend:",
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--channel", required=True, help="Slack channel ID (or user ID for a DM)")
    p.add_argument("--reporting-app", default="Kalshi Investor Pulse")
    p.add_argument("--history", default=DEFAULT_HISTORY, help="Snapshot history JSON path")
    p.add_argument("--block-pages", type=int, default=120,
                   help="Max 1000-trade pages to sample for block share (0 to skip)")
    p.add_argument("--dune", action="store_true",
                   help="Append Dune full-history metrics (needs DUNE_API_KEY). "
                        "Auto-enabled when DUNE_API_KEY is set.")
    p.add_argument("--send", action="store_true", help="Actually POST. Omit for dry run.")
    args = p.parse_args()

    cur, cats = pull_platform()
    snaps = load_history(args.history)
    win = volume_windows(cur, snaps)
    block = sample_block_share(args.block_pages) if args.block_pages > 0 else None

    dune_lines = None
    if args.dune or os.environ.get("DUNE_API_KEY"):
        try:
            import dune_client
            dune_lines = dune_client.build_dune_lines()
        except Exception as e:
            print(f"[dune] skipped: {e}")

    # persist this run's snapshot for future WoW/MoM diffs
    snaps.append({k: cur[k] for k in ("ts", "iso", "total_vol", "vol24", "oi")})
    save_history(args.history, snaps)

    msg = build_message(cur, cats, win, block, dune_lines)
    payload = make_payload(msg, args.channel, args.reporting_app)

    print("=== RENDERED MESSAGE PREVIEW ===")
    print(msg)
    print(f"\n[history] {len(snaps)} snapshot(s) on file at {args.history}")

    if not args.send:
        print("\n[dry run] Nothing sent. Re-run with --send (and SLACK_RELAY_TOKEN set) to post.")
        return

    token = os.environ.get("SLACK_RELAY_TOKEN")
    if not token:
        sys.exit("\nERROR: --send given but SLACK_RELAY_TOKEN is not set. Refusing to send.")
    r = requests.post(RELAY_URL, headers={"Authorization": token, "Content-Type": "application/json"},
                      json=payload, timeout=30)
    print(f"\nPOST status: {r.status_code}  (relay fails silently on bad payload — verify in Slack)")


if __name__ == "__main__":
    main()
