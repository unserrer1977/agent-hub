"""Agent hub registry: define every agent here.

Each agent:
  - name:      unique id (used in URLs, state, logs)
  - title:     human label shown on the dashboard
  - description: what it does
  - schedule:  cron expression (croniter supports standard 5-field + @every)
  - command:   shell command list executed in a subprocess
  - env:       extra environment variables passed to the agent
  - timeout:   subprocess timeout in seconds
"""
from .deploy_heatmap import DEPLOY_HEATMAP_CFG

REGISTRY = {
    "deploy-heatmap": DEPLOY_HEATMAP_CFG,
}
