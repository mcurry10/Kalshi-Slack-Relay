"""Minimal Dune API client (stdlib only) for reading existing dashboard queries.

Reads the LATEST cached results of a query by ID via
  GET /api/v1/query/{id}/results
which is cheap and does not re-execute. Credentials come from env DUNE_API_KEY.

Query IDs are taken straight from the public Kalshi dashboard
(https://dune.com/kalshi/kalshi) — each panel links to /queries/<id>.
"""
import os, json, time, urllib.request, urllib.error, urllib.parse

API = "https://api.dune.com/api/v1"


def _key():
    k = os.environ.get("DUNE_API_KEY")
    if not k:
        raise RuntimeError("DUNE_API_KEY not set")
    return k.strip()


def latest_results(query_id, limit=20, retries=4):
    """Return {'rows': [...]} on success or {'error': 'reason'} on failure.
    Retries transient 429 / 5xx with backoff so a burst of calls doesn't get throttled."""
    url = f"{API}/query/{query_id}/results?" + urllib.parse.urlencode({"limit": limit})
    for attempt in range(retries):
        req = urllib.request.Request(url, method="GET")
        req.add_header("X-Dune-Api-Key", _key())
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read().decode())
            return {"rows": data.get("result", {}).get("rows", [])}
        except urllib.error.HTTPError as e:
            code = e.code
            if code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            reason = {403: "forbidden/private", 404: "not found",
                      429: "rate-limited"}.get(code, f"HTTP {code}")
            return {"error": reason}
        except Exception as e:
            return {"error": type(e).__name__}
    return {"error": "rate-limited"}


# ---- formatting helpers ----
def _money(x):
    x = float(x); a = abs(x)
    if a >= 1e9:  return f"${x/1e9:.1f}B"
    if a >= 1e6:  return f"${x/1e6:.1f}M"
    if a >= 1e3:  return f"${x/1e3:.1f}K"
    return f"${x:,.2f}"


def _count(x):
    x = float(x); a = abs(x)
    if a >= 1e9:  return f"{x/1e9:.2f}B"
    if a >= 1e6:  return f"{x/1e6:.1f}M"
    return f"{x:,.0f}"


def _first_number(row):
    for v in row.values():
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


# ---- curated headline metrics (public dashboard queries only) ----
# Only queries the dashboard owner left PUBLIC are readable by our key.
# The big cumulative/OI/volume panels are private (API returns 404), so they're
# intentionally omitted here. See ALL_QUERIES below to re-probe what's public.
SCALARS = [
    (6320047, "Avg trade size",   "$"),
    (6320060, "Median trade size", "$"),
]
TABLES = [
    (6219404, "Top markets \u2014 24h volume",    "$", 5),
    (6219452, "Top markets \u2014 7d volume",     "$", 5),
    (6219464, "Top markets \u2014 24h OI change", "$", 5),
    (6219494, "Top markets \u2014 7d OI change",  "$", 5),
]

# Every panel on dune.com/kalshi/kalshi, for re-probing public vs private.
ALL_QUERIES = {
    6357165: "Cumulative Combos Transactions", 6320060: "Median Trade Size",
    6219464: "Top 10 Markets by 24hr OI Change", 6171358: "Open Interest by Category",
    6171388: "Monthly Volume by Category", 6357167: "Cumulative Combos Transactions by Category",
    6319933: "Weekly Combos Volume", 6357208: "Monthly Combos Transactions",
    6319929: "Daily Combos Volume", 6320047: "Average Trade Size",
    6171391: "Monthly Transactions by Category", 6319950: "Cumulative Combos Volume",
    6315464: "Current Open Interest by Category", 6171373: "Daily Volume by Category",
    6357161: "Daily Combos Transactions", 6320065: "Median Trade Size by Category",
    6357201: "Weekly Combos Transactions", 6320062: "Average Trade Size by Category",
    6357087: "Most Recent Day's Volume", 6315458: "Open Interest (line)",
    6171386: "Weekly Volume by Category", 6171395: "Cumulative Volume",
    6357086: "Most Recent Day's Transactions", 6171379: "Daily Transactions by Category",
    6171392: "Weekly Transactions by Category", 6357152: "Cumulative Combos Volume by Category",
    6171396: "Cumulative Trades", 6219452: "Top 10 Markets by Volume Last 7d",
    6315454: "Cumulative Trades by Category", 6171404: "Current Open Interest",
    6219404: "Top 10 Markets by Volume Last 24hr", 6219494: "Top 10 Markets by 7d OI Change",
    6319938: "Monthly Combos Volume", 6315442: "Cumulative Volume by Category",
    6171396: "Cumulative Trades",
}


def build_dune_lines():
    """Return Slack mrkdwn lines for the Dune-sourced parity section."""
    lines = ["*— Dune (full-history parity) —*"]
    for qid, label, kind in SCALARS:
        res = latest_results(qid, limit=1)
        time.sleep(0.6)  # space out calls to dodge free-tier throttling
        if "error" in res:
            lines.append(f"  • {label}: _n/a ({res['error']})_")
            continue
        rows = res["rows"]
        v = _first_number(rows[0]) if rows else None
        val = "_n/a (empty)_" if v is None else (_money(v) if kind == "$" else _count(v))
        lines.append(f"  • {label}: *{val}*")
    for qid, label, kind, n in TABLES:
        res = latest_results(qid, limit=n)
        time.sleep(0.6)
        if "error" in res:
            lines.append(f"  • {label}: _n/a ({res['error']})_")
            continue
        rows = res["rows"]
        if not rows:
            lines.append(f"  • {label}: _n/a (empty)_")
            continue
        lines.append(f"  *{label}:*")
        for row in rows[:n]:
            vals = list(row.values())
            name = str(vals[0]) if vals else "?"
            num = _first_number(row)
            shown = "" if num is None else ("  " + (_money(num) if kind == "$" else _count(num)))
            lines.append(f"    · {name}{shown}")
    lines.append("Source: dune.com/kalshi/kalshi (full trade-level history).")
    return lines


if __name__ == "__main__":
    # Probe every dashboard query: PUBLIC (with a sample value) vs private/error.
    pub, priv = [], []
    for qid, name in sorted(ALL_QUERIES.items(), key=lambda kv: kv[1]):
        res = latest_results(qid, limit=1)
        time.sleep(0.6)
        if "error" in res:
            priv.append((name, qid, res["error"]))
            print(f"[private/err] {name} ({qid}): {res['error']}")
        else:
            rows = res["rows"]
            v = _first_number(rows[0]) if rows else None
            print(f"[PUBLIC]      {name} ({qid}): {rows[0] if rows else 'empty'}")
            pub.append((name, qid))
    print(f"\n{len(pub)} public, {len(priv)} private/unavailable")
