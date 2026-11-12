"""Agent: regenerate the Git deployment heat map on the david.levine personal site.

The site (davidlevine.me) is a static nginx host with a GitHub-activity section
(c9) that already renders a contribution heat map. This agent:

  1. Collects git push / commit activity for a set of public repos over a
     rolling window.
  2. Renders it as a 7-column "deployment heat map" strip (labelled
     "Git deployment heat map").
  3. Replaces the client-side contribution heat map in public/index.html with
     a server-rendered version.
  4. Commits the updated file to the site repo and updates the branch ref, so
     Railway auto-redeploys the site.

Runs daily at 08:00 via the agent hub scheduler.
"""
import base64
import os
import subprocess
import traceback
from datetime import datetime, timedelta, timezone

import requests

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
SITE_REPO = os.environ.get("SITE_REPO", "unserrer1977/david-levine").strip()
SITE_BRANCH = os.environ.get("SITE_BRANCH", "main").strip()
GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME", "agent-hub").strip()
GITHUB_EMAIL = os.environ.get("GITHUB_EMAIL", "agent-hub@users.noreply.github.com").strip()
HEATMAP_DAYS = int(os.environ.get("HEATMAP_DAYS", "21"))
SITE_INDEX = "public/index.html"

_repo_owner, _repo_name = SITE_REPO.split("/", 1)
REPO_PATH = "/repos/%s/%s" % (_repo_owner, _repo_name)
HOTEL = "https://api.github.com"

# Intensity buckets -> CSS classes (mirrors the site's colour ramp)
LEVELS = ["mild", "med", "hot", "peak"]


def _gh(path, token=None, method="GET", payload=None, params=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "agent-hub",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    elif GITHUB_TOKEN:
        # Use the configured token by default (needed for private repos).
        headers["Authorization"] = "Bearer " + GITHUB_TOKEN
    url = HOTEL + path
    r = requests.request(method, url, headers=headers, json=payload, params=params, timeout=60)
    if r.status_code == 403:
        raise RuntimeError("GitHub API rate-limited: %s" % r.text)
    r.raise_for_status()
    return r.json()


def collect_activity(days=HEATMAP_DAYS):
    """Return list of {date: date, count: int} across watched repos."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    day_totals = {}
    for repo in repos_to_watch():
        for commit in commits_in_window(repo, start, end):
            d = commit["commit"]["committer"]["date"][:10]
            day_totals[d] = day_totals.get(d, 0) + 1
    return [{"date": d, "count": c} for d, c in sorted(day_totals.items())]


def repos_to_watch():
    """Full 'owner/repo' strings whose git activity feeds the heat map.

    The user's public repos are mirror-fetched; any repo name can be overridden
    via the SITE_REPOS env var, e.g.:
      SITE_REPOS=unserrer1977/agent-hub,unserrer1977/apprenticeship-watcher
    """
    raw = os.environ.get("SITE_REPOS", "unserrer1977/agent-hub,unserrer1977/apprenticeship-watcher,unserrer1977/ca-lookbook,unserrer1977/fuse-design-system,unserrer1977/instinct-task-compass,unserrer1977/receipts,unserrer1977/sollo-design-system,unserrer1977/wepsbot,unserrer1977/whatsmeow").strip()
    return [r for r in raw.split(",") if r]


def commits_in_window(repo, start, end, per_page=100):
    """Paginate commits across the window for one 'owner/repo' repo."""
    since = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    until = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    page = 1
    while True:
        url = "/repos/%s/commits" % repo
        params = {"since": since, "until": until, "per_page": per_page, "page": page}
        try:
            data = _gh(url, params=params)
        except requests.HTTPError:
            break
        if not data:
            break
        for c in data:
            yield c
        if len(data) < per_page:
            break
        page += 1


def render_heatmap(activity, days=HEATMAP_DAYS):
    """Render a 7-column (Mon-Sun) heat map grid.

    Rows are weeks; each cell colour reflects per-day activity intensity.
    """
    if not activity:
        activity = [{"date": (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d"), "count": 0} for i in range(days)]

    # index by date
    by_date = {a["date"]: a["count"] for a in activity}
    max_count = max([a["count"] for a in activity] + [1])

    def level(n):
        if n <= 0:
            return None
        if n >= max_count * 0.8:
            return "peak"
        if n >= max_count * 0.6:
            return "hot"
        if n >= max_count * 0.4:
            return "med"
        return "mild"

    # build week rows (Mon first) covering the range
    start = min(a["date"] for a in activity)
    end = max(datetime.strptime(a["date"], "%Y-%m-%d") for a in activity)
    d0 = datetime.strptime(start, "%Y-%m-%d").replace(hour=0, minute=0, second=0)
    # snap to Monday
    while d0.weekday() != 0:
        d0 -= timedelta(days=1)
    rows = []
    cur = d0
    while cur <= end:
        week = []
        for off in range(7):
            day = cur + timedelta(days=off)
            key = day.strftime("%Y-%m-%d")
            week.append((day.strftime("%a")[:3], day, by_date.get(key, 0), level(by_date.get(key, 0))))
        rows.append(week)
        cur += timedelta(weeks=1)

    cells = []
    for row in rows:
        cells.append('<div class="dep-heat-row">')
        for label, date, count, lvl in row:
            cls = '<span class="day-cell %s" title="%s %d"></span>' % (lvl or "empty", date, count)
            cells.append(cls)
        cells.append('</div>')

    return (
        '<div id="dl-heatmap">'
        '<div class="dep-heat-head">Git deployment heat map '
        '<span class="dep-heat-legend">'
        '<i class="lev peak"></i><i class="lev hot"></i>'
        '<i class="lev med"></i><i class="lev mild"></i>'
        '<i class="lev empty"></i></span>'
        '<span class="dep-heat-count">%d days</span></div>'
        '<div class="dep-heat-grid">%s</div>'
        '<!-- /dl-heatmap -->'
        '</div>' % (days, "".join(cells))
    )


def update_site():
    """Write the heat map into the site repo and push to SITE_BRANCH.

    Uses the GitHub REST 'Update a file' endpoint, which atomically commits the
    content change to a branch (no manual blob/tree/commit dance).
    """
    if not GITHUB_TOKEN:
        return {"status": "dry-run", "note": "GITHUB_TOKEN not set; heat map generated but not committed."}

    # 1) fetch existing file content (and its blob sha, required for updates)
    info = _gh("%s/contents/%s" % (REPO_PATH, SITE_INDEX), token=GITHUB_TOKEN)
    old_content = base64.b64decode(info["content"]).decode("utf-8")
    old_sha = info["sha"]

    # 2) server-render the heat map and replace the existing block (or insert it
    #    if the site has not been updated yet).
    import re
    fragment = render_heatmap(collect_activity())
    # Short-circuit: if the rendered fragment is already on the site, it is current.
    if fragment in old_content:
        return {"status": "unchanged", "note": "Heat map already current."}
    # Replace an existing heat map block (matched by the dl-heatmap id + marker).
    pattern = r'<div id="dl-heatmap">.*?<!-- /dl-heatmap --></div>'
    new_content, count = re.subn(
        pattern, fragment, old_content, count=1, flags=re.DOTALL
    )
    if count == 0:
        # No existing heat map block: insert before the languages section (or append).
        marker = '<h4 style="margin:0 0 12px;font-size:14px;letter-spacing:.02em;text-transform:uppercase;color:var(--muted);font-weight:600">'
        idx = old_content.find(marker)
        if idx != -1:
            new_content = old_content[:idx] + fragment + old_content[idx:]
        else:
            new_content = old_content + fragment
    else:
        new_content = new_content

    if new_content == old_content:
        return {"status": "unchanged", "note": "Heat map already current."}

    # 3) commit the change via the GitHub REST 'Update a file' endpoint
    commit = _gh(
        "%s/contents/%s" % (REPO_PATH, SITE_INDEX),
        method="PUT",
        payload={
            "message": "agent: update Git deployment heat map",
            "content": base64.b64encode(new_content.encode("utf-8")).decode("ascii"),
            "sha": old_sha,
            "branch": SITE_BRANCH,
            "committer": {"name": GITHUB_USERNAME, "email": GITHUB_EMAIL},
        },
        token=GITHUB_TOKEN,
    )
    return {"status": "ok", "sha": commit["commit"]["sha"], "branch": SITE_BRANCH}


def run():
    """Entry point called by the agent hub scheduler."""
    started = datetime.now(timezone.utc).isoformat()
    try:
        result = update_site()
        result["started_at"] = started
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        if result.get("status") == "dry-run":
            msg = "Heat map generated (dry-run): %s" % result.get("note", "")
            print(msg)
            return msg
        if result.get("status") == "ok":
            msg = "Heat map updated on %s@%s" % (SITE_REPO, result.get("sha", ""))
        else:
            msg = "Heat map %s: %s" % (result.get("status"), result.get("note", ""))
        print(msg)
        return msg
    except Exception as e:
        finished = datetime.now(timezone.utc).isoformat()
        err = traceback.format_exc()
        print("deploy-heatmap FAILED: %s\n%s" % (e, err))
        return {"status": "error", "error": str(e), "traceback": err, "started_at": started, "finished_at": finished}


DEPLOY_HEATMAP_CFG = {
    "name": "deploy-heatmap",
    "title": "Git Deployment Heat Map",
    "description": "Regenerates the deployment heat map on the david.levine personal website (runs daily at 08:00).",
    "schedule": "0 8 * * *",
    "command": ["python", "-m", "agents.deploy_heatmap"],
    "timeout": 900,
    "env": {
        "GITHUB_TOKEN": "${GITHUB_TOKEN}",
        "SITE_REPO": "${SITE_REPO}",
        "SITE_BRANCH": "${SITE_BRANCH}",
        "GITHUB_USERNAME": "${GITHUB_USERNAME}",
        "GITHUB_EMAIL": "${GITHUB_EMAIL}",
        "HEATMAP_DAYS": "${HEATMAP_DAYS}",
    },
}
