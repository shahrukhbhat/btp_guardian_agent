# BTP Guardian Agent — Project Specification & Current Status

_Last updated: 2026-07-23_

## 1. Overview

**BTP Guardian** is an AI-powered conversational agent that gives SAP BTP platform
teams and FinOps stakeholders natural-language, read-only visibility into their
cloud consumption, costs, account topology, entitlements, and governance posture.

It is an A2A (Agent-to-Agent) LangGraph agent that answers questions by calling
SAP BTP platform REST APIs as tools and synthesising the results.

- **Repo root:** `btp_guardian_agent/`
- **Deployable asset:** `assets/btp-guardian-agent/`
- **Agent card / A2A version:** `1.0.0`
- **Model:** SAP AI Core `gpt-4o` (temperature `0.0`), resolved at runtime.

## 2. Runtime Modes

The same codebase runs in three modes, selected by environment variables. This is
the single most important architectural fact about the project.

| Mode | Gate | Tool source | LLM / auth |
|------|------|-------------|-----------|
| **Cloud Foundry (production)** | `JOULE_RUNTIME` unset | Direct BTP REST clients (`api_client.py`) via the Destination Service | AI Core resolved from `aicore` destination |
| **Joule / Kyma** | `JOULE_RUNTIME=1` | MCP tools loaded from Agent Gateway over mTLS (`mcp_client.py`) | AI Core via `sap_cloud_sdk` |
| **Local / test** | `IBD_TESTING=1` | Mock MCP tools from `mcp-mock.json` | Real AI Core via `config.json` creds |

Gating rules to preserve:
- CF path must never import `sap_cloud_sdk` (not in CF `requirements.txt`).
- `IBD_TESTING=1` is only ever set locally (by `run_local.py`) — never in Joule/CF.
- Local-only changes must stay gated so deployed CF/Joule behaviour is byte-for-byte unchanged.

## 3. Architecture

### 3.1 Request flow (Cloud Foundry / production)

```
A2A client (Bruno / Joule)
        │  POST /  (A2A JSON-RPC)
        ▼
main.py  (A2AStarletteApplication, gunicorn + uvicorn worker)
        ▼
AgentExecutor.execute()          agent_executor.py
        ▼
BTPGuardianAgent.stream()        agent.py
        ▼
_run_agent() → LangGraph loop (model ⇄ tools)
        ▼
_build_domain_tools() → 13 StructuredTools
        ▼
api_client.Client.get()/.post()
        ▼
Destination Service (VCAP_SERVICES binding)
   - fetch OAuth token (client_credentials)
   - resolve destination config + injected authTokens[] bearer
        ▼
BTP platform REST APIs (*.cfapps.eu10.hana.ondemand.com)
```

### 3.2 Key modules (`assets/btp-guardian-agent/app/`)

- **`main.py`** — A2A server bootstrap. Builds the `AgentCard`, wires the
  `AgentExecutor`, instruments Starlette. Reads `PORT`/`HOST` from env. On
  `JOULE_RUNTIME=1` it runs the Joule telemetry/AI Core config block first.
- **`agent_executor.py`** — A2A `AgentExecutor`. Bridges A2A task lifecycle to
  the agent's `stream()`. Imports MCP tools only under `JOULE_RUNTIME`.
- **`agent.py`** — `BTPGuardianAgent`. Holds the system prompt (`@prompt_section`),
  model/config decorators, LangGraph construction, milestone (M1–M5) OpenTelemetry
  spans, and `_build_domain_tools()` (the 13 BTP tools). `_get_tools()` picks mock
  vs. domain tools based on `IBD_TESTING`.
- **`api_client.py`** — `Client` + `_DestinationResolver`. Resolves destinations
  from the `destination` VCAP binding, fetches OAuth tokens, and calls BTP REST
  APIs. Forwards the Destination Service's injected `authTokens[0].value` as
  `Authorization: Bearer` (required for `OAuth2ClientCredentials` destinations).
- **`aicore.py`** — Resolves AI Core credentials from the `aicore` destination and
  returns a LangChain chat model via `gen_ai_hub`.
- **`mcp_tools.py`** — MCP tool loader. `IBD_TESTING=1` → mock tools from
  `mcp-mock.json`; otherwise → Agent Gateway via `mcp_client.py`.
- **`mcp_client.py`** — mTLS MCP client for the Joule Agent Gateway.

### 3.3 Local dev harness (`run_local.py`, repo root)

- Requires Python 3.10+ (a2a-sdk). Use Homebrew `python3.13`; a `.venv` exists at
  repo root.
- Run: `uv run run_local.py 8080` (must pass a port — default 5000 collides with
  macOS Control Center / AirPlay).
- Sets `AICORE_*` from `config.json` and `IBD_TESTING=1` (→ mock MCP tools),
  then launches `main.py` with `PORT` in env.

### 3.4 Local chat UI (`.local-chat-ui/`, repo root)

A minimal browser chat front-end for demoing the agent locally. It talks to the
agent purely over HTTP via a local proxy and never touches the deployed CF/Joule
runtime, nor does it read any credentials (`config.json` stays with the agent
process only).

- **`index.html`** — self-contained chat UI (vanilla HTML/CSS/JS, no build step, no
  dependencies). Posts to the proxy's `/a2a` route using the A2A `message/stream`
  JSON-RPC method, parses the SSE stream, shows `working` status updates as a live
  progress line, then renders the final `agent_result` artifact. Reuses the returned
  `contextId` across turns to preserve conversation context.
- **`serve.py`** — tiny stdlib (`http.server`) proxy: serves `index.html` and
  forwards `POST /a2a` → the agent's A2A endpoint, streaming the SSE response straight
  back to the browser (avoids CORS, since Starlette/A2A sends no CORS headers).
- **Run (two terminals):**
  1. `uv run run_local.py 8080` — the agent.
  2. `python .local-chat-ui/serve.py 8000 8080` — UI proxy (`<ui-port> <agent-port>`,
     defaults 8000/8080). Open `http://localhost:8000`.
- **SSE note:** the agent frames SSE events with CRLF (`\r\n\r\n`) separators, so the
  client normalizes `\r\n`→`\n` before splitting on blank lines. The final answer
  arrives as an `artifact-update` event (`result.artifact.parts[].text`), not the last
  status message.

## 4. BTP Platform Tools (13)

All backed by direct REST calls in CF mode; each service has its own destination.

| Tool | API path | Destination |
|------|----------|-------------|
| `getGlobalAccount` | `GET /accounts/v1/globalAccount` | `BTP_ACCOUNTS` |
| `getSubaccounts` | `GET /accounts/v1/subaccounts` (`derivedAuthorizations=any`) | `BTP_ACCOUNTS` |
| `getDirectories` | `GET /accounts/v1/globalAccount?expand` | `BTP_ACCOUNTS` |
| `getGlobalAccountAssignments` | `GET /entitlements/v1/globalAccountAssignments` | `BTP_ENTITLEMENTS` |
| `getSubaccountAssignments` | `GET /entitlements/v1/assignments` | `BTP_ENTITLEMENTS` |
| `monthlySubaccountCmCosts` | `GET /odata/MonthlySubaccountCmCosts` | `BTP_RESOURCE_CONSUMPTION` |
| `monthlyUsage` | `GET /reports/v1/monthlyUsage` | `BTP_RESOURCE_CONSUMPTION` |
| `cloudCreditsDetails` | `GET /reports/v1/cloudCreditsDetails` | `BTP_RESOURCE_CONSUMPTION` |
| `GET_accounts-…-metrics` | `GET /accounts/{sa}/apps/{app}/metrics` | `BTP_METRICS` |
| `GET_accounts-…-state` | `GET /accounts/{sa}/apps/{app}/state` | `BTP_METRICS` |
| `get_usage-records` | `GET /usage-records` | `BTP_USAGE_RECORDS` |
| `getEnvironmentInstances` | `GET /provisioning/v1/environments` | `BTP_PROVISIONING` |
| `getAvailableEnvironments` | `GET /provisioning/v1/availableEnvironments` | `BTP_PROVISIONING` |

Pagination is capped at 100 (`MAX_PAGE_SIZE`) on tools that accept `$top`/`limit`.

## 5. Deployment (Cloud Foundry, eu10)

- **Manifest:** `manifest.yml` — 512M / 1G disk, 1 instance, python buildpack,
  gunicorn+uvicorn, health check on `/.well-known/agent.json`.
- **Bound service:** `proj-vector-destination-service` (Destination Service).
- **Destinations** (all `OAuth2ClientCredentials`, same clientid/secret/token URL
  from the cis-central `client_credentials` binding):

  | Destination | URL |
  |-------------|-----|
  | `BTP_ACCOUNTS` | `https://accounts-service.cfapps.eu10.hana.ondemand.com` |
  | `BTP_ENTITLEMENTS` | `https://entitlements-service.cfapps.eu10.hana.ondemand.com` |
  | `BTP_RESOURCE_CONSUMPTION` | `https://account-budgets-service.cfapps.eu10.hana.ondemand.com` |
  | `BTP_METRICS` | `https://account-budgets-service.cfapps.eu10.hana.ondemand.com` |
  | `BTP_USAGE_RECORDS` | `https://account-budgets-service.cfapps.eu10.hana.ondemand.com` |
  | `BTP_PROVISIONING` | `https://provisioning-service.cfapps.eu10.hana.ondemand.com` |
  | `aicore` | AI Core service URL (clientId/clientSecret/tokenServiceURL/AI-Resource-Group) |

- **Packaging:** `.cfignore` excludes `.venv`, `vendor/`, tests, coverage,
  Joule-only requirements, and metadata so the droplet stays small (~30 MB upload).
  `.bp-config/options.json` sets `DISABLE_PYTHON_VENDORING`.

## 6. Current Status

### Working
- ✅ Local run via `run_local.py 8080` — real AI Core LLM + mock MCP data.
- ✅ Local chat UI (`.local-chat-ui/`) — verified end-to-end in
  a browser against the local agent, including multi-turn `contextId` reuse.
- ✅ CF deployment packaging (clean `.cfignore`, small droplet).
- ✅ **BTP platform API calls succeed on CF** after two fixes:
  1. Destinations reconfigured to `OAuth2ClientCredentials` using the cis-central
     `client_credentials` binding credentials (the original cis service **key** used
     `grant_type: user_token`, which cannot mint admin-scoped tokens headlessly).
  2. `api_client.py` now forwards the Destination Service's injected
     `authTokens[0].value` as `Authorization: Bearer` (previously it only sent Basic
     auth and ignored `authTokens`, causing 401/403 → surfaced as 404s).
- ✅ Account topology, entitlements, consumption, and provisioning queries return
  real data.

### By design (not bugs)
- 🔒 Agent is **read-only** — the system prompt forbids write/modify operations, so
  requests like "create a subaccount" are declined. Enabling writes would require
  relaxing the prompt rule and adding write tools.

### Open / follow-ups
- ⏳ Confirm all six BTP_* destinations updated to `OAuth2ClientCredentials` (accounts
  verified working; entitlements/consumption/metrics/usage/provisioning to confirm
  end-to-end).
- ⏳ Metrics/Usage/Consumption share the `account-budgets-service` host — validate the
  exact paths return data for the target global account.

## 7. Constraints & Conventions

- Never make code changes that alter deployed CF/Joule runtime behaviour when the
  goal is local testing; keep local-only changes gated behind `IBD_TESTING`.
- All secrets (`config.json`, service keys) stay out of git (`.gitignore`).
- Exactly 3 decorated functions in `agent.py` (`@agent_model`, `@agent_config`,
  `@prompt_section`) per the bootstrap contract.
