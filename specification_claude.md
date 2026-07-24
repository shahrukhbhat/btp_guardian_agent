# BTP Guardian Agent — Project Specification & Current Status

_Last updated: 2026-07-24_

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
  back to the browser (avoids CORS, since Starlette/A2A sends no CORS headers). The
  upstream agent target is configurable (see below); `GET /whoami` returns the resolved
  target so the UI header can show local vs deployed.
- **Target selection.** The UI always runs locally, but can point at either agent:
  - **Local** (default): `python .local-chat-ui/serve.py 8000 8080` → `http://localhost:8080/`
    (requires the local agent via `run_local.py 8080`).
  - **Deployed CF** (real BTP data via Destination, no local agent needed):
    `python .local-chat-ui/serve.py 8000 --target https://btp-guardian-agent.cfapps.eu10.hana.ondemand.com/`
    or set `AGENT_URL=<cf-url>`. Precedence: `--target`/`--agent-url` flag > positional
    URL > `AGENT_URL` env > `http://localhost:<agent-port>/`.
  - The deployed route is recorded in `deploy_result.json`. The deployed A2A endpoint is
    currently **unauthenticated** — anyone with the URL can query real BTP data through
    it (fine for a demo; flag if it ever leaves demo use).
- **Run (local, two terminals):**
  1. `uv run run_local.py 8080` — the agent.
  2. `python .local-chat-ui/serve.py 8000 8080` — UI proxy (`<ui-port> <agent-port>`,
     defaults 8000/8080). Open `http://localhost:8000`.
- **SSE note:** the agent frames SSE events with CRLF (`\r\n\r\n`) separators, so the
  client normalizes `\r\n`→`\n` before splitting on blank lines. The final answer
  arrives as an `artifact-update` event (`result.artifact.parts[].text`), not the last
  status message. This contract is identical for the local and deployed agents (same
  A2A SDK).

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

### 4.1 Response shaping (summary/detail) — context-overflow guard

Every tool routes its result through `_shape_result()` (in `agent.py`) before returning
it to the LLM, instead of a blind `json.dumps(result)`. This prevents a single tool
payload from overflowing gpt-4o's 128K context (a real entitlements payload measured at
~1.5M tokens, which 400'd AI Core with `context_length_exceeded`).

- **Summary by default.** Entitlement tools (`getGlobalAccountAssignments`,
  `getSubaccountAssignments`) project each record to an allow-list of fields
  (`ENTITLEMENT_SUMMARY_FIELDS`) and replace heavy nested per-subaccount arrays with an
  integer `<key>Count`. Any list-valued field not in the allow-list is defensively
  counted too, so unknown/future heavy fields can't reintroduce overflow.
- **Drill-down on follow-up.** Both entitlement tools accept
  `detailLevel: "summary" | "detail"` (default `summary`). `detail` returns the raw
  record **only when a scope filter is also set** (`assignedServiceName` for global,
  `subaccountGUID` for subaccount) — i.e. `detail` is ignored unless `scoped`. `contextId`
  persistence lets the LLM issue the scoped detail call on the next turn.
- **Universal char-cap backstop.** All 13 tools are bounded by
  `MAX_TOOL_RESULT_CHARS` (default 24000, env `BTP_MAX_TOOL_RESULT_CHARS`). If a
  serialized result still exceeds the cap, the largest record list is truncated and a
  machine-readable `_truncated` note (or `[TRUNCATED]` marker for non-dict/unshrinkable
  payloads) is appended, instructing the model to narrow by service/subaccount/date.
- The system prompt (§7) documents this contract so the model summarizes, offers to drill
  down, and surfaces capped-data notes to the user.

### 4.2 API-spec conformance audit (2026-07-24)

Before the last `cf push`, all 13 tools were audited against the authoritative OpenAPI
specs in `specification/btp-guardian-agent/api-specs/` (verified directly against the JSON).
Six discrepancies were found and fixed in `agent.py`:

1. **Entitlements record keys.** The top-level response keys are `entitledServices[]` and
   `assignedServices[]` (NOT `assignments`). Both entitlement tools now shape
   `record_keys=["entitledServices", "assignedServices"]`. The heavy per-subaccount array
   is `assignedServices[].servicePlans[].assignmentInfo[]`; entitled plans carry heavy
   `dataCenters[]`/`resources[]`/`sourceEntitlements[]`. Fields `usedAmount`/`unitOfMeasure`
   do **not** exist on plans. **This was the critical bug** — the wrong key silently
   disabled summary shaping and would have reintroduced the 1.5M-token overflow.
2. **`monthlyUsage`** — `fromDate` & `toDate` are **required** integers, format `YYYYMM`
   (e.g. `202401`). Tool normalizes `YYYY-MM` → `YYYYMM` and sends integers.
3. **`get_usage-records`** — pagination is `pageSize` (default 16, capped 100) + `pageNumber`
   (1-based); there is no `limit` param. Response is a **bare array**, not `{value:[]}`.
4. **`getEnvironmentInstances`** — the spec has **no query params**; `subaccountGUID`
   filtering is done **client-side** over `environmentInstances[]`.
5. **`cloudCreditsDetails`** — `viewPhases` is a single enum (`CURRENT` default | `ALL`),
   not comma-separated.
6. **`getSubaccounts`** — response is `{value: [SubaccountResponseObject]}` with
   displayName/subdomain/guid; a `name` param does **client-side** case-insensitive
   substring match (resolves names like "coena" → GUID without misusing `labelSelector`,
   which must be a URL-encoded `key=value`).

Non-issues verified OK: entitlements `subaccountGUID` param; metrics `/metrics|/state`
paths; `MonthlySubaccountCmCosts` OData `$top/$skip/$filter/$select/$orderby/$count`.

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
- ✅ **Context-overflow fixed** via summary/detail response shaping (§4.1). Entitlement
  queries previously 400'd AI Core with `context_length_exceeded` (~1.5M-token payload);
  all 13 tools now route through `_shape_result` (summary-by-default + drill-down +
  universal char-cap backstop). Verified locally end-to-end against the real LLM:
  summary answer, scoped `detailLevel="detail"` drill-down, and topology regression all
  pass; unit-tested the backstop truncation + `_truncated` note.
- ✅ **API-spec conformance audit (§4.2)** — all 13 tools verified against the OpenAPI
  specs; 6 discrepancies fixed (most critically the entitlements
  `assignments`→`assignedServices` record-key bug that would have re-triggered the
  overflow). Re-verified locally end-to-end: entitlements summary (clean "not found" for a
  non-existent subaccount via client-side name match), drill-down, and topology regression
  all pass with no errors. **Not yet `cf push`-ed** — pending user go-ahead.

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
- ⏳ **Deployed verification of the overflow fix:** after `cf push`, re-run the coena
  entitlements query via `.local-chat-ui --target <CF url>` and confirm in `cf logs` that
  the AI Core `chat/completions` returns **200** (not 400 `context_length_exceeded`), plus
  a scoped drill-down follow-up.

## 7. Constraints & Conventions

- Never make code changes that alter deployed CF/Joule runtime behaviour when the
  goal is local testing; keep local-only changes gated behind `IBD_TESTING`.
- All secrets (`config.json`, service keys) stay out of git (`.gitignore`).
- Exactly 3 decorated functions in `agent.py` (`@agent_model`, `@agent_config`,
  `@prompt_section`) per the bootstrap contract.
