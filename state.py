"""State persistence for the agent hub.

Runs are recorded to a JSON file so the dashboard can show stats and the last
run across service restarts. The file is written atomically (tmp + rename).
"""
import json
import os
import threading
import time
from datetime import datetime, timezone

STATE_FILE = os.environ.get("STATE_FILE", "/app/state.json")
_lock = threading.Lock()


def _now():
    return datetime.now(timezone.utc).isoformat()


def default_state():
    return {
        "global": {
            "runs_total": 0,
            "runs_successful": 0,
            "runs_failed": 0,
            "start_time": _now(),
        },
        "agents": {},
    }


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
            g = data.setdefault("global", {})
            g.setdefault("runs_total", 0)
            g.setdefault("runs_successful", 0)
            g.setdefault("runs_failed", 0)
            g.setdefault("start_time", _now())
            agents = data.setdefault("agents", {})
            for name, ag in agents.items():
                ag.setdefault("runs", [])
                ag.setdefault("stats", {"total": 0, "success": 0, "failed": 0})
                ag.setdefault("last_run", None)
                ag.setdefault("next_run", None)
                ag.setdefault("running", False)
                ag.setdefault("last_event", None)
            return data
        except (ValueError, OSError):
            pass
    return default_state()


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def agent_state(agents, name):
    return agents.setdefault(name, {
        "name": name,
        "runs": [],
        "stats": {"total": 0, "success": 0, "failed": 0},
        "last_run": None,
        "next_run": None,
        "running": False,
        "last_event": None,
    })


def record_run(agents, name, started_at, finished_at, exit_code, output):
    """Record one agent run and update stats + scheduling state."""
    with _lock:
        st = load_state()
        agents_map = st.setdefault("agents", {})
        ag = agent_state(agents_map, name)
        ag["runs"].append({
            "started_at": started_at,
            "finished_at": finished_at,
            "exit_code": exit_code,
            "output": output,
        })
        ag["stats"]["total"] = len(ag["runs"])
        ag["stats"]["success"] = sum(1 for r in ag["runs"] if r["exit_code"] == 0)
        ag["stats"]["failed"] = ag["stats"]["total"] - ag["stats"]["success"]

        gl = st.setdefault("global", {})
        gl["runs_total"] += 1
        if exit_code == 0:
            gl["runs_successful"] += 1
        else:
            gl["runs_failed"] += 1

        ag["last_run"] = {
            "started_at": started_at,
            "finished_at": finished_at,
            "exit_code": exit_code,
            "output": output,
        }
        ag["last_event"] = finished_at
        save_state(st)
    return ag["last_run"]


def next_run_after(last_run_iso, schedule):
    """Next due time strictly after `last_run_iso` following `schedule`."""
    try:
        import croniter
        if not last_run_iso:
            return None
        dt = datetime.fromisoformat(last_run_iso.replace("Z", "+00:00"))
        return croniter.croniter(schedule, dt).get_next(datetime)
    except Exception:
        return None


def compute_next_run(agent_cfg, last_run_iso):
    """Best-effort next due time for an agent based on schedule + last run."""
    schedule = agent_cfg.get("schedule", "0 * * * *")
    return next_run_after(last_run_iso, schedule)
