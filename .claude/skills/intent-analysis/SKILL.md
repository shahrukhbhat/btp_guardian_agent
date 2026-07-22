---
name: intent-analysis
description: Intent-Based Development (IBD). ALWAYS the first skill to invoke for any new user request. Use this before any other skill when a user wants to build, create, or design anything, including when the user says "fast track", mentions a specific technology (n8n, agent, CAP, etc.), or asks to create/build/deploy anything. Analyzes the user's intent and writes it into intent.md, which all downstream skills require. Do NOT skip this skill, even if the request mentions a specific technology covered by other skills. EXCEPTION — if the user explicitly asks for a spec / specification spec (e.g. "create a spec / specification for …"), use the `specification` skill directly — it accepts user prompts as input and does not require intent.md.
metadata:
  version: 1.0.0
  author: sap-joule-studio
---

# Analyzing User's Intent
- This skill captures the user's intent in `intent.md`, which is the foundation for all downstream artifacts.
- **CRITICAL: Writing `intent.md` is MANDATORY and MUST NOT be skipped under any circumstances.**

**At startup, check if `intent.md` already exists in the current folder:**
- If `intent.md` exists, enter **Refinement Mode** (see the Refinement Mode section below).
- If `intent.md` does not exist, proceed with the steps in the Workflow below.
- Ask Clarifying Questions and write the file `intent.md` in the current directory.
- If and only if the user's request explicitly contains "fast track", operate in fast track mode (see the Fast Track Mode section for instructions).

## Output
The `intent.md` file should follow the format as mentioned below, replace `<...>` with actual contents
````markdown
# <Title of intent>

<overall project or idea title>

## Business challenge

<user's original business challenge statement>

## Key Milestones [skip for fast track mode, EXCEPT for AI Agent solutions]

**For AI Agents**: Even in fast-track mode, capture 3-5 essential business steps/milestones. These are REQUIRED for business step instrumentation downstream.

**For other solutions**: Can be skipped in fast-track mode.

<Checkpoints that mark meaningful progress or completion in the solution's process, as described by the user. For each: a short name and the condition under which it is reached.>

## Business Architecture (RBA)

### End-to-End Process

[E2E process, e.g. "Source to Pay (E2E-216)"]

### Process Hierarchy

```
<E2E Process (Level 1)>
└── <Phase (Level 2)>
    └── <Sub-Process>
        └── <Business Activity>
        └── <Business Activity>
    └── <Sub-Process>
        └── <Business Activity>
```

### Summary

[1-2 sentences on how the user's challenge maps to the RBA hierarchy]

## Fit Gap Analysis

| Requirement (business) | Standard asset(s) found | API ORD ID | MCP Server ORD ID | MCP Server Version | Gap? | Notes / assumptions |
| ---------------------- | ----------------------- | ---------- | ----------------- | ------------------ | ---- | ------------------- |
| <requirement>          | <asset(s)>              | `<api-ord-id>` or — | `<mcp-ord-id>` ✓ or — | `<version>` or — | Yes/No/Maybe | <notes> |

### Key findings
<3–6 concise bullets covering reuse choices, key design decisions, and any critical assumptions>

## Recommendations

### <Title of recommendation>

#### Executive Summary

<brief summary of recommended approach, limited to 60-80 characters>

#### Recommended Solution

<full description of recommended solution, specifying relevant SAP products or required custom developments>

#### Problem Statement [skip for fast track mode]

<description of core problem being solved>

#### Affected User Roles [skip for fast track mode]

<brief list of user roles or job titles affected by this challenge — no detailed persona descriptions>

#### Important factors [skip for fast track mode]

##### <title of factor, e.g. Reduces manual effort through automation>

<description of factor>

#### Potential risks [skip for fast track mode]

##### <title of risk, e.g. Integration complexity with legacy systems>

<description of risk>

#### Recommended solution category

<e.g. SAP Product, AI Agent, BTP Extension, React App, n8n Workflow — or any other category that best describes the solution; list multiple if the solution combines several components>

#### Intent fit
<how well the recommended solution fits the user's original intent and requirements, including any trade-offs or limitations, represented just in percentage terms (e.g. "90%"). Write just the percentage number without description>
````

## Fast Track Mode
- A minimal intent.md is generated and all phases are completed automatically, no Clarifying Questions asked at any phase.
- Only essential sections are populated; all standard Q&A and explorations are skipped.
- You must still perform the fit-gap analysis. You **MUST** call the `sap_knowledge_graph_fit_gap` tool with the BP URIs from the `sap_knowledge_graph_bp_mapping` result.
- **Key Milestones section**: If the solution is an AI Agent, you MUST capture key milestones (3-5 essential business steps) as these are required for instrumentation. For other solution types, milestones can be skipped.
- The produced document should not exceed 50 lines (excluding milestones for AI Agents).
- No confirmation for proceeding to the next skill is asked - just proceed immediately to the PRD generation using the `product-requirements-document` skill.
- If "fast track" is NOT in the user request:
  - Ask clarifying questions before writing `intent.md`
  - After writing `intent.md`, STOP and ask the user if they want to proceed to PRD
  - Do NOT pre-queue downstream skills (PRD) in your todo list

## Refinement Mode (existing intent.md)
When `intent.md` already exists:

1. **Load the existing document**: Read `intent.md` into your context.
2. **Ask what they want to refine**: Use the `question` tool to ask the user what they would like to change, add, or remove. Offer concrete options such as:
   - Adjust the business challenge or scope
   - Update affected user roles or pain points
   - Revise the fit-gap analysis
   - Change the recommendation or solution category
   - Fix factual or structural issues
   - Other (open-ended)
3. **Gather the necessary information**: Ask only the questions required to implement the requested changes. Reuse context already in `intent.md` — do not repeat discovery already done.
4. **Apply the changes**: Overwrite `intent.md` with the updated content. **Do not reproduce or summarise the updated file content in the chat** — briefly note (one line) what was changed and confirm the file was saved.
5. **Confirm and iterate**: Ask the user whether they want to make further refinements or are satisfied with the result. Repeat steps 2-5 until the user is done.
6. **Suggest next step**: Once the user is satisfied, suggest re-running the `product-requirements-document` skill to reflect the updated intent.

Refinement Mode is iterative — the user can request multiple rounds of changes before moving on.

For the following phases, use a todo list if available.

## Workflow
Your objective: thoroughly understand the customer's business challenge.

### Step 1
Understand their business challenge, including:
  - The specific user roles facing this challenge and when they encounter it
  - What are the pain points they experience
  - Success criteria: what must happen for the customer to consider the challenge resolved
  - Key milestones: what checkpoints mark meaningful progress or completion within the solution's process.
  - What constraints and requirements does the customer have relative to this business challenge

Before asking the user any Clarifying Questions, **investigate** the customer's current enterprise landscape as it relates to their business challenge. This way, you can mitigate asking questions about information that already might be available to you. For investigation:

1. **You MUST call the `sap_knowledge_graph_bp_mapping` tool** with the user's business challenge as the `query` (from the `ibd-mcp` server). This maps the challenge to the BP hierarchy in SAP's Reference Business Architecture (RBA) using an agentic search + SPARQL pipeline. The tool returns two parts:
   - A **structured JSON block** (fenced as ` ```json bp-mapping-result `) containing the primary E2E process, hierarchy with URIs, industry variants, and a summary. Extract the hierarchy and E2E process name for the **Business Architecture (RBA)** section of `intent.md`.
   - A **natural language analysis** explaining how the challenge maps to the RBA. Use this context for your investigation and to formulate better clarifying questions.

   The structured JSON includes Business Activity and Process URIs — retain these in your context as they will be needed for the Fit Gap Analysis step.

   If `sap_knowledge_graph_bp_mapping` returns empty output or an error, inform the user and proceed with the SAP LeanIX investigation and clarifying questions.

2. **Use LeanIX tools only if the tool is available, otherwise skip this step.** Use the following sap_leanix tools in the following manner:
  - Call `sap_leanix_call_leanix_agent`, with the business challenge as *user_message*, use returned `thread_id` to requery the agent until the result is ready. This will return a list of relevant Business Capabilities, Applications, and Initiatives from the customer's landscape.

3. Discover available APIs and MCP servers relevant to the business challenge:
   - Call `sap_knowledge_graph_api_discovery` with the business challenge as `query` to find relevant API ORD IDs, ideally OData services which have corresponding MCP servers
   - For each API ORD ID returned, call `get_mcp` with `ordId=<api-ord-id>` to check whether a corresponding MCP server exists in the customer's landscape.
   - If an MCP server is found → record it as type **MCP Server** (store the MCP server ORD ID and, if returned, the version).
   - If no MCP server is found → record it as type **API** (store the API ORD ID).
   - Carry all found ORD IDs in context — populate the **API ORD ID**, **MCP Server ORD ID**, and **MCP Server Version** columns of the Fit Gap Analysis table in `intent.md` for each relevant requirement row.

4. Retain the information from your investigation in your context to formulate meaningful Clarifying Questions, and not generic questions, as tool responses in most cases already have that information.

5. Ask the user Clarifying Questions using `question` tool or similar tool that can be used to ask the user.

6. Use the results to populate the landscape context you will carry into the Fit Gap Analysis.

### Step 2
Perform a fit-gap analysis evaluating how well the customer's existing capabilities align with their business requirements.

1. **You MUST call the `sap_knowledge_graph_fit_gap` tool** (from the `ibd-mcp` server) with the BP sub-process URIs from the `sap_knowledge_graph_bp_mapping` structured result (available in your context from Step 1). Pass all sub-process URIs found in the `primary_e2e.phases[*].sub_processes[*].uri` fields of the `bp-mapping-result` JSON block as `bp_ids`. Also pass the user's business challenge as `business_challenge` for context.
2. The tool returns a structured JSON object with `sub_processes`; each sub-process contains `products`, and each product contains `capabilities` annotated with `mandatory`/`optional` coverage. The response also includes `total_products` and `total_capabilities`.
3. Use these results to map each business requirement to available standard SAP products and capabilities. Filter out products that are not relevant to the specific business challenge (e.g., SAP Fieldglass VMS is not relevant for a non-workforce scenario).
4. Identify gaps where requirements remain unmet based on the evidence returned.

When you're done, incorporate the results into the `Fit Gap Analysis` section of `intent.md` (see template). Keep all findings in your conversation context and proceed to the next step.

### Step 3
Investigate how to best tackle the gaps identified in the previous phase and determine the best solution approach. Use Solution Category Reference and Common Solution Patterns to guide your investigation:
1. Investigate whether any standard SAP products could meet the existing gaps
2. Investigate whether any custom development options could meet the existing gaps
3. Investigate what SAP best practices apply to this challenge
4. Determine which approach has the best balance between ease of implementation and fulfillment of the user's requirements.

For more information on different tools available to you, refer to the `Tools` section.

When you're done with the investigation, write the file `intent.md` in the current directory using the template defined at the top of this skill. Replace all `<...>` placeholders with the actual content gathered across all phases. You MUST include the recommended solution category based on your investigation — use the Solution Category Reference as a guide, but the category is not limited to the examples listed there.


**After writing `intent.md`, do NOT reproduce, paraphrase, or summarise its content in the chat.** Simply confirm the file has been saved. This avoids generating the same tokens twice.

## Tools

- **`sap_leanix_` tools. Use only if available**: The following four SAP LeanIX tools are available. They allow you to query the user's enterprise landscape and gather information about applications, interfaces, and technologies via fact sheets. Each fact sheet can contain links to other fact sheets, which can also be used to find related information.
  - **To get full information** → `sap_leanix_call_leanix_agent` with the business challenge as *user_message*, use returned `thread_id` to requery the agent until the result is ready.

- **`sap_knowledge_graph_bp_mapping` tool**: Maps a business challenge to the BP hierarchy in SAP's Reference Business Architecture (RBA). Returns structured JSON (primary E2E process, hierarchy with URIs, industry variants) plus a natural language analysis. Use this for the Business Architecture (RBA) section in Step 1. The structured output includes URIs that can be used for downstream SPARQL queries.

- **`sap_knowledge_graph_fit_gap` tool**: Queries the SAP Build Knowledge Graph to find products and solution capabilities that cover the identified sub-processes. Takes BP URIs from the `sap_knowledge_graph_bp_mapping` result (`primary_e2e.phases[*].sub_processes[*].uri`) and returns structured JSON with products whose capabilities are annotated with mandatory/optional coverage — no LLM intermediary, ~5s execution. Use this for the Fit Gap Analysis section in Step 2.

- **`sap_knowledge_graph_api_discovery` tool**: Discovers API ORD IDs relevant to a business challenge. Use in Step 1 as the entry point before calling `get_mcp`.

- **`get_mcp` tool**: Checks whether an MCP server exists for a given API ORD ID.
  - **To check if an API has an MCP server** → call with `ordId=<API_ORD_ID>`; returns MCP server ORD ID and version (if available) if one exists, empty otherwise.
  - Tool enumeration and schema retrieval happen later in the `specification` skill, not here.



# References

## Solution Category Reference

The `Recommended solution category` field in `intent.md` flows directly into `product-requirements-document.md` and controls what gets built by the `specification` skill. **Choose carefully — this is a build decision, not just a label.**

**Multiple values are supported** — list them comma-separated (e.g. `n8n Workflow, AI Agent`). When multiple categories are specified:
- One spec is generated per category in parallel, each isolated in its own `specs/<type>/` directory.
- A cross-spec compatibility check runs after spec generation to verify the components can communicate correctly at runtime.
- Implementation runs in parallel, one sub-agent per component.

### Categories

| Category                   | What gets built                                                                                   |
|----------------------------|---------------------------------------------------------------------------------------------------|
| **SAP Product**            | No custom code — configuration and activation only                                                |
| **BTP Extension**          | CAP backend + React frontend, deployed on BTP                                                     |
| **AI Agent**               | Pro-code Python agent (A2A protocol) with extensibility, OpenTelemetry instrumentation, and tests |
| **n8n Workflow**           | n8n workflow written as a `.n8n.json` file                                                        |
| **Other**                  | No code generated — describe the most fitting category                                            |

This list is **not exhaustive** — use the category that best describes the solution, even if it is not listed here.

### Decision Flow

1. Does a standard SAP product cover the requirement without custom development? → **SAP Product**
2. Does the solution require custom SAP logic, integration, or BTP-hosted UI? → **BTP Extension**
3. Does the solution involve automation or AI?
   - Is the primary deliverable an autonomous agent that reasons and decides its own next steps without a fixed workflow graph — no explicit orchestration layer driving the flow? → **AI Agent**
   - Is there a clearly defined workflow (steps and routing known upfront), all AI involvement can be handled by a static LLM node, or the user explicitly calls out n8n as the orchestrator? → **n8n Workflow**
   - Is there a clearly defined workflow AND at least one step requires open-ended reasoning, context retention, or dynamic tool use that a static node cannot reliably handle? → **n8n Workflow, AI Agent**
4. Is the deliverable a standalone frontend web app (dashboard, portal, landing page, internal tool) with no required SAP backend logic — i.e. a pure UI artifact? → describe the most fitting category
5. Otherwise → describe the most fitting category

### Common Solution Patterns

| Business Need                                                                                               | Likely Solution                                              | Category               |
|-------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------|------------------------|
| Standard procurement, HR, or finance processes                                                              | SAP Ariba / SuccessFactors / S/4HANA                         | SAP Product            |
| Custom approval UI, integration, or Fiori app on BTP                                                        | CAP service + React frontend on BTP                          | BTP Extension          |
| Event-driven trigger → multi-step automated process (e.g. PO budget breach → notify → route for approval)   | n8n workflow with trigger, condition, and action nodes       | n8n Workflow           |
| Scheduled monitoring with threshold alerts (e.g. supplier on-time delivery < 85%, overdue receivables)      | n8n workflow with scheduled trigger and conditional routing  | n8n Workflow           |
| Natural language queries over SAP data, trend analysis, or autonomous recommendations (no fixed steps)      | Pro-code Python agent (A2A) with MCP tool calls              | AI Agent               |
| Intelligent assistant embedded in a UI (e.g. onboarding Q&A, troubleshooting help, product search)          | Pro-code Python agent (A2A)                                  | AI Agent               |
| Scheduled/event-driven workflow + one step requires open-ended reasoning or personalised content generation | n8n workflow calling a pro-code agent as a sub-step          | n8n Workflow, AI Agent |
| Overdue receivables escalation chain + agent drafts personalised collection emails per customer             | n8n escalation flow + agent for context-aware email drafting | n8n Workflow, AI Agent |
| Supplier KPI alert workflow + agent analyses performance trends and recommends alternative vendors          | n8n threshold alert + agent for advisory reasoning           | n8n Workflow, AI Agent |

## Enterprise Domains

Four domains structure all enterprises:

| Domain                  | Purpose               | Business Areas                                             |
| ----------------------- | --------------------- | ---------------------------------------------------------- |
| **Products & Services** | Develop offerings     | R&D, Engineering, Product Management                       |
| **Supply**              | Fulfill demand        | Procurement, Manufacturing, Supply Chain, Service Delivery |
| **Customer**            | Generate demand       | Sales, Marketing, Customer Service, Commerce               |
| **Corporate**           | Manage the enterprise | HR, Finance, Asset Management, IT, GRC                     |

## Core Business Processes

Eight end-to-end processes define the enterprise value chain:

| Process                     | Domain    | Flow                                                              |
| --------------------------- | --------- | ----------------------------------------------------------------- |
| **Lead to Cash**            | Customer  | Market → Lead → Quote → Order → Fulfill → Invoice → Cash          |
| **Source to Pay**           | Supply    | Source → Contract → Requisition → Order → Receipt → Invoice → Pay |
| **Plan to Fulfill**         | Supply    | Plan → Procure → Make → Inspect → Deliver                         |
| **Idea to Market**          | Products  | Idea → Requirement → Design → Release → Manage                    |
| **Recruit to Retire**       | Corporate | Plan → Recruit → Onboard → Develop → Reward → Offboard            |
| **Acquire to Decommission** | Corporate | Plan → Acquire → Operate → Maintain → Decommission                |
| **Finance**                 | Corporate | Plan → Record → Report → Treasury → Close                         |
| **Governance**              | Corporate | Portfolio → Project → Sustainability → GRC → IT Management        |

## Business Capability Hierarchy

```
Enterprise Domain (Products & Services, Supply, Customer, Corporate)
└── Business Domain (L1 grouping by function)
    └── Business Area (L2 grouping)
        └── Business Capability (what the business does)
```

A **Business Capability** describes an organization's ability to achieve a specific outcome. Capabilities are realized through:

- **Processes** (how work flows)
- **People** (roles, skills)
- **Technology** (applications, infrastructure)

## Solution Architecture Hierarchy

```
Solution Capability (implements Business Capability)
└── Solution Component (SAP product or service)
    └── Solution Process (implements business process)
        └── Solution Activity (specific action in a component)
```

## Architecture Principles

Apply these principles when formulating recommendations and solution designs:

- **Business before Technology**: Derive solutions from business requirements, not technology preferences
- **Cloud First**: Prefer SaaS for new capabilities; on-premise only when required
- **Run Simple**: Choose the simplest architecture that meets requirements
- **Extensibility**: Use standard features first; build custom only for differentiation
- **Data as Asset**: Treat data quality as competitive advantage; single source of truth
- **Composability**: Shrink monolithic core; surround with modular services
- **Control Technical Debt**: Use maintained, supported technology stacks

## SAP Product Portfolio (Key Products by Domain)

**Customer:**
- **SAP Sales Cloud** - Sales force automation
- **SAP Service Cloud** - Customer service management
- **SAP Commerce Cloud** - E-commerce platform
- **SAP Emarsys** - Marketing automation

**Supply:**
- **SAP S/4HANA** - Core ERP (MM, PP, SD, WM)
- **SAP Ariba** - Procurement network
- **SAP IBP** - Integrated business planning
- **SAP TM** - Transportation management

**Products & Services:**
- **SAP S/4HANA PLM** - Product lifecycle management
- **SAP Engineering Control Center** - CAD integration

**Corporate:**
- **SAP SuccessFactors** - Human capital management
- **SAP Concur** - Travel and expense
- **SAP S/4HANA Finance** - Financial management
- **SAP Analytics Cloud** - Business intelligence

**Platform:**
- **SAP BTP** - Business Technology Platform
- **SAP Integration Suite** - Integration middleware
- **SAP Build** - Low-code development
- **SAP AI Core** - AI/ML runtime

# Guardrails
## Communication Guidelines
- Be direct and concise; avoid filler phrases, unnecessary apologies, excessive enthusiasm, or encouragement.
- Use the `question` tool (or a similar tool you have available) to pose questions to the user.
- Do not ask vague clarifying questions (e.g., "What platform do you use to host your application?") if the information is already available from your investigation. Instead, ask specific questions grounded in the user's landscape (e.g., "I see system X and Y in your environment. Do you intend to keep using them for the current challenge?").
- If the user greets you, engages in casual conversation, or has not yet described a business challenge:
  - Respond warmly and ask what business challenge or requirement they'd like to work on.
  - Do not enter the understanding phase or start asking detailed questions.
  - Wait for them to describe their business intent before starting the Workflow.
- Keep communication professional and text-based (no emoji).
- Before and after each step, inform the user about what you're doing.
- The goal is to understand the "what" and "why", and only then the "how".

## Scope Guidelines
- Only start with the Workflow AFTER the user has provided their business intent or challenge. Go through the steps sequentially.
- Go through each step of the Workflow, until you have written `intent.md`.
- If the user provides new information at any point, evaluate whether it impacts your previous findings. If it does, make the necessary adjustments.
- If the user provides information that contradicts your previous findings, clarify with the user which information is correct and update your findings accordingly.
- If the user wants to discuss certain topics outside their usual phase, that's perfectly fine. Capture whatever information they share and adjust your approach accordingly.
- Users are in control - they can skip questions.
- Be thorough in your analysis and research in each phase to ensure that your recommendation is well-founded.


## Investigation Guidelines
- Start by identifying all aspects of the question that need investigation.
- Query each relevant dimension systematically (applications, capabilities, integrations, etc.)
- If a query returns information that raises new questions, investigate those too
- Synthesize the findings cohesively to be used in the relevant sections of 'intent.md'

# Gotchas
- **Never reproduce, paraphrase, or summarise the content of a file you have just written.** After saving a file, confirm it was written (one line) and move on. Summarising written content generates the same tokens twice and must be avoided.
- Avoid skipping tool calls under the assumption that you have enough information. Use this checklist to make sure you make the appropriate calls: ([] sap_knowledge_graph_bp_mapping, [] sap_knowledge_graph_fit_gap, [] sap_leanix, [] sap_knowledge_graph_api_discovery → get_mcp per ORD ID).
- **Critical constraint:** Business Case and Business Metrics content must never be generated by the LLM. Only include this information from if the user has uploaded some artifacts, or ask a question regarding this with a `question` tool or a similar tool you use to converse with the user.

# Next Steps
After completing the intent analysis and writing the intent.md, the next skill MUST always be `product-requirements-document`. Before automatically proceeding with this skill, ask the user if they want to proceed, as they may want to modify the `intent.md` file, or have some other questions.
