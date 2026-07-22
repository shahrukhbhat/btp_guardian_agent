---
name: specification
description: Generate actionable TODO-list specifications from a PRD. Produces specification/ folder with per-asset checklists. To implement, just say "execute specification/specification.md". Uses product-requirements-document.md, if it's missing, execute `product-requirements-document` skill.
metadata:
  version: 1.0.0
  author: sap-joule-studio
---

# Specification Skill

Generate actionable, TODO-list specifications from a Product Requirements Document (PRD), an intent document, or a direct user prompt. Output is a `specification/` folder containing one main spec and one per-asset spec — each a persisted checklist an AI coding agent can execute directly.

## Input Resolution

The skill uses the best available input, checked in this order:

1. **`product-requirements-document.md`** — full PRD with solution category, milestones, requirements. Preferred.
2. **`intent.md`** — lighter intent document from the `intent-analysis` skill. Used if no PRD exists.
3. **User prompt** — direct description of what to build. Used if neither file exists.

When working from intent or user prompt (no PRD), the skill must infer solution category, feature scope, and requirements from the available context. Ask clarifying questions if the input is too ambiguous to determine task types or key features.

**To implement code:** just say `execute specification/specification.md`.

## Output Structure

```
specification/
  specification.md                            # Master spec — orchestrates all asset specs
  guidelines.md                               # Execution rules (copied from skill, editable)
  guidelines-<type>.md                        # Type-specific constraints (copied once per used type, editable)
  <asset-name>/
    specification.md                          # Per-asset TODO list
    api-specs/                                # (optional) Downloaded API spec files (OData / REST)
    mcp-specs/                                # (optional) Existing MCP server tool schemas
```

Code is generated into `assets/<asset-name>/` when specifications are executed.

## Fast Track Mode

If user request contains "fast track":
- Skip all interactive questions, bypass confirmations
- Generate minimal specification TODO items
- Immediately proceed to execution after generation — do NOT ask the user; autonomously execute `specification/specification.md` by reading it and running all tasks
- Never skip: tests (for agents)

**Fast track = faster workflow, NOT incomplete implementation.**

## A - Writing Specifications

### Step 1: Determine Task Types

Read the resolved input (PRD → intent → user prompt).

If a PRD exists, find the `Solution Category` field. If working from intent or user prompt, infer the category from the description.

**Mapping:**
- AI Agent → `agent`
- BTP Extension → `cap`
- n8n Workflow → `n8n-workflow`

Split on commas if multiple. If missing/ambiguous, ask user.

Select a **feature name** prefix (e.g. `purchase-order-approval`).

**Asset name** = `<feature>-<task-type>` (e.g. `purchase-order-approval-agent`). Note: This is not valid for n8n workflows for which there is only one asset named `n8n`.

This naming allows multiple assets of the same type to coexist.

### Step 2: API Discovery (Conditional)

> **CONSTRAINT: APIs must NEVER be consumed directly. No direct HTTP clients (`requests`, `httpx`, OData clients, `fetch`, `axios`, etc.) for any SAP or external API. All API interactions MUST go through MCP tools.**

This step covers two sub-cases. Run both that apply.

#### 2a — MCP server tool discovery

**When to run:** `intent.md` Fit Gap Analysis table has one or more rows with a value in the **MCP Server ORD ID** column.

For each MCP server ORD ID carried over from intent analysis:

1. Call `get_mcp_tools` with the MCP server ORD ID (and the optional `version` if recorded in `intent.md`) to list all available tools (returns name, title, description — no schemas).
2. For each **relevant** tool (relevant to the business challenge / asset being specified), call `get_mcp_tool_details` with the MCP server ORD ID (and the optional `version` if recorded in `intent.md`) and tool name to retrieve the full input/output JSON schema.
3. Save each tool's full schema as a separate file: `specification/<asset-name>/mcp-specs/mcp-spec-<server-name>-<tool-name>.json`. Create directory if needed.
4. The MCP server ORD IDs, versions (if available), tool names, and paths to schema files **must** be included in the per-asset spec TODO items.

#### 2b — OData / REST API discovery

**When to run:** `intent.md` Fit Gap Analysis table has rows with an **API ORD ID** but no corresponding **MCP Server ORD ID** (i.e., a plain REST/OData API with no MCP server).

**Skip** if the agent has zero SAP API touchpoints (pure AI-to-AI, no SAP backend), OR if every required API interaction is already covered by an available MCP server.

For each relevant asset:

1. Call `sap_knowledge_graph_api_discovery` with a concise use-case description as `query`, `output_file: "api-discovery-results.md"`.
2. For each API with a Download Link (pre-signed S3 URL), fetch the spec file and save **raw content as-is** to `specification/<asset-name>/api-specs/` (e.g. `specification/<asset-name>/api-specs/supplier-invoices.json`). Create directory if needed. **Important note:** If there are multiple specs available for a given API, the `.edmx` should be preferred over the `.json` ones. Keep the original file extension (for example, do not rename `.edmx` to `.json`).
3. Do a targeted read of each downloaded spec: scan `paths` or `channels` keys for relevant endpoints, extract matching request/response schemas. Do not read the entire spec.
4. If search fails, ask user to retry with different query.

The discovered API names, ORD IDs, endpoints, and paths to spec files **must** be included in the PRD-specific tasks in the generated specification.

### Step 3: Generate Specification Files

**Before writing any files**, copy the guidelines from the skill into the `specification/` folder so they live next to the specs and can be customized per project:

- Copy `<skill:specification>/assets/guidelines.md` → `specification/guidelines.md` (skip if already exists)
- For each distinct asset type used, copy `<skill:specification>/assets/guidelines-<type>.md` → `specification/guidelines-<type>.md` (once per type; skip if already exists)

These are **pure file copies** (`cp`) — do not modify, resolve references, or rewrite content. Copy the file bytes as-is.

For each asset:

1. Read the template: `<skill:specification>/assets/specification-<task-type>.template.md`
2. Read the PRD thoroughly to derive feature-specific tasks
3. Replace placeholders:
   - `{​{asset-name}}` → actual asset name (e.g. `purchase-order-approval-agent`)
   - `{​{feature}}` → feature name (e.g. `purchase-order-approval`)
   - Any remaining `<skill:specification>` references: replace with the relative path from the file being written to the skill directory. **Must be a relative `../`-based path — never an absolute path (e.g. never `/Users/...` or `/home/...`).**
4. Replace `{{project-specific-tasks}}` with concrete TODO items derived from the input (PRD, intent, or user prompt). Use `##` markdown headers to group related TODOs:
   - Each functional requirement → one or more `- [ ]` items
   - Each business rule → specific TODO
   - When working from intent/prompt (no PRD): derive tasks from the described goals; be explicit about assumptions made
   - Remove any remaining conditional markers (`{{#if ...}}...{{/if}}`) from output
5. Write to `specification/<asset-name>/specification.md`

**CRITICAL: Complete the spec generation loop for ALL assets before doing anything else.**
Finish every per-asset spec file first, then generate the main spec at `specification/specification.md`:

```markdown
# Specification

> **Guidelines**: Read [guidelines.md](./guidelines.md) before executing ANY tasks below.

Check off items as completed.

## Solution Setup

- [ ] Create asset directories: `mkdir -p assets/<asset-name-1>/ assets/<asset-name-2>/`
- [ ] Invoke `setup-solution` skill to create `solution.yaml` and `asset.yaml` files for every asset
- [ ] Validate all `asset.yaml` and `solution.yaml` files exist and are well-formed

## Asset Implementation

- [ ] Execute specification/<asset-name-1>/specification.md (all items)
- [ ] Execute specification/<asset-name-2>/specification.md (all items)
- [ ] Cross-implementation compatibility check (only if multiple assets — verify interfaces, data shapes, auth, env vars align across assets; fix any mismatches before proceeding)
```

Adjust the list based on actual assets. Single-asset solutions omit the compatibility check.

### Step 4: Review (not in fast-track mode)

Only run this step after ALL per-asset specs and the main `specification/specification.md` is fully written.

Present the full set of generated specifications to the user. Summarize:
- Number of assets and their types
- Key features per asset
- API integrations discovered (if any)

User can edit specification files before execution.

**In fast-track mode:** skip review — do NOT output a summary, do NOT ask the user; autonomously proceed to execution by reading `specification/specification.md` and running all tasks.

## B - Executing Specifications

When user says **"execute specification/specification.md"** (or similar):

Read `specification/specification.md` and execute tasks.

## Templates

Templates live in `<skill:specification>/assets/`:

| Template                                 | Asset Type                       | Availability |
|------------------------------------------|----------------------------------|--------------|
| `specification-agent.template.md`        | AI Agent (Python, A2A protocol)  | Always |
| `specification-cap.template.md`          | CAP BTP Extension (Node.js + UI) | Always |
| `specification-n8n-workflow.template.md` | n8n Workflow automation          | Always |

## Guidelines

Guidelines are **copied from the skill into the `specification/` folder** during spec generation, so they live next to the specs and can be customized per project.

| Source (skill asset)         | Copied to                              | Referenced as (main spec) | Referenced as (per-asset spec) | Availability |
|------------------------------|----------------------------------------|---------------------------|--------------------------------|--------------|
| `guidelines.md`              | `specification/guidelines.md`          | `./guidelines.md`         | `../guidelines.md`             | Always |
| `guidelines-agent.md`        | `specification/guidelines-agent.md`   | —                         | `../guidelines-agent.md`       | Always |
| `guidelines-cap.md`          | `specification/guidelines-cap.md`     | —                         | `../guidelines-cap.md`         | Always |
| `guidelines-n8n-workflow.md` | `specification/guidelines-n8n-workflow.md` | —                    | `../guidelines-n8n-workflow.md` | Always |

Only the guidelines files for asset types actually used in the solution are copied. If a guideline file does not exist, skip it silently and continue.

## What This Skill Does NOT Do

- Implement code (specifications are executed separately)
- Git operations, deployment, authentication
- Create PRDs (use `product-requirements-document` skill)
