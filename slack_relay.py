"""
Kalshi Weekly Market Digest -> Anthos Slack Log Relay.

Built from Kalshi's PUBLIC market-data API (no auth):
  - Market activity: open interest and 24-hour volume, in notional dollars and
    contracts, with WoW / MoM changes computed from stored weekly snapshots
    (config/kalshi_snapshot_history.json). These are point-in-time comparisons
    (this Monday vs prior Mondays), which stay valid as markets settle.
  - Trade size: exchange-lifetime average and median from the public Kalshi
    Dune dashboard (requires DUNE_API_KEY), trended WoW / MoM from our stored
    snapshots since the public queries expose no time series.
  - Institutional activity: share of dollar volume from trades >= $1,000,
    read from accumulated samples in config/kalshi_trade_flow.json
    (populated every few hours by trade_collector.py).
  - Category mix and most-active-market tables, with series tickers resolved
    to human-readable titles via the Kalshi series endpoint.

Formatting follows balance-sheet conventions: negative values in parentheses.
Dry run by default; POST only with --send. Channel is a required arg.
"""
import os, sys, json, time, argparse, requests
from collections import defaultdict
from datetime import datetime, timezone

RELAY_URL = "https://647891eb-2a47-4a3b-ac32-bb8df7ee4b8c.trayapp.io"
BASE = "https://api.elections.kalshi.com/trade-api/v2"
DEFAULT_HISTORY = "config/kalshi_snapshot_history.json"
DEFAULT_FLOW = "config/kalshi_trade_flow.json"

S = requests.Session()
S.headers.update({"Accept": "application/json"})


# ---- live platform pull ----
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
            tot_vol += float(m.get("volume_fp") or 0)
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


def series_title(ticker, _cache={}):
    """Human-readable title for a Kalshi series ticker; falls back to the ticker."""
    t = str(ticker or "").strip()
    if not t or " " in t:
        return t
    if t in _cache:
        return _cache[t]
    title = t
    try:
        d = S.get(f"{BASE}/series/{t}", timeout=15).json()
        title = (d.get("series") or {}).get("title") or t
    except Exception:
        pass
    _cache[t] = title
    return title


# ---- institutional flow (accumulated by trade_collector.py) ----
def load_flow(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"days": {}}


def flow_windows(flow, windows=(7, 30)):
    days = flow.get("days", {})
    today = datetime.now(timezone.utc).date()
    out = {}
    for w in windows:
        tot = lrg = smin = 0.0
        nd = 0
        for dstr, d in days.items():
            try:
                dd = datetime.fromisoformat(dstr).date()
            except ValueError:
                continue
            if 0 <= (today - dd).days < w:
                tot += d["total_usd"]; lrg += d["large_usd"]
                smin += d.get("sample_min", 0.0); nd += 1
        out[w] = {"total": tot, "n_days": nd, "sample_min": smin,
                  "large_share": (lrg / tot if tot else None)}
    return out


# ---- snapshot history (WoW / MoM) ----
def load_history(path):
    try:
        with open(path) as f:
            return json.load(f).get("snapshots", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_history(path, snaps):
    cutoff = int(time.time()) - 75 * 86400
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


def pct_change(cur_v, prev_v):
    try:
        if cur_v is None or prev_v is None or float(prev_v) == 0.0:
            return None
        return 100.0 * (float(cur_v) / float(prev_v) - 1.0)
    except (TypeError, ValueError):
        return None


def snapshot_changes(cur, snaps, keys):
    """Point-in-time WoW / MoM per key, vs snapshots ~7 and ~30 days back.
    Each key is matched only against snapshots that recorded that key, so
    metrics added later (e.g. trade size) trend as soon as they have history."""
    now = cur["ts"]
    out = {}
    for k in keys:
        have = [s for s in snaps if s.get(k) is not None]
        s7 = nearest(have, now, 7, 5, 10)
        s30 = nearest(have, now, 30, 25, 38)
        out[k] = {"wow": pct_change(cur.get(k), s7.get(k) if s7 else None),
                  "mom": pct_change(cur.get(k), s30.get(k) if s30 else None)}
    return out


# ---- formatting (balance-sheet conventions: negatives in parentheses) ----
def fmt_pct(x, dp=1):
    if x is None:
        return "n/a"
    s = f"{abs(x):,.{dp}f}%"
    return f"({s})" if x < 0 else s


def fmt_share(x, dp=1):
    return "n/a" if x is None else f"{100.0 * x:,.{dp}f}%"


def fmt_usd(x):
    if x is None:
        return "n/a"
    neg, a = x < 0, abs(float(x))
    if a >= 1e9:   s = f"${a/1e9:,.2f}B"
    elif a >= 1e6: s = f"${a/1e6:,.1f}M"
    elif a >= 1e3: s = f"${a/1e3:,.1f}K"
    else:          s = f"${a:,.2f}"
    return f"({s})" if neg else s


def fmt_count(x):
    if x is None:
        return "n/a"
    neg, a = x < 0, abs(float(x))
    if a >= 1e9:   s = f"{a/1e9:,.2f}B"
    elif a >= 1e6: s = f"{a/1e6:,.1f}M"
    else:          s = f"{a:,.0f}"
    return f"({s})" if neg else s


# ---- message ----
def build_message(cur, cats, chg, fw, threshold, dune):
    now = datetime.now(timezone.utc)
    L = [
        "*Kalshi Weekly Market Digest*",
        f"{now.strftime('%A, %B %d, %Y')}  |  data as of {now.strftime('%H:%M')} UTC",
        "",
        "*Market activity*",
    ]

    def activity_line(label, contracts, key):
        c = chg.get(key, {})
        L.append(f"{label}: *{fmt_usd(contracts)}* notional "
                 f"({fmt_count(contracts)} contracts)  |  "
                 f"WoW {fmt_pct(c.get('wow'))}  |  MoM {fmt_pct(c.get('mom'))}")

    activity_line("Open interest", cur["oi"], "oi")
    activity_line("24-hour volume", cur["vol24"], "vol24")
    L.append(f"Open markets: {cur['n_markets']:,} across {cur['n_events']:,} events")

    if cur.get("avg_trade") is not None or cur.get("median_trade") is not None:
        L += ["", "*Trade size (exchange lifetime)*"]
        for label, key in (("Average trade", "avg_trade"), ("Median trade", "median_trade")):
            v = cur.get(key)
            if v is None:
                continue
            c = chg.get(key, {})
            L.append(f"{label}: *${v:,.2f}*  |  "
                     f"WoW {fmt_pct(c.get('wow'))}  |  MoM {fmt_pct(c.get('mom'))}")

    if fw and any((fw[w]["total"] or 0) > 0 for w in fw):
        L += ["", "*Institutional activity*"]
        L.append(f"Trades of ${threshold:,.0f} or more: "
                 f"*{fmt_share(fw[7]['large_share'])}* of dollar volume, trailing 7 days  |  "
                 f"{fmt_share(fw[30]['large_share'])} trailing 30 days")

    top = sorted(cats.items(), key=lambda kv: -kv[1]["vol24"])[:5]
    tot24 = cur["vol24"] or 1
    tot_oi = cur["oi"] or 1
    L += ["", "*Category mix, share of 24-hour volume*"]
    for cat, a in top:
        L.append(f"{cat}: {100*a['vol24']/tot24:,.1f}% of volume  |  "
                 f"{100*a['oi']/tot_oi:,.1f}% of open interest")

    if dune and dune.get("top_7d"):
        L += ["", "*Most active markets, trailing 7 days*"]
        for i, (name, v) in enumerate(dune["top_7d"], 1):
            val = f": {fmt_usd(v)}" if v is not None else ""
            L.append(f"{i}. {series_title(name)}{val}")

    if dune and dune.get("top_oi_change"):
        L += ["", "*Largest open-interest changes, trailing 24 hours*"]
        for i, (name, v) in enumerate(dune["top_oi_change"], 1):
            val = f": {fmt_usd(v)}" if v is not None else ""
            L.append(f"{i}. {series_title(name)}{val}")

    L += ["", ("_Notional equals contract count times the $1 settlement value and "
               "reflects both sides' maximum payout, not premium dollars at risk. "
               "WoW and MoM compare Monday snapshots; n/a indicates history is still "
               "accumulating. Negative changes appear in parentheses. Institutional "
               "share is measured from sampled public trade data (roughly two hours "
               "per day of coverage). Trade-size figures are exchange-lifetime values. "
               "Sources: Kalshi public API; dune.com/kalshi/kalshi._")]
    return "\n".join(L)


def make_payload(message, channel, reporting_app):
    return {"message": message, "channelID": channel, "reportingApp": reporting_app,
            "headline": "Kalshi Weekly Market Digest", "icon_emoji": ":bar_chart:"}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--channel", required=True)
    p.add_argument("--reporting-app", default="Kalshi Weekly Market Digest")
    p.add_argument("--history", default=DEFAULT_HISTORY)
    p.add_argument("--flow", default=DEFAULT_FLOW)
    p.add_argument("--send", action="store_true")
    args = p.parse_args()

    cur, cats = pull_platform()

    dune = None
    if os.environ.get("DUNE_API_KEY"):
        try:
            import dune_client
            dune = dune_client.get_metrics()
        except Exception as e:
            print(f"[dune] skipped: {e}")
    cur["avg_trade"] = (dune or {}).get("avg_trade")
    cur["median_trade"] = (dune or {}).get("median_trade")

    snaps = load_history(args.history)
    chg = snapshot_changes(cur, snaps, ("oi", "vol24", "avg_trade", "median_trade"))

    flow = load_flow(args.flow)
    fw = flow_windows(flow)
    threshold = flow.get("threshold", 1000.0)

    snaps.append({k: cur.get(k) for k in ("ts", "iso", "total_vol", "vol24", "oi",
                                          "n_events", "n_markets",
                                          "avg_trade", "median_trade")})
    save_history(args.history, snaps)

    msg = build_message(cur, cats, chg, fw, threshold, dune)
    payload = make_payload(msg, args.channel, args.reporting_app)

    print("=== RENDERED MESSAGE PREVIEW ===")
    print(msg)
    print(f"\n[history] {len(snaps)} snapshot(s) | [flow] {len(flow.get('days', {}))} day(s)")

    if not args.send:
        print("\n[dry run] Nothing sent. Re-run with --send (and SLACK_RELAY_TOKEN set) to post.")
        return
    token = os.environ.get("SLACK_RELAY_TOKEN")
    if not token:
        sys.exit("\nERROR: --send given but SLACK_RELAY_TOKEN is not set. Refusing to send.")
    r = requests.post(RELAY_URL, headers={"Authorization": token, "Content-Type": "application/json"},
                      json=payload, timeout=30)
    print(f"\nPOST status: {r.status_code}")
    r.raise_for_status()  # fail the run if the relay rejects the POST


if __name__ == "__main__":
    main()
