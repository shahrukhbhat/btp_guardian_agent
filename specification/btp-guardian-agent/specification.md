# Specification: btp-guardian-agent

> **Guidelines**: Read [guidelines.md](../guidelines.md) and [guidelines-agent.md](../guidelines-agent.md) before executing ANY tasks below. Follow all constraints described there throughout execution.

## Basic Setup

- [ ] Read the project input (`product-requirements-document.md`, `intent.md`)
- [ ] Bootstrap agent code in `assets/btp-guardian-agent/` using skill `sap-agent-bootstrap` (invoke from inside `assets/btp-guardian-agent/`, use copy commands — do NOT create files manually)
- [ ] Install dependencies, validate the agent starts and responds at `/.well-known/agent.json`

## API-Backed MCP Tool Layer

> All BTP API interactions MUST go through MCP tools. Never make direct HTTP calls from agent code.

- [ ] Invoke `mcp-translation-file` skill to generate MCP translation files and server cards for the following API spec files in `specification/btp-guardian-agent/api-specs/`:
  - `accounts-service.json` — BTP Account topology (directories, global account, subaccounts)
  - `entitlements-service.json` — Entitlements and assignments per subaccount/directory
  - `resource-consumption.json` — Monthly cost reports: cloud credits, subaccount costs, usage summaries
  - `metrics-api.json` — Runtime metrics for apps and service instances per subaccount
  - `usage-records.json` — Granular usage records by service
  - `provisioning-service.json` — Environment provisioning and available environments
- [ ] Invoke `setup-solution` skill to create MCP server assets for each translation file generated above
- [ ] Note MCP server names and ORD IDs from `setup-solution` output for use in `asset.yaml` required dependencies

## Agent Core Implementation

- [ ] In `assets/btp-guardian-agent/app/agent.py`, implement the BTP Guardian system prompt using `@prompt_section` covering:
  - Role: expert BTP FinOps and governance assistant with access to live BTP platform data
  - Instruction: always set page-size / top to a maximum of 100 on every tool call that accepts a pagination parameter; inform the user when this limit is applied
  - Instruction: never hallucinate data — if a tool returns no results, explicitly state that no data was found
  - Instruction: when asked about costs, always specify the time period and currency
  - Instruction: for governance queries, flag issues by severity (critical, warning, info)
  - Instruction: read-only mode — never suggest or imply write/modify operations on BTP resources
- [ ] Wire MCP tools dynamically using `get_mcp_tools()` from `mcp_tools.py` (canonical pattern from guidelines-agent.md); store as lazy instance variable `self._tools`
- [ ] Implement `_build_graph(tools, system_prompt)` using LangGraph to create the agent reasoning loop

## Feature: Account Topology (R1 — M1)

- [ ] Implement tool handler: invoke Accounts Service MCP tool for `GET /accounts/v1/globalAccount` to retrieve global account details
- [ ] Implement tool handler: invoke Accounts Service MCP tool for `GET /accounts/v1/directories` to list all directories
- [ ] Implement tool handler: invoke Accounts Service MCP tool for `GET /accounts/v1/subaccounts` to list all subaccounts
- [ ] Build a topology aggregator helper `_build_topology_tree(global_account, directories, subaccounts)` that structures the response as a nested hierarchy: global account → directories → subaccounts
- [ ] Instrument with M1: emit `M1.achieved: account topology resolved — {subaccount_count} subaccounts mapped` on success; emit `M1.missed: account topology could not be resolved — Accounts Service unreachable or empty` on failure

## Feature: Consumption & Cost Analysis (R2 — M2)

- [ ] Implement tool handler: invoke Resource Consumption MCP tool for `GET /reports/v1/monthlySubaccountsCost` — returns per-subaccount cost breakdown for a given month
- [ ] Implement tool handler: invoke Resource Consumption MCP tool for `GET /reports/v1/monthlyUsage` — returns global monthly usage summary
- [ ] Implement tool handler: invoke Resource Consumption MCP tool for `GET /reports/v1/cloudCreditsDetails` — returns cloud credits consumption
- [ ] Implement cost ranking helper `_rank_subaccounts_by_cost(cost_data)` — sorts subaccounts by total cost descending and formats a human-readable ranked table
- [ ] Instrument with M2: emit `M2.achieved: consumption data retrieved — {service_count} services, period={period}` on success; emit `M2.missed: consumption data unavailable — {api_name} returned no data` on failure

## Feature: Entitlement Utilization (R3 — M3)

- [ ] Implement tool handler: invoke Entitlements Service MCP tool for `GET /entitlements/v1/globalAccountAssignments` — returns all entitlement assignments at global account level
- [ ] Implement tool handler: invoke Entitlements Service MCP tool for `GET /entitlements/v1/assignments` — returns per-subaccount entitlement assignments
- [ ] Implement tool handler: invoke Usage Records MCP tool for `GET /usage-records` — returns granular usage by service to compare against entitlements
- [ ] Implement utilization analyzer helper `_compute_entitlement_utilization(assignments, usage_records)` — computes utilization ratio (used / assigned), flags over-provisioned (< 20% used) and near-exhaustion (> 85% used) services
- [ ] Instrument with M3: emit `M3.achieved: entitlement utilization computed — {entitlement_count} entitlements analyzed` on success; emit `M3.missed: entitlement analysis incomplete — {reason}` on failure

## Feature: Governance Posture Assessment (R4 — M4)

- [ ] Implement tool handler: invoke Provisioning Service MCP tool for `GET /provisioning/v1/environments` — returns all provisioned environments (CF orgs, Kyma clusters)
- [ ] Implement tool handler: invoke Provisioning Service MCP tool for `GET /provisioning/v1/servicePlanAssignments` — returns service plan assignments across environments
- [ ] Implement policy drift detector helper `_assess_governance_posture(environments, assignments)` that:
  - Flags subaccounts with no environment provisioned (orphaned subaccounts)
  - Flags entitlements assigned but never used (last 30 days)
  - Returns a posture report with severity levels: critical / warning / info
- [ ] Instrument with M4: emit `M4.achieved: governance posture assessed — {flagged_count} issues detected` on success; emit `M4.missed: governance assessment failed — Checks API returned error` on failure

## Feature: Proactive Alerting & Threshold Monitoring (R5 — M5)

- [ ] Implement tool handler: invoke Metrics API MCP tool for `GET /accounts/{subaccountName}/apps/{appName}/metrics` — retrieves runtime metrics
- [ ] Implement `_check_thresholds(metrics, thresholds)` helper that evaluates metrics against configurable thresholds dict (default: cost_alert_pct=80, entitlement_alert_pct=85)
- [ ] Implement proactive alert formatter `_format_alert(subaccount, service, current_val, threshold, breach_type)` returning structured alert dict with: subaccount, service, current value, threshold, breach type, severity, recommended action
- [ ] Instrument with M5: emit `M5.achieved: proactive alert emitted — subaccount={subaccount}, service={service}, breach_type={type}` on success; emit `M5.missed: alert generation skipped — no thresholds configured or Metrics API unavailable` on failure

## Feature: Access & Identity Governance (R6)

- [ ] Implement tool handler: invoke Accounts Service MCP tool for `GET /accounts/v1/subaccounts` with full detail to extract subaccount member roles
- [ ] Implement privilege drift detector helper `_detect_privilege_drift(subaccounts)` that identifies subaccounts where admin-level bindings exist but lack justification metadata (description/owner field empty)
- [ ] Return formatted role binding summary table with subaccount, user/group, role, and justification status

## Business Step Instrumentation

- [ ] Implement instrumentation for all 5 milestones (M1–M5) as described in each feature section above
- [ ] Add OpenTelemetry custom spans for each milestone using decorator form `@tracer.start_as_current_span("M{n}-{name}")` on the helper methods (not on `stream()`)
- [ ] Extract all business logic from `stream()` into a plain async helper `_run_agent(query, context_id)` and instrument that helper; yield results from `stream()` outside any span context
- [ ] Verify `auto_instrument()` is called at top of `main.py` before any AI framework imports
- [ ] Verify `assets/btp-guardian-agent/app/agent.py` has exactly 3 decorated functions from bootstrap: `grep -c "^@agent_model\|^@agent_config\|^@prompt_section" assets/btp-guardian-agent/app/agent.py` must return 3

## MCP Mock Configuration

- [ ] After `mcp-translation-file` and `setup-solution` complete, invoke `mcp-mock-config` skill to generate `mcp-mock.json` with realistic mock responses for:
  - Accounts Service: 1 global account, 2 directories, 5 subaccounts
  - Entitlements Service: 3 service plans with varying utilization (10%, 60%, 92%)
  - Resource Consumption: 3 months of cost data with 1 anomalous subaccount
  - Metrics API: app metrics for 2 apps in 1 subaccount
  - Usage Records: 10 usage record entries across 3 services
  - Provisioning Service: 2 CF environments, 1 Kyma cluster

## Testing

- [ ] `conftest.py` only sets `IBD_TESTING=true` — causes agent to run with mock MCP tool results during tests
- [ ] Write unit tests in `assets/btp-guardian-agent/tests/`:
  - `test_topology_tool.py` — tests account topology resolution (M1), asserts hierarchy structure
  - `test_consumption_tool.py` — tests cost retrieval and ranking (M2), asserts sorted output
  - `test_entitlement_tool.py` — tests utilization analysis (M3), asserts over/under-provisioned flags
  - `test_governance_tool.py` — tests posture assessment (M4), asserts flagged items with severity
  - `test_alerting_tool.py` — tests threshold evaluation and alert formatting (M5), asserts alert fields
  - `test_access_tool.py` — tests privilege drift detection (R6), asserts role binding table
- [ ] Run each unit test immediately after writing it: `pytest tests/test_<name>.py`
- [ ] Write integration test `test_integration.py` — end-to-end agent flow: submit query "Which subaccounts have the highest cost this month?", mock LLM to return tool calls for Resource Consumption API, assert final response contains ranked list
- [ ] Run `pytest` from `assets/btp-guardian-agent/` (no args) — if coverage < 70%, add targeted tests
- [ ] Verify `test_report.json` exists in `assets/btp-guardian-agent/`

## Agent Evaluation

- [ ] Invoke `sap-aeval-generate-tool-schema` skill from `assets/btp-guardian-agent/` — writes `tools.json`
- [ ] Invoke `sap-aeval-generate-testcase` skill from `assets/btp-guardian-agent/`, passing `specification/btp-guardian-agent/specification.md` and `tools.json` — writes eval criteria to `aeval/eval.yaml` and test cases to `aeval/testcases/`
- [ ] Review generated test cases and replace placeholder values with realistic BTP data (subaccount names, service plans, cost figures)
