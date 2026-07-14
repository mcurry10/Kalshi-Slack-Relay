# Deploying the tracker to GitHub Actions (cloud, no desktop needed)

This runs the weekly digest on GitHub's servers, so it no longer depends on your
laptop being open. The `kalshi-tracker` folder **is** the repository root.

## 1. Create a private repo
On github.com: **New repository** → name it e.g. `prediction-markets-tracker` →
**Private** → Create. Don't add a README (we already have one).

## 2. Push this folder
Your Dune key and Slack auth live in `config/secrets.env`, which is `.gitignore`d —
it will **not** be pushed. Run these **in your own terminal** (they can't run from the
Cowork sandbox — no git filesystem access, no GitHub credentials there).

First remove the broken `.git` folder left by an earlier attempt:
- PowerShell:  `Remove-Item -Recurse -Force .git`
- Git Bash / cmd:  `rmdir /s /q .git`

Then initialize and push the whole project (note: `git add .`, not just the README):

```bash
cd "C:\Users\mcurry\Desktop\CoWork\kalshi-tracker"
git init
git add .
git status            # confirm config/secrets.env is NOT listed
git commit -m "Prediction markets weekly tracker"
git branch -M main
git remote add origin https://github.com/mcurry10/Kalshi-Weekly-Update.git
git push -u origin main
```

(If you prefer no command line: GitHub Desktop works too — just make sure you do NOT
upload `config/secrets.env`.)

## 3. Add the four repository secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**.
Copy each value from your local `config/secrets.env`:

| Secret name        | Value (from secrets.env) |
| ------------------ | ------------------------ |
| `DUNE_API_KEY`     | your Dune API key        |
| `SLACK_RELAY_URL`  | the Tray relay URL        |
| `SLACK_RELAY_AUTH` | the relay Authorization value |
| `SLACK_CHANNEL_ID` | `U08V72B4163` (your Slack user ID) |

## 4. Test it
Repo → **Actions** tab → **Prediction Markets Weekly Tracker** → **Run workflow**.
Open the run, expand "Run weekly tracker", and confirm it ends with `SLACK POST: 200`.
Then check that the digest arrived in Slack.

## 5. Turn off the desktop version
Once the GitHub run posts successfully, disable the Cowork scheduled task
`prediction-markets-weekly` (Scheduled sidebar) so you don't get two posts each Monday.

## Schedule / timezone
The workflow runs **14:00 UTC every Monday** (≈ 7:00 AM Pacific during PDT, 6:00 AM during
PST). GitHub cron is always UTC and does not follow daylight saving. To change it, edit the
`cron:` line in `.github/workflows/weekly-tracker.yml`.

## Notes
- No dependencies to install — the scripts use only the Python standard library.
- The Dune queries (IDs in `config/queries.json`) are saved on your Dune account; the
  Action just executes them via the API.
- `config/secrets.env` stays on your machine for local runs; GitHub uses the repo secrets.
