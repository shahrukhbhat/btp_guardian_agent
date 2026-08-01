# BTP Guardian Agent — Project Specification & Current Status

_Last updated: 2026-08-01 (session 4)_

> ## ⚡ Local vs. Deployed — read this first
>
> The codebase runs identically in all modes, but two recent demo-prep changes are
> **LOCAL-ONLY** and are **NOT** in the deployed/deployable artifacts. See §4.12 for the full
> matrix. In one line:
>
> | Concern | Local (`run_local.py`, this repo's `.venv`) | Deployable to BTP CF / SAP-managed runtime |
> |---------|---------------------------------------------|--------------------------------------------|
> | **LLM** | **Claude 4.6 Opus** (`anthropic--claude-4.6-opus`, Bedrock) | **gpt-4o** (`agent.py:67` default; `requirements.txt` still pins the old SDK) |
> | **AI SDK** | `sap-ai-sdk-gen[amazon,google]==7.2.0` (venv only) | `sap-ai-sdk-gen==6.6.0` (`requirements.txt`) |
> | **Cloud Foundry app-ops tools** | **13 CF v3 tools (mocked, stateful)** via `mcp-mock.json` | **None** — no real CF Controller client exists |
> | **Tool count** | **132 mock tools** (incl. CF) | **64 read-only / 119 with writes** (real REST clients, no CF) |
> | **Everything else** (accounts/entitlements/cost/usage/auth/audit) | Mocked | Real BTP REST clients — unchanged |
>
> Nothing above changes deployed CF/Joule behaviour: the CF tools only exist in the mock JSON
> (loaded solely under `IBD_TESTING=1`), and the Claude path in `aicore.py` is guarded to only
> trigger for `anthropic--*` model names — `gpt-4o` still flows through the stock `init_llm`.
> The uncommitted changes as of this update: `agent.py`, `aicore.py`, `mcp_tools.py`,
> `mcp-mock.json`, `run_local.py`.
>
> **Session 4 (2026-08-01) demo-prep, LOCAL-ONLY, uncommitted:** the CF "all apps in a space"
> answer was reshaped into a fleet table + Summary + hottest-app root-cause narrative (the SAP
> Joule mockup shape); the CF mock seed grew 3 → 8 apps with a tuned CPU spread; `_app_summary`
> now returns `cpu_pct` + running/total instances so the fleet table needs one call; and all
> mock user placeholders were renamed to the real team on `@sap.com`. See §4.11.

## 1. Overview

**BTP Guardian** is an AI-powered conversational agent that gives SAP BTP platform
teams and FinOps stakeholders natural-language visibility into their cloud consumption,
costs, account topology, entitlements, governance posture, and identity/access management.
By default the agent is **read-only** (demo guardrail); setting `BTP_ALLOW_WRITES=1`
unlocks full write/management capabilities for XSUAA Authorization, IdP Trust, Security
Settings, and User Management.

It is an A2A (Agent-to-Agent) LangGraph agent that answers questions by calling
SAP BTP platform REST APIs as tools and synthesising the results.

- **Repo root:** `btp_guardian_agent/`
- **Deployable asset:** `assets/btp-guardian-agent/`
- **Agent card / A2A version:** `1.0.0`
- **Model:** deployable default SAP AI Core `gpt-4o` (temperature `0.0`, resolved at
  runtime); **locally** overridden to Claude 4.6 Opus (see §4.12).

## 2. Runtime Modes

The same codebase runs in three modes, selected by environment variables. This is
the single most important architectural fact about the project.

| Mode | Gate | Tool source | LLM / auth |
|------|------|-------------|-----------|
| **Cloud Foundry (production)** | `JOULE_RUNTIME` unset | Direct BTP REST clients (`api_client.py`) via the Destination Service | AI Core `gpt-4o` resolved from `aicore` destination |
| **Joule / Kyma (SAP-managed)** | `JOULE_RUNTIME=1` | MCP tools loaded from Agent Gateway over mTLS (`mcp_client.py`) | AI Core `gpt-4o` via `sap_cloud_sdk` |
| **Local / test** | `IBD_TESTING=1` | Mock MCP tools from `mcp-mock.json` (incl. 13 CF tools) | Real AI Core via `config.json` creds; model = Claude 4.6 Opus (§4.12) |

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
_build_domain_tools() → 64–119 StructuredTools
  (64 read-only always [42 core + 22 auth/SCIM read]; +55 write tools when BTP_ALLOW_WRITES=1)
        ▼
api_client.Client.get()/.post()/.put()/.patch()/.delete()
        ▼
Destination Service (VCAP_SERVICES binding)
   - fetch OAuth token (client_credentials)
   - resolve destination config + injected authTokens[] bearer
        ▼
BTP platform REST APIs (*.cfapps.eu10.hana.ondemand.com,
                        api.authentication.eu10.hana.ondemand.com,
                        coena.authentication.eu10.hana.ondemand.com)
```

### 3.2 Key modules (`assets/btp-guardian-agent/app/`)

- **`main.py`** — A2A server bootstrap. Builds the `AgentCard`, wires the
  `AgentExecutor`, instruments Starlette. Reads `PORT`/`HOST` from env. On
  `JOULE_RUNTIME=1` it runs the Joule telemetry/AI Core config block first.
- **`agent_executor.py`** — A2A `AgentExecutor`. Bridges A2A task lifecycle to
  the agent's `stream()`. Imports MCP tools only under `JOULE_RUNTIME`.
- **`agent.py`** — `BTPGuardianAgent`. Holds the system prompt (`@prompt_section`),
  model/config decorators, LangGraph construction, milestone (M1–M5) OpenTelemetry
  spans, and `_build_domain_tools()` (14 read-only + up to 44 write tools gated by
  `BTP_ALLOW_WRITES`). `_get_tools()` picks mock vs. domain tools based on `IBD_TESTING`.
- **`api_client.py`** — `Client` + `_DestinationResolver`. Resolves destinations
  from the `destination` VCAP binding, fetches OAuth tokens, and calls BTP REST
  APIs. Forwards the Destination Service's injected `authTokens[0].value` as
  `Authorization: Bearer` (required for `OAuth2ClientCredentials` destinations).
  Implements `get`, `post`, `put`, `patch`, and `delete` — all with 401-refresh-retry
  (re-resolves a fresh bearer token once on 401, then re-raises on repeat failure).
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

### 3.5 Conversation memory (multi-turn)

The LangGraph is compiled with an in-process `MemorySaver` checkpointer, and `_run_agent`
threads `thread_id = <A2A context_id>` into `graph.ainvoke`. Only the new turn's
`HumanMessage` is sent each call — the checkpointer replays prior turns — and the
`SystemMessage` (system prompt + injected current date) is prepended **only on the first
turn** of a thread (detected via `graph.aget_state`).

- This keys off the A2A `context_id`, which is part of the protocol, so **every** A2A
  client (Joule, Bruno, the demo UI) gets multi-turn memory — not just the demo UI.
- Without this, `_run_agent` rebuilt messages from scratch every turn and ignored
  `context_id`, so the model treated each message in isolation (asked redundant
  "what are you looking for?" follow-ups, re-resolved the same subaccount repeatedly).
- **Production caveat:** `MemorySaver` is in-process — memory is lost on app restart and
  is **not shared across instances**. Fine for the current single-instance deployment,
  but if the app scales to multiple instances, follow-up turns could land on an instance
  without the thread's history. For durable/multi-instance memory, swap `MemorySaver` for
  a persistent checkpointer (e.g. SQLite/Postgres/Redis-backed). No new dependency today:
  `MemorySaver` ships inside the pinned `langgraph==1.1.9`.

## 4. BTP Platform Tools

### 4.0 Write-capability gate (`BTP_ALLOW_WRITES`)

`ALLOW_WRITES = os.environ.get("BTP_ALLOW_WRITES", "").lower() in ("1", "true", "yes")`

- **`BTP_ALLOW_WRITES=0`** (default, manifest default): **64 read-only tools** registered
  (42 core read tools + 22 Authorization/SCIM read tools).
  System prompt explicitly states the agent is read-only; write requests are declined with
  an explanation of what the operation would do.
- **`BTP_ALLOW_WRITES=1`**: **119 tools** registered (64 read + 55 write). System prompt
  switches to write-enabled policy. Destructive operations (delete, unassign) require the
  user to confirm with an explicit "yes" before execution.
- `authorization_client` and `scim_client` are **always instantiated** (fixed 2026-07-29
  session 2 — previously they were mistakenly gated behind `ALLOW_WRITES`, which caused all
  Authorization/SCIM read tools to be silently absent in read-only mode).
- Write tools (create/update/delete operations) remain gated by `ALLOW_WRITES`.

> **Tool-count reconciliation (deployed vs local).** The 64/119 counts above are the
> **deployable** tool set (real BTP REST clients, `_build_domain_tools`). The **local mock**
> (`mcp-mock.json`, `IBD_TESTING=1`) exposes **132 tools** because it additionally includes
> the 13 mocked Cloud Foundry app-ops tools (§4.11) and mirrors every read/write REST tool as
> a mock. The CF tools have **no** real-client counterpart, so they exist only locally.

### 4.1 Read-only tools (42 core — always registered)

All backed by direct REST calls in CF mode; each service has its own destination.

| Tool | API path | Destination |
|------|----------|-------------|
| `getGlobalAccount` | `GET /accounts/v1/globalAccount` | `BTP_ACCOUNTS` |
| `getSubaccounts` | `GET /accounts/v1/subaccounts` (`derivedAuthorizations=any`) | `BTP_ACCOUNTS` |
| `getSubaccount` | `GET /accounts/v1/subaccounts/{subaccountGUID}` | `BTP_ACCOUNTS` |
| `getDirectories` | `GET /accounts/v1/globalAccount?expand` | `BTP_ACCOUNTS` |
| `getDirectory` | `GET /accounts/v1/directories/{directoryGUID}` | `BTP_ACCOUNTS` |
| `getSubaccountLabels` | `GET /accounts/v1/subaccounts/{subaccountGUID}/labels` | `BTP_ACCOUNTS` |
| `getSubaccountSettings` | `GET /accounts/v1/subaccounts/{subaccountGUID}/settings` | `BTP_ACCOUNTS` |
| `getServiceManagementBinding` | `GET /accounts/v1/subaccounts/{subaccountGUID}/serviceManagementBinding` | `BTP_ACCOUNTS` |
| `getAllServiceManagerBindingsV2` | `GET /accounts/v2/subaccounts/{subaccountGUID}/serviceManagerBindings` | `BTP_ACCOUNTS` |
| `getServiceManagerBindingV2` | `GET /accounts/v2/subaccounts/{subaccountGUID}/serviceManagerBindings/{bindingName}` | `BTP_ACCOUNTS` |
| `getDirectoryLabels` | `GET /accounts/v1/directories/{directoryGUID}/labels` | `BTP_ACCOUNTS` |
| `getDirectorySettings` | `GET /accounts/v1/directories/{directoryGUID}/settings` | `BTP_ACCOUNTS` |
| `getJobStatus` | `GET /accounts/v1/jobs/{jobID}` | `BTP_ACCOUNTS` |
| `getGlobalAccountAssignments` | `GET /entitlements/v1/globalAccountAssignments` | `BTP_ENTITLEMENTS` |
| `getSubaccountAssignments` | `GET /entitlements/v1/assignments` | `BTP_ENTITLEMENTS` |
| `getAllowedDataCenters` | `GET /entitlements/v1/datacenters` | `BTP_ENTITLEMENTS` |
| `monthlySubaccountCmCosts` | `GET /reports/v1/monthlySubaccountsCost` | `BTP_RESOURCE_CONSUMPTION` |
| `monthlyUsage` | `GET /reports/v1/monthlyUsage` | `BTP_RESOURCE_CONSUMPTION` |
| `cloudCreditsDetails` | `GET /reports/v1/cloudCreditsDetails` | `BTP_RESOURCE_CONSUMPTION` |
| `monthlyDirectoryUsage` | `GET /reports/v1/monthlyDirectoryUsage` | `BTP_RESOURCE_CONSUMPTION` |
| `dailySubaccountUsage` | `GET /reports/v1/dailySubaccountUsage` | `BTP_RESOURCE_CONSUMPTION` |
| `GET_accounts-…-metrics` | `GET /accounts/{sa}/apps/{app}/metrics` | `BTP_METRICS` |
| `GET_accounts-…-state` | `GET /accounts/{sa}/apps/{app}/state` | `BTP_METRICS` |
| _(+9 more metrics paths)_ | app/process/dbsystem/html5/instance metrics & state | `BTP_METRICS` |
| `get_usage-records` | `GET /usage-records` | `BTP_USAGE_RECORDS` |
| `getEnvironmentInstances` | `GET /provisioning/v1/environments` | `BTP_PROVISIONING` |
| `getEnvironmentInstance` | `GET /provisioning/v1/environments/{environmentInstanceID}` | `BTP_PROVISIONING` |
| `getAvailableEnvironments` | `GET /provisioning/v1/availableEnvironments` | `BTP_PROVISIONING` |
| `getEnvironmentInstanceBindings` | `GET /provisioning/v1/environments/{id}/bindings` | `BTP_PROVISIONING` |
| `getEnvironmentInstanceBinding` | `GET /provisioning/v1/environments/{id}/bindings/{bindingID}` | `BTP_PROVISIONING` |
| `getEnvironmentInstanceLabels` | `GET /provisioning/v1/environments/{id}/labels` | `BTP_PROVISIONING` |
| `getSubaccountQuota` | `GET /provisioning/v1/subaccounts/{subaccountGUID}/quota` | `BTP_PROVISIONING` |
| `getAuditLogRecords` | `GET /auditlog/v2/auditlogrecords` | `BTP_AUDIT_LOGS` |

Pagination is capped at 100 (`MAX_PAGE_SIZE`) on tools that accept `$top`/`limit`.

### 4.2 Authorization & SCIM read tools (22 — always registered)

These use the `BTP_AUTHORIZATION` and `BTP_SCIM` destinations but are **read-only** and
always registered regardless of `BTP_ALLOW_WRITES`. They were incorrectly gated behind
`ALLOW_WRITES` previously (fixed 2026-07-29 session 2).

**`BTP_AUTHORIZATION` destination** (URL: `https://api.authentication.eu10.hana.ondemand.com`):

| Tool | Method + Path |
|------|--------------|
| `getXsuaaApps` | `GET /sap/rest/authorization/v2/apps` |
| `getXsuaaApp` | `GET /sap/rest/authorization/v2/apps/{appId}` |
| `getXsuaaAppScopes` | `GET /sap/rest/authorization/v2/apps/{appId}/scopes[/{scopeName}]` |
| `getXsuaaAppAuthorities` | `GET /sap/rest/authorization/v2/apps/{appId}/authorities/{grantedAppId}` |
| `getOwnXsuaaApp` | `GET /sap/rest/authorization/v2/apps/own[?includeUsage=true]` |
| `getRoleCollections` | `GET /sap/rest/authorization/v2/rolecollections` |
| `getRoleCollection` | `GET /sap/rest/authorization/v2/rolecollections/{name}` |
| `getRoleCollectionRoles` | `GET /sap/rest/authorization/v2/rolecollections/{name}/roles` |
| `getRoleCollectionsByRole` | `GET /sap/rest/authorization/v2/rolecollections?roleName=…&appId=…` |
| `getXsuaaRoles` | `GET /sap/rest/authorization/v2/roles[?appId=…]` |
| `getXsuaaRole` | `GET /sap/rest/authorization/v2/apps/{appId}/roles/{templateName}/{roleName}` |
| `getRoleTemplates` | `GET /sap/rest/authorization/v2/apps/{appId}/roletemplates[/{templateName}]` |
| `getAttributeMappings` | `GET /sap/rest/authorization/v2/rolecollections/{name}/attributeMappings/{idpOrigin}` |
| `getIdentityProviders` | `GET /sap/rest/identity-providers` |
| `getIdentityProvider` | `GET /sap/rest/identity-providers/{idpOrigin}` |
| `getIasTenants` | `GET /sap/rest/identity-providers/ias` |
| `getSecuritySettings` | `GET /sap/rest/authorization/v2/securitySettings` |
| `getTrustedDomains` | `GET /sap/rest/trusted-domains` |

**`BTP_SCIM` destination** (URL: `https://coena.authentication.eu10.hana.ondemand.com`):

| Tool | Method + Path |
|------|--------------|
| `getSCIMGroups` | `GET /scim/Groups[?filter=…&count=…&startIndex=…]` |
| `getSCIMGroup` | `GET /scim/Groups/{groupId}` |
| `getSCIMUsers` | `GET /scim/Users[?filter=…&count=…&startIndex=…]` |
| `getSCIMUser` | `GET /scim/Users/{userId}` |

### 4.3 Write tools (55 — registered only when `BTP_ALLOW_WRITES=1`)

**`BTP_AUTHORIZATION` destination** — Role Collections, Roles, Attribute Mapping, IdP, Security Settings:

| Tool | Method + Path |
|------|--------------|
| `createRoleCollection` | `POST /sap/rest/authorization/v2/rolecollections` |
| `updateRoleCollection` | `PUT /sap/rest/authorization/v2/rolecollections/{name}` |
| `deleteRoleCollection` | `DELETE /sap/rest/authorization/v2/rolecollections/{name}` |
| `assignRoleToRoleCollection` | `PUT /sap/rest/authorization/v2/rolecollections/{name}/roles/{appId}/{roleName}/{templateName}` |
| `unassignRoleFromRoleCollection` | `DELETE /sap/rest/authorization/v2/rolecollections/{name}/roles/…` |
| `createXsuaaRole` | `POST /sap/rest/authorization/v2/apps/roles` |
| `updateXsuaaRole` | `PUT /sap/rest/authorization/v2/apps/{appId}/roles/{templateName}/{roleName}` |
| `deleteXsuaaRole` | `DELETE /sap/rest/authorization/v2/apps/{appId}/roles/{templateName}/{roleName}` |
| `createAttributeMapping` | `POST /sap/rest/authorization/v2/rolecollections/{name}/attributeMappings` |
| `deleteAttributeMapping` | `DELETE /sap/rest/authorization/v2/rolecollections/{name}/attributeMappings/…` |
| `createIdentityProvider` | `POST /sap/rest/identity-providers` |
| `updateIdentityProvider` | `PUT /sap/rest/identity-providers/{idpOrigin}` |
| `deleteIdentityProvider` | `DELETE /sap/rest/identity-providers/{idpOrigin}` |
| `updateSecuritySettings` | `PATCH /sap/rest/authorization/v2/securitySettings` |
| `triggerKeyRotation` | `POST /sap/rest/authorization/v2/securitySettings/rotate` |

**`BTP_SCIM` destination** — SCIM write + Accounts write:

| Tool | Method + Path |
|------|--------------|
| `createSCIMGroup` | `POST /scim/Groups` |
| `updateSCIMGroup` | `PUT /scim/Groups/{groupId}` |
| `patchSCIMGroup` | `PATCH /scim/Groups/{groupId}` |
| `createShadowUser` | `POST /scim/Users` |
| `updateShadowUser` | `PUT /scim/Users/{userId}` |
| `patchShadowUser` | `PATCH /scim/Users/{userId}` |
| `deleteShadowUser` | `DELETE /scim/Users/{userId}` |

Plus ~33 Accounts/Entitlements/Provisioning write tools (createSubaccount, deleteSubaccount, moveSubaccount, createDirectory, etc.) all gated by `ALLOW_WRITES`.

### 4.4 Response shaping (summary/detail) — context-overflow guard

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
- **Universal char-cap backstop.** All tools are bounded by
  `MAX_TOOL_RESULT_CHARS` (default **80000**, env `BTP_MAX_TOOL_RESULT_CHARS`). gpt-4o's
  128K context is ~512K chars; after reserving room for the system prompt, tool schemas,
  growing multi-turn history, and output, ~80K is safe for one tool result — enough to fit
  a full summarized large-GA entitlements payload (~50–70K) without truncation, while still
  catching pathological raw payloads (the ~1.5M-token dump is ~6M chars). **Was 24000**,
  which was over-conservative: it truncated even normal summaries (a realistic 60-service GA
  is ~29K chars), so the model reported "capped" on nearly every entitlement/usage query.
- **Proportional truncation.** If a serialized result still exceeds the cap, the largest
  record list is trimmed: the backstop estimates how many records fit (from the fit ratio)
  and shrinks by 10% steps, keeping far more records than the old blind halving
  (~121/200 vs ~8/200 in a worst case). A machine-readable `_truncated` note (or a
  `[TRUNCATED]` marker for non-dict/unshrinkable payloads) is appended, telling the model
  to narrow by service/subaccount/date.
- The system prompt (§7) documents this contract so the model summarizes, offers to drill
  down, and surfaces capped-data notes to the user.

### 4.5 API-spec conformance audit (2026-07-24)

Before the last `cf push`, all 14 read-only tools were audited against the authoritative OpenAPI
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
paths.

### 4.6 Post-deploy runtime fixes (2026-07-24, verified against real CF data)

After the audit push, live testing via `.local-chat-ui --target <CF url>` surfaced four
more issues (three real bugs, one UX). All fixed in `agent.py`; **not yet `cf push`-ed**.

1. **Cost tool endpoint switched.** `monthlySubaccountCmCosts` now calls
   `GET /reports/v1/monthlySubaccountsCost` with **required `fromDate`/`toDate` (YYYYMM)**,
   not the OData `/odata/MonthlySubaccountCmCosts`. Root cause: the cost entity has no
   `billingPeriod` field (the period column is `reportYearMonth`), so the LLM's invented
   `$filter=billingPeriod eq '...'` returned **400 Bad Request**. The `monthlySubaccountsCost`
   report is purpose-built ("monthly cost for all subaccounts"), takes YYYYMM directly (no
   fragile hand-built `$filter`), wraps rows under `content[]`, and carries richer fields
   (`cost`, `currency`, `paygCost`, `cloudCreditsCost`, `reportYearMonth`, `subaccountName`).
   An optional `subaccountName` narrows client-side. **Note:** the UAS Reporting Service
   host (`uas-reporting.cfapps.eu10...`) serves all `/reports/v1/*` + `/odata/*` consumption
   endpoints; the `BTP_RESOURCE_CONSUMPTION` destination must point there with a credential
   authorized for cost reporting (the cis-central creds returned **403** on the cost
   endpoint; the **Usage Data Management Service** service key resolves it).
2. **Current-date injection.** `_run_agent` appends a `## Current date` block
   (`Today is YYYY-MM-DD (UTC). Current month as YYYYMM: YYYYMM.`) to the system prompt at
   runtime, and a prompt rule tells the model to resolve "this month"/"last month" from it
   as YYYYMM. Root cause: the LLM was hallucinating stale dates (queried `202310` for "this
   month" when today is 2026-07).
3. **Empty-cost UX.** A prompt rule: when a cost tool returns no rows, explain the account
   may be a subscription/commitment model with no consumption-based cost and offer the
   `monthlyUsage` report instead — rather than a bare "no data." Confirmed real for this GA:
   cost endpoints return empty, but `monthlyUsage` returns 278 usage records for the period.
4. **Char-cap raised + proportional trim** (§4.5) — the "capped on every query" complaint.

A fifth fix followed on 2026-07-25 in `api_client.py`:

5. **401 retry-once with token refresh.** `Client.get`/`post` now retry a request **once**
   on a **401** after invalidating the cached destination + shared xsuaa token
   (`_DestinationResolver.invalidate` + `Client._refresh_destination`). Root cause: the
   Destination-Service-injected bearer token is cached (`DEST_CACHE_TTL=300s`,
   `TOKEN_TTL=600s`) and can expire *inside* the cache window, so the first call after
   expiry got a **401** from the target API (observed on `getGlobalAccount` →
   `accounts-service` on 2026-07-25; a `cf restart` — which clears the cache — fixed it,
   confirming a stale token, not a bad credential). The retry re-resolves a fresh token and
   self-heals without a restart. Bounded to one retry and only on 401 (403/5xx are not
   retried), so a genuinely bad credential still surfaces as an error.

### 4.7 Conversation memory

See §3.5 — the LangGraph now uses an in-process `MemorySaver` checkpointer keyed by the
A2A `context_id`, so multi-turn follow-ups ("what about last month", "who are these assigned
to") retain context instead of being answered in isolation.

### 4.8 Message windowing (2026-07-29 session 2)

`call_model` now trims the LangGraph message history before each LLM call via
`_trim_messages()` — capped at `MAX_HISTORY_MESSAGES` (default **40**, env
`BTP_MAX_HISTORY_MESSAGES`) non-system messages. Trimming only happens at `HumanMessage`
boundaries so tool call/result pairs are never orphaned.

**Root cause fixed:** a query like "who has admin roles in coena?" caused the agent to loop
calling `getSubaccountAssignments` ~5 times (no authorization tools were available in the
deployed app at the time), accumulating entitlements payloads until the total reached
**148,644 tokens** — exceeding GPT-4o's 128K limit and throwing `context_length_exceeded`.
The windowing ensures this cannot recur regardless of how many tool calls a turn makes.

### 4.9 PRD gap analysis (2026-07-29)

Audit of the current implementation against `product-requirements-document.md`
(dated 2026-05-19). All 58 tools (14 read-only + 44 write) in `agent.py`, the milestone
helpers, and the proactive-monitor / extensibility claims were checked.

**Requirements R1–R6:**

| Req | PRD APIs | Status | Gap |
|-----|----------|--------|-----|
| **R1** Account topology | Accounts + Provisioning | ✅ Met | `getGlobalAccount`, `getSubaccounts`, `getDirectories`, `getEnvironmentInstances`, `getAvailableEnvironments` (Provisioning creds broken — see §6). |
| **R2** Consumption & cost | Consumption + Resource Consumption + Usage Records | ✅ Met | `monthlySubaccountCmCosts`, `monthlyUsage`, `cloudCreditsDetails`, `get_usage-records`. |
| **R3** Entitlement utilization | Entitlements + **Entitlement Consumptions API** | ⚠️ Partial | Assigned-quota tools exist (`getGlobalAccountAssignments` / `getSubaccountAssignments`), but there is **no Entitlement Consumptions tool**, so the used/assigned ratio the AC requires cannot be computed. Plan objects have no `usedAmount` field (per §4.4), so "over-provisioned" cannot be answered as specified. |
| **R4** Governance posture | **Checks API + Monitor Log API** | ⚠️ Partial | `getAuditLogRecords` (§4.8) provides audit log retrieval with notable-event classification. Still missing: Checks API / Monitor Log API tools. |
| **R5** Proactive alerting | Metrics + **Alerting Channels API** | ❌ Missing | Reactive per-app `get_app_metrics` / `get_app_state` only. No Alerting Channels tool, no background monitor, no threshold emission. |
| **R6** Access/identity governance | **Platform Authorization Management API** | ✅ Met (read tools always on; write tools when `BTP_ALLOW_WRITES=1`) | 22 read tools (getRoleCollections, getSCIMGroups, getIdentityProviders, etc.) always registered. 33 additional write tools gated. SCIM `/scim/Groups` currently returns **403** — see §6 known issues. |

**Milestone logging (M1–M5):** Helper methods `milestone_account_topology` …
`milestone_proactive_alert` exist and emit the exact `[MID].[achieved|missed]` pattern
under OTel spans — **but they are never called** anywhere in the reasoning loop, so the
milestone logs never fire. Dead scaffolding until wired into the tool/answer path.

**Proactive Monitor:** ❌ Not implemented. The agent is purely request/response A2A —
no background polling task, no threshold loop. The `COST_ALERT_PCT=80` /
`ENTITLEMENT_ALERT_PCT=85` constants are defined but unused.

**Extensibility layer:** ❌ Not implemented. Tools are hard-coded in
`_build_domain_tools`; there is no registry/plugin mechanism for adding tools without
editing core code.

**Coverage vs. PRD claim:** the PRD claims 17 wrapped APIs ("all BTP platform APIs");
the implementation wraps 13 APIs across 14 read-only tools + 4 additional APIs (Authorization,
IdP Management, Security Settings, SCIM) across 44 write tools. Service Manager and Events
Service (listed as integration points) still have no tools.

**Correctly implemented guardrail:** the read-only constraint is enforced via the system
prompt by default; `BTP_ALLOW_WRITES=1` switches to write-enabled policy.

**Bottom line:** R1–R2 solid; R3 partial; **R4 partially addressed** (audit log tool now
wired, see §4.8); **R5 unbuilt**; **R6 fully implemented** (write tools, gated by
`BTP_ALLOW_WRITES`); milestone logging and the proactive monitor are scaffolded but inert.

### 4.10 Audit Log Retrieval tool (2026-07-25)

Adds `getAuditLogRecords` (tool #14) to close part of the **PRD R4 governance gap**.

**API:** Audit Log Retrieval API (CF environment) — `GET /auditlog/v2/auditlogrecords`.
**Destination:** `BTP_AUDIT_LOGS` (`OAuth2ClientCredentials`, `auditlog-management`
service key — `url` + `uaa.clientid/clientsecret/url`). **Scope:** subaccount-level
(the service key is bound to the "coe na" subaccount).

**Parameters:** `timeFrom` / `timeTo` (ISO-8601, required), `category` (one of
`audit.security-events` | `audit.configuration` | `audit.data-access` |
`audit.data-modification`, optional), `surfaceNotable` (bool, default `False`),
`pageSize` / `pageNumber` (pagination, max 100).

**Two response modes:**
- **Filtered query** (`surfaceNotable=False`, default): returns all matching records as a
  **markdown table** (Time / Category / User / Object / Change columns).
- **Notable events** (`surfaceNotable=True`): classifies records by severity and returns
  a structured markdown summary:
  - 🔴 **Critical** — `audit.security-events` (login failures, token revocations)
  - 🟡 **Warning** — `audit.configuration` records whose changed attribute names match
    role/trust/binding/permission/scope/credential keywords
  - ℹ️ **Info** — all other records (first 10 shown)

**System prompt rule:** default to last 7 days if no time range given; use
`surfaceNotable=True` when user asks for "all" logs or a governance/security overview;
present the returned markdown as-is.

**Helpers added (module-level in `agent.py`):** `_format_audit_log_markdown()` and
`_surface_notable_audit_events()` — plain functions, not tools, not decorated.

**Mock data:** 8 records in `mcp-mock.json` `"audit-logs"` server across all 4
categories (2 security, 3 configuration incl. role binding + trust change, 2 data-access,
1 data-modification) — both response paths exercisable locally with `IBD_TESTING=1`.

**BTP-side prerequisite (user action):** create `BTP_AUDIT_LOGS` destination in the
Destination Service pointing to the existing `auditlog-management` service key.
Not yet `cf push`-ed.

### 4.11 Cloud Foundry app-ops tools (2026-07-30) — **LOCAL MOCK ONLY**

Added a 10th mock server `cf-controller` to `mcp-mock.json` so the demo agent can show and
manage apps deployed in a CF runtime (list apps, check CPU/memory, tail logs, view events,
start/stop/restart, scale, deploy). Modeled on the **CF v3 Cloud Controller REST API**
(`{pagination:{total_results}, resources:[...]}` shapes).

**⚠️ This is mock-only and has NO deployable counterpart.** There is no real CF Cloud
Controller client in `api_client.py`; `_build_domain_tools()` does not register these. They
load only under `IBD_TESTING=1`. A deployed BTP CF / SAP-managed agent will **not** have any
CF app-ops capability today — building that would require a real CF API client + destination
+ OAuth, which has not been done.

**13 tools** (`mcp_tools.py` `_cf_handlers`, backed by the stateful in-memory `_CfAppStore`):

| Tool | CF v3 shape | Read/Write |
|------|-------------|-----------|
| `getCfOrganizations` | `GET /v3/organizations` | read |
| `getCfSpaces` | `GET /v3/spaces` (by `organizationGuid`) | read |
| `getCfApps` | `GET /v3/apps` (by `spaceGuid`/`name`) | read |
| `getCfApp` | `GET /v3/apps/{guid}` | read |
| `getCfAppProcesses` | `GET /v3/apps/{guid}/processes` | read |
| `getCfAppProcessStats` | `GET /v3/processes/{type}/stats` (CPU/mem/disk per instance) | read |
| `getCfAppRecentLogs` | recent log lines (by `limit`) | read |
| `getCfAppEvents` | `GET /v3/audit_events` for the app | read |
| `startCfApp` | `POST /v3/apps/{guid}/actions/start` | **write** |
| `stopCfApp` | `POST /v3/apps/{guid}/actions/stop` | **write** |
| `restartCfApp` | restart action | **write** |
| `scaleCfAppProcess` | `POST /v3/processes/{type}/actions/scale` (instances/mem/disk) | **write** |
| `createCfAppDeployment` | `POST /v3/deployments` (metadata-only, bumps `version`) | **write** |

**Stateful mock:** `_CfAppStore` (in `mcp_tools.py`) is seeded from
`servers["cf-controller"]["_seed"]` and mutates in-process — start/stop flips `state`, scale
rebuilds per-instance web stats to the new instance count and persists, deploy bumps
`current_version`. Mutations survive across turns within one agent process (lost on restart).

**`_app_summary()` carries CPU + instances (session 4).** The list/get shape returned by
`getCfApps`/`getCfApp` now includes three derived fields computed from `app["stats"]["web"]`:
`cpu_pct` (average of per-instance `usage.cpu_pct`, 1dp, `None` if no stats),
`running_instances` (count of web stats in `RUNNING`), and `total_instances` (= `instances`).
This lets a **fleet CPU table for a whole space come back in ONE `getCfApps` call** — no
per-app `getCfAppProcessStats` fan-out. Minor, deliberate deviation from strict CF v3 (which
exposes CPU only under per-process stats) — acceptable because it's a mock.

**Seed topology (session 4 — expanded 3 → 8 apps):** 1 org (`org-guardian-0001`/guardian-ga),
1 space (`space-guardian-prod`/prod). The CPU spread is tuned so the "all apps in prod"
question yields a realistic fleet: **avg ≈ 43.4%**, **highest guardian-api 92.3%**, **2 apps
>80%** (guardian-api 🔴 + guardian-workflow-svc ⚠️). Invariant per app:
`instances == len(stats.web) == processes[0].instances`.

| App | guid slug | Instances | Avg CPU% | Badge | Role |
|-----|-----------|-----------|----------|-------|------|
| **guardian-api** (HERO/hot) | `app-guardian-api-0001` | 3/3 | 92.3 (93/91.5/92.4) | 🔴 | v2.8.1; logs show event-loop lag / GC pause / queue depth / upstream timeout; deploy event 27 min ago. Grounds the root-cause story. |
| guardian-ui | `app-guardian-ui-0002` | 1/1 | 12.0 | — | healthy, v1.9.0 (retuned from 2 inst → 1 this session) |
| guardian-xs-app | `app-guardian-xs-0003` | 1/1 | 26.3 | — | healthy, v3.2.1 |
| guardian-orders-api | `app-guardian-orders-0004` | 2/2 | 18.0 | — | NEW this session |
| guardian-catalog-svc | `app-guardian-catalog-0005` | 1/1 | 42.0 | — | NEW this session |
| guardian-inventory-api | `app-guardian-inventory-0006` | 2/2 | 67.0 | — | NEW this session |
| guardian-workflow-svc | `app-guardian-workflow-0007` | 2/2 | 81.0 (82/80) | ⚠️ | NEW — elevated-but-running, no incident story |
| guardian-notify-api | `app-guardian-notify-0008` | 1/1 | 9.0 | — | NEW this session |

**System prompt (session 4 — fleet-view rule added to "## Cloud Foundry apps"):** in addition
to the app-NAME → GUID resolution rule, the block now instructs the agent, for a "CPU/health
for ALL apps in space X" query, to call `getCfApps` **once** and render **one row per app**
(Application | Status | CPU% | Instances running/total) with **🔴 ≥90 / ⚠️ ≥80** badges, then a
**Summary** (total apps, average CPU, highest name + %, count >80%), then — for the SINGLE
hottest app only, if its metrics payload carries trend/deploy/baseline/autoscaling fields — the
root-cause narrative + actions + offer to execute (per the §Health & metrics grounding rules). A
fleet with nothing >80% gets a brief "all healthy", never a manufactured incident. **Prompt
budget:** adding this rule pushed the write-enabled `get_system_prompt()` render over the 8000
`max_length`; it was offset by condensing several existing bullets (Health & metrics, write
policy ENTITLEMENTS paragraph, Summary-vs-detail, cost/subaccount/XSUAA bullets). Final render
measured **7979 / 8000** write-enabled, **7537** read-only (re-measure with the ast char-count
snippet after ANY prompt edit — this is a hard gate).

**Observed result quality (session 4).** Running locally on **Claude 4.6 Opus**, the "all apps
in prod" query renders the fleet table + Summary + hottest-app narrative cleanly; a focused
"why is guardian-api CPU so high?" query pulls the full grounded incident story (deploy v2.8.1
correlation, heap/GC/event-loop/queue/upstream-timeout log evidence) + a confirm-gated scale
offer. **What drove the improvement is two things, not just the model:** (1) the model swap
(gpt-4o → Claude) does the *presentation* lift — markdown tables, the observe→reason→root-cause
→recommend→offer structure, and the "ground every claim in a present field" discipline; (2) the
prompt rules + the richer mock (8-app spread, `_app_summary` CPU, grounded hero logs/deploy) do
the *substance* lift and would benefit gpt-4o too. Note: **gpt-4o cannot run this build locally
at all** — 132 mock tools exceed its 128-tool array cap (400 `array_above_max_length`), which is
exactly why local runs on Claude via Bedrock. If demoing against the *deployed* runtime (still
gpt-4o, and with no CF tools), you get gpt-4o's rendering — plan the demo from local 8080.

**Mock user identities renamed to real team (session 4).** The placeholder users
(`alice`/`bob`/`carol`/`dave`/`erin`/`frank`/`michael.chen`/`john.doe`/`jane.doe`/`admin` @
`example.com`) were replaced across `mcp-mock.json` with 10 real team members on the
**`@sap.com`** domain (Shahrukh Bhat, Archit Goel, Ashok Kumar, Brady Zhou, Sead David, Aarya
Deshmukh, Haibo Huang, Robby Mains, Jarod Durkin, Victor Chan) — emails updated across
`userName`/`value`/`display`/`id`/`createdBy`/`assignedBy`/`user:` fields plus paired
`givenName`/`familyName`. Service accounts moved to `@sap.com` too but kept as service accounts
(`svc-automation`, `svc-account`, `monitor`). One human placeholder (`developer@example.com`) was
left untouched by design (only 10 of 11 slots replaced), so "Anthony Siluk" is not yet used.
Haibo/Robby/Jarod appear only in audit/event-log entries (no user-record blocks), so they show
in audit-log answers but not in a "list subaccount users" table.

### 4.12 Local vs. deployable state (model, SDK, CF tools)

This section is the authoritative record of what diverges between **local demo** and the
**deployable artifact** as of 2026-08-01. All divergences are demo-prep; the deployed
CF/Joule runtime is byte-for-byte unaffected.

### 4.12.1 What is available LOCALLY only (this repo's `.venv` + `run_local.py`)

- **Claude 4.6 Opus** as the LLM (`AGENT_LLM_MODEL=anthropic--claude-4.6-opus`, set in
  `run_local.py`). Routes via `ChatBedrock`, which has **no 128-tool cap** — required because
  the 132 mock tools exceed gpt-4o's hard 128-tool array limit (gpt-4o 400s with
  `array_above_max_length`).
- **`sap-ai-sdk-gen[amazon,google]==7.2.0`** installed in the venv (pulls `langchain-aws`,
  `boto3`, `langchain-google-genai`, etc.; also bumped langchain→1.3.14 / langgraph→1.2.10).
  The `[google]` extra is required even for Claude — 7.2.0's `init_models.py` hard-imports
  amazon+google_genai+openai with no try/except.
- **13 CF app-ops tools** (§4.11), mock+stateful.
- **All writes enabled** (`BTP_ALLOW_WRITES=1`, set in `run_local.py`).
- **`aicore.py` anthropic branch:** for `model_name.startswith("anthropic")` it bypasses
  `init_llm` and constructs `ChatBedrock` directly with a temperature-only `model_kwargs`.
  Reason: Bedrock/Claude rejects `temperature`+`top_p` together, but `init_llm` always injects
  both. Also passes `proxy_client=get_proxy_client()` explicitly (7.x no longer lazily inits
  it). The branch is **guarded** — `gpt-4o` still uses the stock `init_llm` path, so this file
  is safe to deploy even against the older SDK.

### 4.12.2 What is available for DEPLOYMENT (BTP CF + SAP-managed / Joule)

- **LLM = gpt-4o.** `agent.py:67` `AGENT_LLM_MODEL` default is still `"gpt-4o"`; nothing in
  the deployable config overrides it.
- **`sap-ai-sdk-gen==6.6.0`** — `requirements.txt` is **unchanged** (still pins 6.6.0 without
  the amazon/google extras). The deployed runtime therefore **cannot** run Claude/Bedrock even
  if asked: 6.6.0 has no Bedrock deps registered and doesn't know 4.6-opus.
- **Real BTP REST tools only** — 64 read-only (`BTP_ALLOW_WRITES=0`, manifest default) or 119
  with writes. **No CF app-ops tools.**
- Everything in §4 (accounts, entitlements, cost, usage, provisioning, audit, auth, SCIM)
  against real BTP data via destinations (subject to the §6 known issues).

### 4.12.3 To promote Claude 4.6 Opus / CF tools to deployment (NOT yet done — needs approval)

Per the runtime-safety preference these were intentionally left undone:
1. **Claude for deployed runtime:** bump `requirements.txt` to
   `sap-ai-sdk-gen[amazon,google]==7.2.0` (or the CF equivalent), re-vendor/repackage, verify
   the larger dep set fits the buildpack + 512M/1G limits, and set `AGENT_LLM_MODEL` in the
   manifest. Confirm the tenant deployment `anthropic--claude-4.6-opus` is reachable from the
   deployed `aicore` destination's resource group.
2. **CF app-ops for real:** implement a real CF Cloud Controller v3 client (auth via a CF API
   destination/OAuth), register the 13 tools in `_build_domain_tools`, and remove the
   mock-only assumption. Substantial new work — not a config flip.

Both are **separate, explicitly-approved changes**; do not ship them as part of the demo prep.

## 5. Deployment (Cloud Foundry, eu10)

- **Manifest:** `manifest.yml` — 512M / 1G disk, 1 instance, python buildpack,
  gunicorn+uvicorn, health check on `/.well-known/agent.json`.
- **Bound service:** `proj-vector-destination-service` (Destination Service).
- **Destinations** (all `OAuth2ClientCredentials`. Accounts/Entitlements use the
  cis-central `client_credentials` binding; **`BTP_RESOURCE_CONSUMPTION` uses the Usage
  Data Management Service key** — cis-central creds return 403 on the cost endpoint):

  | Destination | URL | Credential source | Status |
  |-------------|-----|-------------------|--------|
  | `BTP_ACCOUNTS` | `https://accounts-service.cfapps.eu10.hana.ondemand.com` | cis-central `client_credentials` key | ✅ Working |
  | `BTP_ENTITLEMENTS` | `https://entitlements-service.cfapps.eu10.hana.ondemand.com` | cis-central `client_credentials` key | ✅ Working |
  | `BTP_RESOURCE_CONSUMPTION` | `https://uas-reporting.cfapps.eu10.hana.ondemand.com` | Usage Data Management Service key (cis-central returns 403) | ✅ Working |
  | `BTP_METRICS` | `https://account-budgets-service.cfapps.eu10.hana.ondemand.com` | cis-central key | ✅ Working |
  | `BTP_USAGE_RECORDS` | `https://account-budgets-service.cfapps.eu10.hana.ondemand.com` | cis-central key | ✅ Working |
  | `BTP_PROVISIONING` | `https://provisioning-service.cfapps.eu10.hana.ondemand.com` | cis-central key | ❌ Bad credentials (see §6) |
  | `BTP_AUDIT_LOGS` | `https://auditlog-management.cfapps.eu10.hana.ondemand.com` | `auditlog-management` service key | ⚠️ Not yet verified deployed |
  | `BTP_AUTHORIZATION` | `https://api.authentication.eu10.hana.ondemand.com` | `xsuaa apiaccess` service key (`XSUAA_API_PLAN`), token URL = `coena.authentication.eu10.hana.ondemand.com/oauth/token` | ⚠️ Not yet verified (Authorization API calls not tested) |
  | `BTP_SCIM` | `https://coena.authentication.eu10.hana.ondemand.com` | Same `xsuaa apiaccess` key as `BTP_AUTHORIZATION` | ❌ 403 on `/scim/Groups` — scope issue (see §6) |
  | `aicore` | AI Core service URL | clientId/clientSecret/tokenServiceURL/AI-Resource-Group | ✅ Working |

- **Packaging:** `.cfignore` excludes `.venv`, `vendor/`, tests, coverage,
  Joule-only requirements, and metadata so the droplet stays small (~30 MB upload).
  `.bp-config/options.json` sets `DISABLE_PYTHON_VENDORING`.

## 6. Known Issues & Open Items

### ❌ SCIM `/scim/Groups` and `/scim/Users` — 403 Forbidden

**Symptom:** `getSCIMGroups`, `getSCIMGroup`, `getSCIMUsers`, `getSCIMUser` all return 403.

**Root cause:** The `xsuaa apiaccess` OAuth token does not include the scope required by
XSUAA to access the SCIM endpoint. The `apiaccess` plan grants Authorization API access
(`/sap/rest/authorization/v2/...`) but not SCIM by default.

**What was tried:**
1. Wrong destination URL (`api.authentication.eu10.hana.ondemand.com/scim`) → 404. Fixed to
   `coena.authentication.eu10.hana.ondemand.com`.
2. Wrong paths in code (`/Groups`, `/Users`) → 401/404. Fixed to `/scim/Groups`, `/scim/Users`.
3. Added `scope=uaa.user` as additional property on destination → `Invalid scope: uaa.user`
   (the `apiaccess` plan rejects this scope override). Removed.

**Current state:** 403 persists. The `apiaccess` service key (`XSUAA_API_PLAN`) does not
grant SCIM access in its current configuration. The `xs_authorization.read` authority would
need to be added to the service instance — but user has chosen not to modify service instances.

**Impact:** "Who has admin roles" queries cannot return member users. `getRoleCollections`
(Authorization API) still works and returns role collection names/roles, just not the member list.

**Options to resolve (deferred):**
- Add `xs_authorization.read` to `XSUAA_API_PLAN` authorities via `cf update-service`.
- Use `OAuth2UserTokenExchange` destination auth (user-propagation) instead of client credentials.
- Accept limitation and rely on `getRoleCollections` for role governance queries.

### ❌ `BTP_PROVISIONING` — Bad credentials

The Destination Service returns `authTokens` error (`Bad credentials`) for this destination.
Provisioning tools (`getEnvironmentInstances`, `getEnvironmentInstance`, etc.) will fail.
**Deferred** — needs the credential fixed on the BTP side.

### ⚠️ `BTP_AUTHORIZATION` — Not yet verified

The destination was corrected (URL: `api.authentication.eu10.hana.ondemand.com`, same
`xsuaa apiaccess` key). No Authorization API tools have been successfully called yet against
live data. Likely works since it uses the same credentials that the SCIM token was successfully
fetched from — the 403 is endpoint-specific, not a token failure.

### ⚠️ `BTP_AUDIT_LOGS` — Not yet deployed-verified

Tool implemented and committed. Destination exists. Not yet exercised against the deployed CF app.

### ⚠️ Message history window caveat

`_trim_messages` keeps the last 40 non-system messages. On very long single-turn reasoning
loops (agent calling 20+ tools in one turn), trimming mid-turn could in theory orphan a tool
result. In practice gpt-4o rarely chains more than 10 tool calls per turn. If this becomes an
issue, raise `BTP_MAX_HISTORY_MESSAGES` or implement per-turn windowing instead.

## 7. Current Status Summary

### ✅ Working
- Account topology, entitlements, consumption, usage, cost queries against real BTP data.
- Multi-turn conversation memory (MemorySaver checkpointer by A2A context_id).
- Context overflow protection (message windowing + response shaping + char-cap backstop).
- 64 read-only tools deployed (`BTP_ALLOW_WRITES=0`): 42 core + 22 Authorization/SCIM read.
- Authorization API read tools (getRoleCollections, getIdentityProviders, getSecuritySettings, etc.) — deployed and available; not yet confirmed against live data.
- Audit log retrieval (`getAuditLogRecords`).
- 401 token-refresh retry in `api_client.py`.
- Local dev harness + chat UI.

### ❌ Not working
- SCIM Groups/Users → 403 (scope issue on `xsuaa apiaccess` key, deferred).
- Provisioning tools → Bad credentials on destination.

### 🚫 Not yet built (PRD gaps)
- R3: Entitlement Consumptions API (used/assigned ratio).
- R4: Checks API / Monitor Log API.
- R5: Proactive alerting (background monitor, Alerting Channels API).
- Milestone logging M1–M5 (helpers exist but never called).
- Extensibility layer (tool registry/plugin mechanism).

## 8. Constraints & Conventions

- Never make code changes that alter deployed CF/Joule runtime behaviour when the
  goal is local testing; keep local-only changes gated behind `IBD_TESTING`.
- All secrets (`config.json`, service keys) stay out of git (`.gitignore`).
- Exactly 3 decorated functions in `agent.py` (`@agent_model`, `@agent_config`,
  `@prompt_section`) per the bootstrap contract.
