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


# ---- curated headline metrics (query_id, label, kind) ----
SCALARS = [
    (6171395, "Cumulative volume",          "$"),
    (6171404, "Current open interest",      "$"),
    (6357087, "Latest day volume",          "$"),
    (6357086, "Latest day transactions",    "#"),
    (6171396, "Cumulative trades",          "#"),
    (6320047, "Avg trade size",             "$"),
    (6320060, "Median trade size",          "$"),
]
TABLES = [
    (6315442, "Cumulative volume by category", "$", 5),
    (6219452, "Top markets — 7d volume",       "$", 5),
]


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
    # Diagnostic: dump raw rows for each configured query so schemas/errors are visible.
    for qid, label, *_ in SCALARS + TABLES:
        print(f"\n=== {label} (query {qid}) ===")
        print(json.dumps(latest_results(qid, limit=3), indent=2, default=str)[:900])
        time.sleep(0.6)
