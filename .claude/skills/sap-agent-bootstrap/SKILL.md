---
name: sap-agent-bootstrap
description: Bootstrap a complete Joule Studio runtime agent project. Don't invoke on its own, go through intent-analysis.
metadata:
  version: 1.0.0
  author: sap-joule-studio
---
# Joule Studio runtime Agent Bootstrap

Creates a ready-to-deploy AI agent asset for Joule Studio runtime with A2A protocol, LangGraph, and SAP AI Core integration.

**This skill operates on the current working directory.** The caller is responsible for running it from the correct target directory (e.g. `assets/<asset-name>/`).

## Instructions

Follow these 3 phases in order:

### Phase 1: Collect User Input

Use `question` tool if available or a similar tool that can be used to ask questions to the user to gather exactly 2 values BEFORE any file operations:

```
Question 1: "Please enter your agent name (e.g., expense-tracker-agent):"
Question 2: "Please enter your agent description (e.g., 'An AI agent that tracks business expenses'):"
```

**Example interaction:**
- User wants: "Create an agent to help with travel expenses"
- Agent name: `travel-expense-agent`
- Agent description: `An AI agent that helps employees manage and submit travel expenses`

### Phase 2: Copy Templates (Deterministic)

Use the skill base directory injected at load time (available at the bottom of this skill as `Base directory for this skill`). Set `SKILL_PATH` to that value and run:

```bash
set -euo pipefail
SKILL_PATH="<base-directory-for-this-skill>"
cp -r "$SKILL_PATH/templates/." ./
```

This produces `app/mcp_tools.py` — the owned indirection layer for MCP tool loading (see Output Structure below).

### Phase 3: Replace Placeholders (Deterministic)

Use a Python script to replace all placeholders. Derive the <...> placeholder values from the 2 inputs collected in Phase 1. Refer to "Placeholder Derivation Rules" section for more information.

**macOS/Linux** — run inline with a heredoc:

```bash
python - << 'PYEOF'
r = {
    "{{AGENT_TITLE}}": "<Agent Title>",
    "{{AGENT_DESCRIPTION}}": "<agent-description>",
    "{{AGENT_ID}}": "<agent-name>",
    "{{AGENT_NAME}}": "<agent-name>",
    "{{AGENT_SKILL_DESCRIPTION}}": "<agent-description>",
    "{{AGENT_CARD_DESCRIPTION}}": "<agent-description>",
    "[\"{{AGENT_TAGS}}\"]": "<tags-list>",
    "[\"{{AGENT_EXAMPLES}}\"]": "<examples-list>",
    "{{SYSTEM_PROMPT}}": "<system-prompt>",
}
for p in ["README.md", "app/main.py", "app/agent.py"]:
    t = open(p).read()
    for k, v in r.items(): t = t.replace(k, v)
    open(p, "w").write(t)
    print(f"Processed: {p}")
PYEOF
```

**Windows** — heredocs are not supported; save the script above to `replace_placeholders.py` (with values filled in) and run:
```powershell
python replace_placeholders.py
```

## Placeholder Derivation Rules

Derive all 10 placeholders from the 2 user inputs:

| Placeholder                   | Derivation                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | Example Value                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
|-------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `{{AGENT_NAME}}`              | Direct from input                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | `travel-expense-agent`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `{{AGENT_NAMESPACE}}`         | Same as AGENT_NAME                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | `travel-expense-agent`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `{{AGENT_ID}}`                | Same as AGENT_NAME                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | `travel-expense-agent`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `{{AGENT_TITLE}}`             | Title-case: replace `-` with space, capitalize                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | `Travel Expense Agent`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `{{AGENT_TAGS}}`              | Split AGENT_NAME by `-` into Python list                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | `["travel", "expense", "agent"]`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `{{AGENT_DESCRIPTION}}`       | Direct from input                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | `An AI agent that helps employees manage and submit travel expenses`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `{{AGENT_SKILL_DESCRIPTION}}` | Same as AGENT_DESCRIPTION                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | `An AI agent that helps employees manage and submit travel expenses`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `{{AGENT_CARD_DESCRIPTION}}`  | Same as AGENT_DESCRIPTION                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | `An AI agent that helps employees manage and submit travel expenses`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `{{SYSTEM_PROMPT}}`           | Template: `You are {AGENT_DESCRIPTION}. Help users with their requests.\n\nIMPORTANT: You MUST use tools to retrieve live data. Never fabricate, guess, or invent data. Relay tool errors verbatim without adding suggestions.` | `You are an AI agent that helps employees manage and submit travel expenses. Help users with their requests.\n\nIMPORTANT: You MUST use tools to retrieve live data. Never fabricate, guess, or invent data. Relay tool errors verbatim without adding suggestions.` |
| `{{AGENT_EXAMPLES}}`          | Generate 2 example prompts based on description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | `["Help me submit a travel expense", "What are the expense policies?"]`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |



## Customization

- **Tools**: Extend LangGraph in `agent.py`
- **A2A skills**: Add `AgentSkill` definitions in `main.py`
- **Runtime skills**: Place skill markdown files in `app/skills/<skill-name>/SKILL.md` (with YAML frontmatter `name` and `description`) and optionally add other assets files. The agent will be able to access all skill related files via the exposed `load(path)` tool in `load_skill_resources.py`.

## ⚠️ Important: Dependencies

**Note:** Dependencies listed in `requirements.txt` are NOT installed during the bootstrap process. They will be installed:
- **In the cluster**: Automatically during the deployment process via CI/CD pipeline

The bootstrap process only creates the project structure and configuration files. No local Python environment setup is performed at this stage.

## ⚠️ Known Deployment Gotchas

These issues have caused real deployment failures and are proven to break the agent on the platform:

1. **`set_aicore_config()` and `auto_instrument()` must be first** — these must be called at the very top of `main.py`, before any AI framework imports (LangChain, LiteLLM, etc.). The platform SDK hooks into the import process; importing AI frameworks first causes telemetry to be missed or misconfigured.

2. **MCP tool loading via `get_mcp_tools()` must be async and lazy** — `get_mcp_tools()` is async and makes real network calls to the Agent Gateway. It cannot be called from `__init__()` and cannot be made sync. The correct pattern is:
   ```python
   from mcp_tools import get_mcp_tools

   async def _load_tools():
       return await get_mcp_tools()

   async def _get_graph(self):
       if self._graph is None:
           tools = await _load_tools()
           self._graph = create_agent(self.llm, tools=tools, system_prompt=get_system_prompt())
       return self._graph
   ```
   If MCP tools are loaded in `__init__()`, the HTTP server cannot start before the startup probe fires, causing the container to be killed.

3. **All imports inside `app/` must use peer-level style** — use `from matching import ...`, `from tools import ...`, never `from app.xxx import ...`.

## asset.yaml Gotchas
`asset.yaml` and `solution.yaml` creation are NOT part of this skill, and are the responsibility of the`setup-solution` skill.

## Next Steps

After bootstrapping completes, return control to the calling skill to continue implementation. Do not prompt the user with interactive options — this skill is only invoked as part of the automated `specification` skill.
