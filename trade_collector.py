"""
Kalshi trade-flow collector — accumulates institutional-flow stats over time.

A single run can only see a ~20-min slice of trades (Kalshi does ~250/sec), so
this samples a bounded recent window each run and ADDS the result into per-day
buckets in config/kalshi_trade_flow.json. Run it frequently (e.g. every few
hours) and the buckets build a representative daily/weekly picture of:
  - block-trade share of taker $ volume  (rare on Kalshi, hence accumulation)
  - large-trade share (>= threshold $)   (institutional-size proxy)
plus how many minutes of trades each day were actually sampled (coverage).

Public API only; no auth. State is committed to the repo by the workflow.
"""
import os, sys, json, time, argparse, urllib.request, urllib.parse
from datetime import datetime, timezone

BASE = "https://api.elections.kalshi.com/trade-api/v2"
DEFAULT_FLOW = "config/kalshi_trade_flow.json"


def _get(params):
    url = f"{BASE}/markets/trades?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def sample(pages, threshold):
    trades, cursor = [], None
    for _ in range(pages):
        p = {"limit": 1000}
        if cursor:
            p["cursor"] = cursor
        d = _get(p)
        b = d.get("trades", [])
        trades += b
        cursor = d.get("cursor")
        if not cursor or not b:
            break
    # bucket this sample by UTC date
    buckets = {}
    for t in trades:
        price = float(t["yes_price_dollars"] if t["taker_side"] == "yes" else t["no_price_dollars"])
        usd = float(t["count_fp"]) * price
        day = t["created_time"][:10]
        d = buckets.setdefault(day, {"total_usd": 0.0, "block_usd": 0.0, "large_usd": 0.0,
                                     "n_trades": 0, "n_block": 0, "n_large": 0,
                                     "_tmin": t["created_time"], "_tmax": t["created_time"]})
        d["total_usd"] += usd
        d["n_trades"] += 1
        if t.get("is_block_trade"):
            d["block_usd"] += usd; d["n_block"] += 1
        if usd >= threshold:
            d["large_usd"] += usd; d["n_large"] += 1
        if t["created_time"] < d["_tmin"]: d["_tmin"] = t["created_time"]
        if t["created_time"] > d["_tmax"]: d["_tmax"] = t["created_time"]
    return buckets


def _span_min(a, b):
    fa = datetime.fromisoformat(a.replace("Z", "+00:00"))
    fb = datetime.fromisoformat(b.replace("Z", "+00:00"))
    return abs((fb - fa).total_seconds()) / 60


def merge(flow, buckets):
    days = flow.setdefault("days", {})
    for day, s in buckets.items():
        d = days.setdefault(day, {"total_usd": 0.0, "block_usd": 0.0, "large_usd": 0.0,
                                  "n_trades": 0, "n_block": 0, "n_large": 0, "sample_min": 0.0})
        for k in ("total_usd", "block_usd", "large_usd", "n_trades", "n_block", "n_large"):
            d[k] += s[k]
        d["sample_min"] += _span_min(s["_tmin"], s["_tmax"])
    return flow


def prune(flow, keep_days=75):
    cutoff = (datetime.now(timezone.utc).date().toordinal()) - keep_days
    flow["days"] = {d: v for d, v in flow.get("days", {}).items()
                    if datetime.fromisoformat(d).date().toordinal() >= cutoff}
    return flow


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=120, help="1000-trade pages to sample this run")
    ap.add_argument("--threshold", type=float, default=1000.0, help="Large-trade $ threshold")
    ap.add_argument("--flow-path", default=DEFAULT_FLOW)
    args = ap.parse_args()

    try:
        with open(args.flow_path) as f:
            flow = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        flow = {"days": {}, "threshold": args.threshold}
    flow["threshold"] = args.threshold

    buckets = sample(args.pages, args.threshold)
    merge(flow, buckets)
    prune(flow)
    os.makedirs(os.path.dirname(args.flow_path) or ".", exist_ok=True)
    with open(args.flow_path, "w") as f:
        json.dump(flow, f, indent=0)

    sampled = sum(s["n_trades"] for s in buckets.values())
    print(f"sampled {sampled:,} trades into {len(buckets)} day-bucket(s); "
          f"flow file now has {len(flow['days'])} day(s) at {args.flow_path}")
    for day in sorted(flow["days"]):
        d = flow["days"][day]
        bs = 100 * d["block_usd"] / d["total_usd"] if d["total_usd"] else 0
        ls = 100 * d["large_usd"] / d["total_usd"] if d["total_usd"] else 0
        print(f"  {day}: block {bs:.2f}% | large {ls:.1f}% | "
              f"{d['n_trades']:,} tr | ~{d['sample_min']:.0f} min sampled")


if __name__ == "__main__":
    main()
