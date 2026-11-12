# Agent Hub

A small Railway service that **hosts multiple agents** on a schedule, and serves a
**dashboard** where you can see their status, stats and last run.

Each agent is a named job with its own cron schedule and command. The hub runs a
scheduler loop that launches due agents in **isolated subprocesses** (so one agent
crashing cannot take down the others or the dashboard). Every run is recorded to a
JSON state file, so the dashboard can show stats and "last run" across restarts.

The first agent, **deploy-heatmap**, regenerates the Git deployment heat map on the
david.levine personal website every day at 08:00.

## Running agents

Edit `agents/registry.py` (or add a new file under `agents/`) to declare agents:

```python
REGISTRY = {
    "my-agent": {
        "title": "My Agent",
        "description": "Does something useful.",
        "schedule": "0 8 * * *",          # cron, daily 08:00
        "command": ["python", "-m", "agents.my_agent"],
        "timeout": 900,
        "env": {"API_KEY": "${API_KEY}"},
    },
}
```

Fields:

| Key | Meaning |
| --- | --- |
| `title` / `description` | Shown on the dashboard |
| `schedule` | Cron expression (croniter) |
| `command` | Executed in a subprocess |
| `timeout` | Seconds before the subprocess is killed |
| `env` | Template-rendered env vars passed to the agent |

## Dashboard

- `/` — the dashboard (agent list, status, stats, last run, recent output)
- `/api/agents` — JSON payload for the same data

Data persisted in `state.json` (override with `STATE_FILE`; on Railway you can
attach a Persistent Storage volume for durability).

## The deploy-heatmap agent

- Runs daily at **08:00** (`0 8 * * *`).
- Collects git push/commit activity for the repos in `SITE_REPOS` over the rolling
  window `HEATMAP_DAYS`.
- Renders a 7-column (Mon–Sun) heat map and **replaces the client-side contribution
  heat map** (`<div class="gh-heat" id="ghHeat"></div>`) in `public/index.html` on the
  david.levine site with a server-rendered version.
- Commits the updated file to the site repo and updates the branch ref, so Railway
  auto-redeploys the site.

It requires a GitHub token with access to `unserrer1977/david-levine` (fine-grained PAT,
`Contents` write + `Metadata` read). Without a token it runs in **dry-run** mode:
the heat map is generated but not committed.

## Railway deployment

The service is configured to build from `Dockerfile` and listen on the `PORT` that
Railway injects.

1. Create a Railway project, e.g. `agent-hub`, and attach this directory.
2. Set the environment variables (see `.env.example`):

   | Variable | Purpose |
   | --- | --- |
   | `GITHUB_TOKEN` | Fine-grained PAT for committing the heat map (see above) |
   | `SITE_REPO` | Repo to update, `unserrer1977/david-levine` |
   | `SITE_BRANCH` | Branch to land the commit on, `main` |
   | `HEATMAP_DAYS` | Rolling window in days, default `21` |
   | `SITE_REPOS` | Comma-sep `owner/repo` list to track |
   | `STATE_FILE` | State file path (use a Persistent Storage mount for durability) |

3. Deploy. The dashboard is at `https://agent-hub-production...up.railway.app/`.
4. Watch the scheduler run `deploy-heatmap` at 08:00 and check the heatmap appears on
   `davidlevine.me`.

## Local dev

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in GITHUB_TOKEN for the site repo if you want commits
python app.py
# dashboard: http://127.0.0.1:8000
```
