#!/usr/bin/env python3
"""Agent Hub — a Railway service that hosts multiple agents and serves a dashboard.

Process layout:
  - A single uvicorn web server serves the dashboard (HTML + JSON API).
  - A background scheduler thread checks every agent's cron schedule and runs
    due agents in isolated subprocesses, so one agent failing cannot take down
    the hub or its siblings.

Agents are defined in agents/registry.py. Each agent's runs, stats, and the
last run are persisted to a JSON state file. The dashboard reads the state
file, so "last run / stats" survive restarts.
"""
import os
import subprocess
import threading
import time
import traceback
import uvicorn
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

import state
from agents import REGISTRY

app = FastAPI(title="Agent Hub")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _load():
    return state.load_state()


def agent_state(name):
    return state.agent_state(_load()["agents"], name)


def get_agent(name):
    return REGISTRY.get(name)


def next_due(schedule, last_run_iso):
    """Next due time: next occurrence strictly after last_run, or first future
    occurrence of the cron from now if the agent has never run."""
    try:
        import croniter
        if last_run_iso:
            dt = datetime.fromisoformat(last_run_iso.replace("Z", "+00:00"))
            return croniter.croniter(schedule, dt).get_next(datetime)
        return croniter.croniter(schedule, datetime.now(timezone.utc)).get_next(datetime)
    except Exception:
        return None


def should_run(cfg, last_run_iso):
    due = next_due(cfg.get("schedule", "0 * * * *"), last_run_iso)
    if not due:
        return False
    return datetime.now(timezone.utc) >= due


def run_agent(name, cfg):
    """Run a single agent in a detached subprocess and record the result."""
    ag = agent_state(name)
    ag["running"] = True
    _save()

    started = now_iso()
    exit_code = None
    output = ""
    try:
        proc = subprocess.run(
            cfg.get("command", ["true"]),
            env=agent_env(cfg),
            capture_output=True,
            text=True,
            timeout=cfg.get("timeout", 900),
        )
        exit_code = proc.returncode
        output = (proc.stdout or "") + (proc.stderr or "")
        if exit_code != 0:
            raise RuntimeError("agent exited with code %d" % exit_code)
    except Exception as e:
        exit_code = -1
        output = traceback.format_exc() + "\n" + str(e)
    finally:
        finished = now_iso()
        state.record_run(agent_state(name), name, started, finished, exit_code, output)
        _save()
        ag["running"] = False

    return {
        "name": name,
        "exit_code": exit_code,
        "output": output,
        "finished_at": finished,
    }


def agent_env(cfg):
    env = dict(os.environ)
    for k, v in cfg.get("env", {}).items():
        env[k] = os.path.expandvars(v)
    return env


def _save():
    state.save_state(_load())


def scheduler_loop():
    interval = int(os.environ.get("SCHEDULER_INTERVAL", 30))
    while True:
        time.sleep(interval)
        now = now_iso()
        for name, cfg in REGISTRY.items():
            ag = agent_state(name)
            if ag.get("running"):
                continue
            if should_run(cfg, ag.get("last_run")):
                run_agent(name, cfg)


def _build_index():
    rows = []
    for name in sorted(REGISTRY):
        cfg = REGISTRY[name]
        ag = agent_state(name)
        running = ag.get("running")
        last = ag.get("last_run")
        if running:
            status, label = "running", "Running"
        elif last:
            status, label = "idle", "Idle"
        else:
            status, label = "pending", "Pending"
        last_at = last["finished_at"] if last else None
        rows.append({
            "name": name,
            "title": cfg.get("title", name),
            "description": cfg.get("description", ""),
            "schedule": cfg.get("schedule", ""),
            "status": status,
            "label": label,
            "next_run": next_due(cfg.get("schedule", ""), ag.get("last_run")),
            "runs_total": ag.get("stats", {}).get("total", 0),
            "runs_success": ag.get("stats", {}).get("success", 0),
            "runs_failed": ag.get("stats", {}).get("failed", 0),
            "last_run": last_at,
            "last_exit": last["exit_code"] if last else None,
            "last_output": (last["output"] if last else ""),
            "running": running,
        })
    gl = _load().get("global", {})
    return rows, gl


def _dashboard_html():
    rows, gl = _build_index()
    css = """
<style>
  :root{--bg:#0f1115;--panel:#1a1d25;--panel-2:#222631;--ink:#e8eaed;--muted:#8a90a0;--line:#2a2f3c;--ok:#4ade80;--bad:#f87171;--acc:#60a5fa;--run:#facc15}
  *{box-sizing:border-box}
  body{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--ink);line-height:1.5}
  a{color:var(--acc)}
  .wrap{max-width:1100px;margin:0 auto;padding:24px 20px 80px}
  header.hero{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:24px}
  h1{font-size:22px;margin:0;letter-spacing:-.02em}
  h1 small{color:var(--muted);font-weight:500;font-size:14px;letter-spacing:0}
  .subtitle{color:var(--muted);font-size:14px;margin:4px 0 0}
  .statsrow{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;margin-bottom:24px}
  .stat{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}
  .stat .v{font-size:24px;font-weight:700;letter-spacing:-.02em}
  .stat .l{font-size:12px;color:var(--muted);margin-top:2px}
  .agents{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}
  .agent{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;box-shadow:0 1px 0 var(--line)}
  .agent.running{border-color:var(--run);box-shadow:0 0 0 1px var(--run)}
  .agent .hd{display:flex;align-items:center;justify-content:space-between;gap:10px}
  .agent .name{font-size:16px;font-weight:700}
  .agent .sub{color:var(--muted);font-size:13px;margin-top:2px}
  .badge{display:inline-flex;align-items:center;gap:6px;padding:3px 9px;border-radius:999px;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.04em}
  .badge.running{background:#eab308;color:#141414}
  .badge.idle{background:#2563eb;color:#fff}
  .badge.pending{background:#4b5563;color:#fff}
  .meta{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin:12px 0;padding:10px 0;border-top:1px dashed var(--line);border-bottom:1px dashed var(--line)}
  .meta .k{color:var(--muted);font-size:12px}
  .meta .v{font-size:13px;color:var(--ink);font-variant-numeric:tabular-nums}
  .meta .v.ok{color:var(--ok)} .meta .v.bad{color:var(--bad)}
  .out{background:var(--panel-2);border:1px dashed var(--line);border-radius:8px;padding:10px 12px;font-size:12px;color:var(--muted);white-space:pre-wrap;word-break:break-word;max-height:160px;overflow:auto;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
  .out:empty{display:none}
  footer{margin-top:28px;color:var(--muted);font-size:12px;text-align:center}
  @media (max-width:600px){.agent .hd{flex-direction:column;gap:4px}}
</style>
"""
    token = {
        "__GTOTAL__": gl.get("runs_total", 0),
        "__GOK__": gl.get("runs_successful", 0),
        "__GFAIL__": gl.get("runs_failed", 0),
        "__GAGENTS__": len(REGISTRY),
        "__NOW__": now_iso(),
    }
    html = [
        """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agent Hub</title>
{css}
</head>
<body>
<div class="wrap">
  <header class="hero">
    <div>
      <h1>Agent Hub<small>&nbsp;·&nbsp;multi-agent scheduler with a dashboard</small></h1>
      <p class="subtitle">Each agent runs on its own schedule in an isolated subprocess; this dashboard shows status, stats and the last run.</p>
    </div>
  </header>

  <div class="statsrow">
    <div class="stat"><div class="v">__GTOTAL__</div><div class="l">Total runs</div></div>
    <div class="stat"><div class="v">__GOK__</div><div class="l">Successful</div></div>
    <div class="stat"><div class="v">__GFAIL__</div><div class="l">Failed</div></div>
    <div class="stat"><div class="v">__GAGENTS__</div><div class="l">Agents</div></div>
  </div>

  <div class="agents">
""",
    ]
    for r in rows:
        html.append(
            """    <div class="agent running">
      <div class="hd">
        <div>
          <div class="name">{title}</div>
          <div class="sub">{desc}</div>
        </div>
        <span class="badge {status}">{label}</span>
      </div>
      <div class="sub" style="margin-top:8px">Schedule: <code>{schedule}</code> &nbsp;·&nbsp; Next run: <span class="{nrcls}">{next}</span></div>
      <div class="meta">
        <div><div class="k">Runs</div><div class="v">{total}</div></div>
        <div><div class="k">Success</div><div class="v ok">{ok}</div></div>
        <div><div class="k">Failed</div><div class="v bad">{fail}</div></div>
        <div><div class="k">Last run</div><div class="v">{last}</div></div>
        <div><div class="k">Last exit</div><div class="v {lc}">{lce}</div></div>
      </div>
      <div class="out">{out}</div>
    </div>
"""
            .format(
                title=r["title"],
                desc=r["description"][:120] + ("…" if len(r["description"]) > 120 else ""),
                status=r["status"],
                label=r["label"],
                schedule=r["schedule"],
                next=r["next_run"].strftime("%Y-%m-%d %H:%M") if r["next_run"] else "—",
                nrcls="muted" if not r["next_run"] else "",
                total=r["runs_total"],
                ok=r["runs_success"],
                fail=r["runs_failed"],
                last=r["last_run"][:16].replace("T", " ") if r["last_run"] else "—",
                lce=r["last_exit"],
                lc="ok" if r["last_exit"] == 0 else ("bad" if r["last_exit"] is not None else ""),
                out=(r["last_output"][:300] + ("…" if len(r["last_output"]) > 300 else "")) or "—",
            )
        )
    html.append(
        """
  </div>
  <footer>
    Scheduler checks every __INTERVAL__s; agents run in isolated subprocesses.<br>
    State persisted in <code>__STATE_FILE__</code>
  </footer>
</div>
</body>
</html>"""
        )
    doc = "".join(html)
    doc = doc.replace("__GTOTAL__", str(token["__GTOTAL__"]))
    doc = doc.replace("__GOK__", str(token["__GOK__"]))
    doc = doc.replace("__GFAIL__", str(token["__GFAIL__"]))
    doc = doc.replace("__GAGENTS__", str(token["__GAGENTS__"]))
    doc = doc.replace("__INTERVAL__", str(int(os.environ.get("SCHEDULER_INTERVAL", 30))))
    doc = doc.replace("__STATE_FILE__", os.path.basename(state.STATE_FILE))
    return doc


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return HTMLResponse(content=_dashboard_html(), status_code=200)


@app.get("/api/agents")
def api_agents():
    rows, gl = _build_index()
    return {"global": gl, "agents": rows}


if __name__ == "__main__":
    # Ensure a state file exists before the scheduler starts.
    _load()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    uvicorn.run(
        app,
        host=os.environ.get("WEB_HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
    )
