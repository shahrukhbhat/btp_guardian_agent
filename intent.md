# BTP Guardian

AI-powered conversational agent giving SAP BTP platform teams and FinOps stakeholders natural-language visibility into cloud consumption, costs, account topology, entitlements, and governance posture — with proactive protection against cost overruns, policy drift, and configuration blind spots.

## Business challenge

SAP BTP platform teams and FinOps stakeholders lack a single, intelligent interface to query their BTP consumption, costs, entitlement utilization, and governance state in real time. They are forced to navigate multiple BTP Cockpit screens, run manual reports, and stitch together data from the Accounts, Entitlements, Usage, and Metrics APIs manually. This creates blind spots that lead to unexpected cost overruns, undetected policy drift, over-provisioned or under-utilized entitlements, and configuration anomalies that go unnoticed until they cause incidents or budget shocks.

## Key Milestones

1. **Account Topology Resolved** — Agent successfully maps the global account hierarchy (global account → directories → subaccounts) and can answer structural topology questions.
2. **Consumption & Cost Data Queried** — Agent retrieves and normalizes real-time and historical consumption metrics and cost data across services and subaccounts.
3. **Entitlement Utilization Analyzed** — Agent compares assigned entitlements against actual usage, identifying over-provisioned and under-utilized services per subaccount.
4. **Governance Posture Assessed** — Agent evaluates configuration against policy baselines, flagging policy drift, access anomalies, and configuration blind spots.
5. **Proactive Alert Generated** — Agent autonomously detects a threshold breach (cost overrun, entitlement exhaustion, anomaly) and surfaces a recommended action.

## Business Architecture (RBA)

### End-to-End Process

Governance (GRC + IT Management)

### Process Hierarchy

```
Governance (E2E)
└── Manage Governance, Risk and Compliance (generic)
    └── Manage enterprise risk and compliance (BPS-398)
        └── Manage regulatory compliance
        └── Manage risk and controls
    └── Manage identity and access governance (BPS-399)
        └── Manage access governance and authorisations
└── Manage Information Technology (generic)
    └── Manage IT governance (BPS-456)
        └── Control IT management system
        └── Operate IT Governance Framework
```

### Summary

BTP Guardian maps to the Governance E2E process, specifically the IT Management and GRC phases. The agent operationalizes IT governance by providing real-time observability and conversational control over BTP platform resources, costs, and compliance posture.

## Fit Gap Analysis

| Requirement (business) | Standard asset(s) found | API ORD ID | MCP Server ORD ID | Gap? | Notes / assumptions |
| ---------------------- | ----------------------- | ---------- | ----------------- | ---- | ------------------- |
| Cloud consumption & cost visibility | SAP Analytics Cloud (optional IT Controlling) | Consumption API, Resource Consumption API (REST) | — | Yes | SAC covers reporting but not conversational real-time querying; no MCP server found |
| Account topology & subaccount management | Accounts Service, Provisioning Service (REST) | Accounts API, Provisioning API | — | Yes | APIs exist; no standard product surfaces topology conversationally |
| Entitlement utilization tracking | Entitlements Service, Entitlement Consumptions API | Entitlements API, Entitlement Consumptions API | — | Yes | APIs available; no product offers NL query interface over entitlements |
| Governance posture & policy drift detection | SAP Data Custodian (Regulatory Compliance, mandatory) | Checks API, Monitor Log API | — | Partial | SAP Data Custodian covers data governance; BTP-specific config drift requires custom logic |
| Identity & access governance | SAP Cloud Identity Access Governance (mandatory), SAP Cloud Identity Services | Platform Authorization Management API | — | Partial | IAG covers access governance broadly; BTP-specific role binding drift needs agent-level reasoning |
| Usage records & SaaS subscription monitoring | SaaS Provisioning Service, Usage Records API | SaaS Provisioning API, Usage Records API | — | Yes | APIs exist; no conversational agent layer; FinOps use case is unmet by standard products |
| Proactive alerting on thresholds/anomalies | — | Alerting Channels API, Metrics API | — | Yes | No standard SAP product provides autonomous, proactive BTP cost/entitlement alerting |

### Key findings

- All BTP platform APIs (Accounts, Consumption, Entitlements, Usage Records, Service Manager, Metrics, Alerting) are available as REST/OData endpoints but expose no ORD IDs and have no corresponding MCP servers in the landscape.
- No standard SAP product delivers a conversational, natural-language interface over BTP FinOps and governance data — this is a clear greenfield gap.
- SAP Analytics Cloud and SAP Data Custodian partially cover IT controlling and compliance reporting, but neither provides autonomous, real-time BTP-native agent capabilities.
- The landscape contains no existing BTP monitoring or FinOps application; BTP Guardian will be a net-new agent asset.
- All 17 BTP platform APIs will need to be wrapped as MCP tools for the agent to call them dynamically.
- The agent's conversational, multi-hop reasoning requirement (e.g., "Why did subaccount X spike last month?" → topology lookup → consumption drill-down → entitlement check) disqualifies static workflows and mandates an autonomous AI Agent architecture.

## Recommendations

### BTP Guardian — AI Agent on SAP BTP

#### Executive Summary

Build a pro-code Python A2A agent wrapping BTP platform APIs as MCP tools.

#### Recommended Solution

Build BTP Guardian as a Python-based A2A (Agent-to-Agent protocol) AI Agent hosted on SAP BTP, using SAP AI Core as the LLM runtime. The agent exposes all relevant BTP platform APIs — Accounts, Entitlements, Consumption, Resource Consumption, Usage Records, SaaS Provisioning, Service Manager, Metrics, Alerting Channels, Monitor Log, Checks, Events, and Platform Authorization Management — as MCP tools. Users interact via a conversational interface backed by the agent's reasoning loop, which autonomously selects and chains tools to answer FinOps and governance questions, detect anomalies, and surface proactive recommendations. The agent is instrumented with OpenTelemetry for observability and includes an extensibility layer so platform teams can add custom policy checks and tools.

#### Recommended solution category

AI Agent

#### Intent fit
92%
