"""Local runner: reads config.json and starts the BTP Guardian A2A server."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
CONFIG_FILE = ROOT / "config.json"
MAIN_PY = ROOT / "assets" / "btp-guardian-agent" / "app" / "main.py"

config = json.loads(CONFIG_FILE.read_text())

env = {
    **os.environ,
    # AI Core credentials for LiteLLM
    "AICORE_AUTH_URL": config["url"],
    "AICORE_CLIENT_ID": config["clientid"],
    "AICORE_CLIENT_SECRET": config["clientsecret"],
    "AICORE_BASE_URL": config["serviceurls"]["AI_API_URL"],
    "AICORE_RESOURCE_GROUP": os.environ.get("AICORE_RESOURCE_GROUP", "default"),
    # Mock BTP MCP tools locally (no Agent Gateway needed)
    "IBD_TESTING": "1",
    # Local mock has no real resources, so enable write tools too.
    "BTP_ALLOW_WRITES": "1",
    # Use Claude 4.6 Opus (deployed in the tenant) — routes via Bedrock, so no
    # 128-tool cap like gpt-4o. Override with AGENT_LLM_MODEL if desired.
    "AGENT_LLM_MODEL": os.environ.get("AGENT_LLM_MODEL", "anthropic--claude-4.6-opus"),
}

port = sys.argv[1] if len(sys.argv) > 1 else "5000"
env["PORT"] = port

subprocess.run(
    [sys.executable, str(MAIN_PY)],
    cwd=str(MAIN_PY.parent),
    env=env,
)
