"""
Kalshi platform pulse — public API only, no credentials.
Paginates all open events (with nested markets), aggregates 24h volume,
total volume, and open interest by category, and prints a concentration table.
"""
import requests, time, sys
from collections import defaultdict

BASE = "https://api.elections.kalshi.com/trade-api/v2"
S = requests.Session()

def pull_events(max_pages=200):
    cursor, pages, events = None, 0, []
    while pages < max_pages:
        params = {"limit": 200, "status": "open", "with_nested_markets": "true"}
        if cursor:
            params["cursor"] = cursor
        r = S.get(f"{BASE}/events", params=params, timeout=30)
        r.raise_for_status()
        d = r.json()
        batch = d.get("events", [])
        events.extend(batch)
        pages += 1
        cursor = d.get("cursor")
        if not cursor or not batch:
            break
        time.sleep(0.05)  # stay well under public rate limits
    return events, pages, cursor

events, pages, leftover = pull_events()
agg = defaultdict(lambda: {"vol24": 0.0, "vol_total": 0.0, "oi": 0.0, "markets": 0, "events": 0})

for ev in events:
    cat = ev.get("category") or "Uncategorized"
    a = agg[cat]
    a["events"] += 1
    for m in ev.get("markets") or []:
        a["markets"] += 1
        a["vol24"]      += float(m.get("volume_24h_fp") or 0)
        a["vol_total"]  += float(m.get("volume_fp") or 0)
        a["oi"]         += float(m.get("open_interest_fp") or 0)

tot24  = sum(a["vol24"] for a in agg.values()) or 1
tot_oi = sum(a["oi"] for a in agg.values()) or 1

print(f"Pages fetched: {pages}{' (MORE REMAIN — table is partial)' if leftover else ' (complete)'}")
print(f"Open events: {len(events):,} | Open markets: {sum(a['markets'] for a in agg.values()):,}\n")
print(f"{'Category':<22} {'24h vol (contracts)':>20} {'% of 24h':>9} {'Open interest':>16} {'% of OI':>8}")
print("-" * 80)
for cat, a in sorted(agg.items(), key=lambda kv: -kv[1]["vol24"]):
    print(f"{cat:<22} {a['vol24']:>20,.0f} {100*a['vol24']/tot24:>8.1f}% {a['oi']:>16,.0f} {100*a['oi']/tot_oi:>7.1f}%")
print("-" * 80)
print(f"{'TOTAL':<22} {tot24:>20,.0f} {'100.0%':>9} {tot_oi:>16,.0f} {'100.0%':>8}")
