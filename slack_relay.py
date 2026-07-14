"""
Kalshi investor pulse -> Anthos Slack Log Relay.

SAFETY / DESIGN:
- The relay auth token is NEVER hardcoded. It is read from the env var
  SLACK_RELAY_TOKEN. If it is absent, the script refuses to send.
- Dry run by default: prints the exact JSON payload it WOULD post.
  Actual POST happens only with `--send`.
- Destination channel is a required CLI arg; no channel is baked in.

Usage:
  python3 slack_relay.py --channel C0XXXXXXX                # dry run (prints payload)
  SLACK_RELAY_TOKEN=... python3 slack_relay.py --channel C0XXXXXXX --send
"""
import os, sys, json, argparse, requests
from collections import defaultdict

RELAY_URL = "https://647891eb-2a47-4a3b-ac32-bb8df7ee4b8c.trayapp.io"
BASE = "https://api.elections.kalshi.com/trade-api/v2"


def pull_category_stats():
    S = requests.Session()
    cursor, events = None, []
    for _ in range(200):
        params = {"limit": 200, "status": "open", "with_nested_markets": "true"}
        if cursor:
            params["cursor"] = cursor
        d = S.get(f"{BASE}/events", params=params, timeout=30).json()
        batch = d.get("events", [])
        events.extend(batch)
        cursor = d.get("cursor")
        if not cursor or not batch:
            break
    agg = defaultdict(lambda: {"vol24": 0.0, "oi": 0.0})
    n_markets = 0
    for ev in events:
        cat = ev.get("category") or "Uncategorized"
        for m in ev.get("markets") or []:
            n_markets += 1
            agg[cat]["vol24"] += float(m.get("volume_24h_fp") or 0)
            agg[cat]["oi"]    += float(m.get("open_interest_fp") or 0)
    return agg, len(events), n_markets


def build_message(agg, n_events, n_markets):
    tot24 = sum(a["vol24"] for a in agg.values()) or 1
    tot_oi = sum(a["oi"] for a in agg.values()) or 1
    top = sorted(agg.items(), key=lambda kv: -kv[1]["vol24"])[:5]
    lines = [
        f"*Kalshi platform pulse*  —  {n_events:,} open events / {n_markets:,} markets",
        f"24h volume: *{tot24:,.0f}* contracts   |   Open interest: *{tot_oi:,.0f}* contracts",
        "",
        "*Top categories by 24h volume:*",
    ]
    for cat, a in top:
        lines.append(
            f"  • {cat}: {100*a['vol24']/tot24:.1f}% of vol  "
            f"(_{100*a['oi']/tot_oi:.1f}% of open interest_)"
        )
    lines.append("")
    lines.append("_Volume is contract count (notional double-counts both sides "
                 "and overstates dollars at risk); not fee revenue._")
    return "\n".join(lines)


def make_payload(message, channel, reporting_app):
    return {
        "message": message,
        "channelID": channel,
        "reportingApp": reporting_app,
        "headline": "Kalshi Investor Pulse",
        "icon_emoji": ":chart_with_upwards_trend:",
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--channel", required=True, help="Slack channel ID (or user ID for a DM)")
    p.add_argument("--reporting-app", default="Kalshi Investor Pulse")
    p.add_argument("--send", action="store_true", help="Actually POST. Omit for dry run.")
    args = p.parse_args()

    agg, n_events, n_markets = pull_category_stats()
    msg = build_message(agg, n_events, n_markets)
    payload = make_payload(msg, args.channel, args.reporting_app)

    print("=== PAYLOAD THAT WOULD BE SENT ===")
    print(json.dumps(payload, indent=2))
    print("=== RENDERED MESSAGE PREVIEW ===")
    print(msg)

    if not args.send:
        print("\n[dry run] Nothing sent. Re-run with --send (and SLACK_RELAY_TOKEN set) to post.")
        return

    token = os.environ.get("SLACK_RELAY_TOKEN")
    if not token:
        sys.exit("\nERROR: --send given but SLACK_RELAY_TOKEN is not set. Refusing to send.")

    r = requests.post(RELAY_URL, headers={"Authorization": token,
                                          "Content-Type": "application/json"},
                      json=payload, timeout=30)
    print(f"\nPOST status: {r.status_code}  (relay fails silently on bad payload — verify in Slack)")


if __name__ == "__main__":
    main()
