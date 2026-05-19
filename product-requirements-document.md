# Product Requirements Document (PRD)

**Title:** BTP Guardian  
**Date:** 2026-05-19  
**Solution Category:** AI Agent

## Product Purpose & Value Proposition

**Elevator Pitch:**  
BTP platform teams and FinOps stakeholders waste hours navigating the BTP Cockpit and stitching together API data to answer basic questions about consumption, costs, and governance. BTP Guardian is an AI agent that answers those questions conversationally in seconds — and proactively flags problems before they become budget shocks or compliance incidents.

**Expected Value:**  
Reduced time-to-insight for BTP cost and entitlement queries; earlier detection of cost overruns, policy drift, and configuration anomalies; fewer surprise invoice line items; less manual work for platform admins.

**Product Objectives:**
1. Enable natural-language queries over BTP account topology, consumption, costs, and entitlements with answers in under 10 seconds.
2. Proactively surface cost overruns, entitlement exhaustion, and governance anomalies before they escalate.
3. Provide a fully observable, extensible agent that platform teams can enhance with custom policy checks.

## Requirements

### Must-Have Requirements

**R1**: Account Topology Navigation
- **User Story**: As a BTP platform admin, I can ask "show me all subaccounts under my global account" and receive a structured answer so that I understand my account hierarchy instantly.
- **Acceptance Criteria**: Agent returns global account → directory → subaccount tree using the Accounts Service and Provisioning APIs.

**R2**: Consumption & Cost Queries
- **User Story**: As a FinOps stakeholder, I can ask "which subaccount spent the most this month?" so that I can prioritize cost optimization conversations.
- **Acceptance Criteria**: Agent queries the Consumption, Resource Consumption, and Usage Records APIs and returns a ranked, human-readable cost summary.

**R3**: Entitlement Utilization Analysis
- **User Story**: As a platform admin, I can ask "which services are over-provisioned?" so that I can right-size entitlements and avoid waste.
- **Acceptance Criteria**: Agent compares assigned entitlements against actual usage via the Entitlements Service and Entitlement Consumptions API and returns a utilization table.

**R4**: Governance Posture Assessment
- **User Story**: As a platform admin, I can ask "are there any configuration drift issues?" so that I can remediate before an audit or incident.
- **Acceptance Criteria**: Agent evaluates configuration using the Checks API and Monitor Log API, flags deviations, and suggests corrective actions.

**R5**: Proactive Alerting
- **User Story**: As a FinOps stakeholder, I receive unprompted alerts when a subaccount approaches or exceeds a cost or entitlement threshold so that I can act before a budget breach.
- **Acceptance Criteria**: Agent monitors Metrics API and Alerting Channels API; proactively emits a structured alert with subaccount, service, current vs. threshold values, and recommended action.

**R6**: Access & Identity Governance Visibility
- **User Story**: As a security officer, I can ask "who has admin access across subaccounts?" so that I can detect privilege drift.
- **Acceptance Criteria**: Agent queries the Platform Authorization Management API and returns a role binding summary, flagging accounts with elevated privileges lacking justification.

## Solution Architecture

**Architecture Overview:**  
BTP Guardian is a Python-based A2A agent hosted on SAP BTP (SAP AI Core as LLM runtime). All BTP platform APIs are wrapped as MCP tools. The agent's reasoning loop selects and chains tools autonomously to answer queries, detect anomalies, and generate proactive alerts. OpenTelemetry instrumentation provides full observability.

**Key Components:**
- **Agent Core**: Python A2A agent with LLM-driven reasoning loop (GPT-4o via SAP Generative AI Hub)
- **MCP Tool Layer**: Wrappers for all 17 BTP platform APIs (Accounts, Entitlements, Consumption, Usage Records, Metrics, Alerting, Service Manager, etc.)
- **Proactive Monitor**: Background task polling Metrics and Alerting APIs against configurable thresholds
- **Extensibility Layer**: Hook points for custom policy checks and additional tools

**Integration Points:**
- BTP Accounts Service — account topology (read-only)
- BTP Entitlements Service + Entitlement Consumptions API — quota vs. usage (read-only)
- BTP Consumption + Resource Consumption APIs — cost data (read-only)
- BTP Usage Records API — service-level usage (read-only)
- BTP Metrics API + Alerting Channels API — threshold monitoring (read-only)
- BTP Checks API + Monitor Log API — governance posture (read-only)
- BTP Platform Authorization Management API — access governance (read-only)
- BTP Service Manager — service instance inventory (read-only)
- BTP SaaS Provisioning Service + Provisioning Service — subscription topology (read-only)
- BTP Events Service — audit event stream (read-only)

### Agent Extensibility & Instrumentation

**Agent Extensibility:**
- The agent exposes named extension points for: additional MCP tools, custom policy check rules, and alert threshold configurations.
- Platform teams can register new tools without modifying core agent code.
- Instruction templates are externalized to allow per-deployment customization of governance policies.

**Business Step Instrumentation:**
- All five key milestones (see Milestones section) are instrumented with structured log statements.
- Log pattern: `[MILESTONE_ID].[achieved|missed]: [description]`
- Logs are emitted via OpenTelemetry spans and captured in the SAP AI Launchpad observability dashboard.

### Automation & Agent Behaviour

**Automation Level:** Autonomous agent

**Actions performed without human approval:**
- Query any BTP API for read-only data
- Compose and return natural-language answers
- Emit proactive threshold alerts

**Actions requiring human review:**
- Any remediation action (e.g., revoking entitlements, modifying access bindings) — agent recommends only, never executes write operations

**Model:** GPT-4o via SAP Generative AI Hub

**Tools / Connectors invoked:**
- BTP Accounts Service (read) — topology queries
- BTP Entitlements + Consumption APIs (read) — cost and quota analysis
- BTP Metrics + Alerting APIs (read) — threshold monitoring
- BTP Checks + Monitor Log APIs (read) — governance assessment
- BTP Platform Authorization Management API (read) — access governance

**Guardrails:**
- Agent is read-only across all BTP APIs — no write, delete, or modify operations permitted.
- Confidence below threshold → agent explicitly states uncertainty and directs user to BTP Cockpit.
- PII and sensitive access data is never stored; only transient in the reasoning context.

## Milestones

### M1: Account Topology Resolved
- **Achieved when**: Agent successfully retrieves and structures the full global account hierarchy.
- **Log on achievement**: `M1.achieved: account topology resolved — {subaccount_count} subaccounts mapped`
- **Log on miss**: `M1.missed: account topology could not be resolved — Accounts Service unreachable or empty`

### M2: Consumption & Cost Data Queried
- **Achieved when**: Agent retrieves and normalizes cost data from at least one consumption API.
- **Log on achievement**: `M2.achieved: consumption data retrieved — {service_count} services, period={period}`
- **Log on miss**: `M2.missed: consumption data unavailable — {api_name} returned no data`

### M3: Entitlement Utilization Analyzed
- **Achieved when**: Agent computes utilization ratio (used/assigned) for all entitlements in scope.
- **Log on achievement**: `M3.achieved: entitlement utilization computed — {entitlement_count} entitlements analyzed`
- **Log on miss**: `M3.missed: entitlement analysis incomplete — {reason}`

### M4: Governance Posture Assessed
- **Achieved when**: Agent evaluates configuration checks and produces a posture summary with flagged items.
- **Log on achievement**: `M4.achieved: governance posture assessed — {flagged_count} issues detected`
- **Log on miss**: `M4.missed: governance assessment failed — Checks API returned error`

### M5: Proactive Alert Generated
- **Achieved when**: Agent detects a threshold breach and emits a structured alert with recommended action.
- **Log on achievement**: `M5.achieved: proactive alert emitted — subaccount={subaccount}, service={service}, breach_type={type}`
- **Log on miss**: `M5.missed: alert generation skipped — no thresholds configured or Metrics API unavailable`
