---
name: create-agent-extension
description: >
  Generate extension descriptor YAML files (extension.yaml, asset.yaml) that
  extend an existing extensible agent with additional tools, instructions, or
  hooks. This skill produces declarative YAML artifacts only — it does NOT
  modify agent source code, add SDK dependencies, or implement runtime
  extensibility. Use this skill when you want to create a new extension
  package for an already-extensible agent via the Agent Extension Editor.
  You can NOT use this skill to make an agent extensible.
metadata:
  version: 1.0.0
  author: sap-joule-studio
---

**This skill operates on the current working directory.** The caller is responsible for running it from the correct target directory (e.g. `assets/agent-extension/`).

## Schema References

All generated files MUST conform to the following JSON Schema definitions:

- **Extension Descriptor** (`extension.yaml`): [assets/extension.json](assets/extension.json)
- **Asset Descriptor** (`asset.yaml`): [assets/asset.json](assets/asset.json)
- **Agent Discovery Response** (response schema reference): [assets/agent-discovery-response.json](assets/agent-discovery-response.json)
  - Fetching all extensible agents returns the full response (`{ agents: [...], pageInfo: {...} }`)
  - Fetching a single extensible agent returns a single agent object (one item from the `agents` array, no `pageInfo`)

### Tool Result → YAML Field Mapping

Use this mapping to translate agent discovery response fields into the generated YAML files.

#### Agent → `extension.yaml`

| Tool Result Field | `extension.yaml` Field |
|---|---|
| `ordId` | `agent.ordId` |
| `systemInstance.globalTenantId` | `agent.identifier` |
| `extensions[].params.capabilityId` | `capabilityImplementations[].capabilityId` |
| `extensions[].params.instructionSupported` | Determines if `capabilityImplementations[].instruction` can be set |
| `extensions[].params.tools.additions.enabled` | Determines if `capabilityImplementations[].tools[]` can be populated |
| `extensions[].params.supportedHooks` | Determines which hooks can be defined in `capabilityImplementations[].hooks[]` |

#### Agent → `asset.yaml`

| Tool Result Field | `asset.yaml` Field |
|---|---|
| `ordId` | `requires[].ordId` (where `type: agent`) |
| `version` | `requires[].version` (where `type: agent`) |

## Step 1: Fetch Agent Information

**IMPORTANT: Always fetch agent information first** to get the correct `ordId` and `version` for the agent to be extended.

#### When User Provides a Specific ordId

If the user provides a specific ordId and version (e.g., `sap.ai:agent:my-agent:v1` & version `1.0.0`), fetch that specific agent via the agent discovery API:

#### Default Query (No ordId Provided)

Fetch all extensible agents via the agent discovery API.

Note that the result can have a next page with a cursor that can be used to fetch more agents if needed. If multiple agents are returned, ask the user to select which agent they want to extend. Present the options clearly with their `ordId`, `version`, and a short description.

#### Handle Multiple Versions

If the query returns **multiple versions** of the same agent (e.g., `sap.ai:agent:my-agent:v1` and `sap.ai:agent:my-agent:v2`), you **MUST ask the user which version to use** before proceeding. Present the versions clearly:

- List each version with its `ordId`, `version`, `description`
- Highlight differences between versions if apparent (e.g., different extension capabilities)
- **Do NOT default to the latest version silently** — always let the user decide

#### Handle Agent Not Found

**CRITICAL — MANDATORY ABORT:** If no agent is found in the extensible agents registry (empty results, the specified `ordId` does not exist, or no matching agent returns):

1. **Do NOT create any extension files** — no `extension.yaml`, no agent-extension folder, no skeleton files
2. **Do NOT use placeholder values** — never write `<REPLACE: ...>`, `<from tool: ...>`, or similar placeholder text
3. **Do NOT invent or guess agent information** — never fabricate `ordId`, `version`, `identifier`, or extension capabilities
4. **Do NOT ask the user to provide the ordId manually** — if the agent is not in the registry, it cannot be extended
5. **SKIP the entire agent extension creation** and inform the user:
   > "The requested agent was not found in the extensible agents registry. Skipping agent extension creation. Only agents that are registered as extensible can be extended via this skill."
6. **Continue with other tasks** in the conversation if applicable (e.g., creating workflows, other assets that don't depend on the agent extension)

**This is a hard stop** — without valid agent data from the discovery API, no extension artifacts can be created. The skill requires real agent metadata; it cannot proceed with placeholders or user-provided values.

## Step 2: Generate `extension.yaml`

Create `assets/<agent-extension>/extension.yaml` conforming to the Extension Descriptor schema ([assets/extension.json](assets/extension.json)).

**Always generate this file immediately as an empty skeleton** — do NOT ask the user what to include first. The skeleton is populated based solely on the extension capabilities returned by the discovery response.

The `capabilityImplementations` structure depends on what the agent's extension capabilities support:

#### If tools AND hooks are supported

(`extensions[].params.tools.additions.enabled` is `true` AND `extensions[].params.supportedHooks` is non-empty)

```yaml
_schema-version: "0.1.0"
kind: Extension

metadata:
  name: "<agent-extension-name>"

agent:
  ordId: "<from tool: ordId>"
  identifier: "<from tool: systemInstance.globalTenantId>"

capabilityImplementations:
  - capabilityId: "<from tool: extensions[].params.capabilityId>"
    tools: []
    hooks: []
```

#### If only tools are supported

(`extensions[].params.tools.additions.enabled` is `true` AND `extensions[].params.supportedHooks` is empty or absent)

```yaml
_schema-version: "0.1.0"
kind: Extension

metadata:
  name: "<agent-extension-name>"

agent:
  ordId: "<from tool: ordId>"
  identifier: "<from tool: systemInstance.globalTenantId>"

capabilityImplementations:
  - capabilityId: "<from tool: extensions[].params.capabilityId>"
    tools: []
```

#### If only hooks are supported

(`extensions[].params.tools.additions.enabled` is `false` or absent AND `extensions[].params.supportedHooks` is non-empty)

```yaml
_schema-version: "0.1.0"
kind: Extension

metadata:
  name: "<agent-extension-name>"

agent:
  ordId: "<from tool: ordId>"
  identifier: "<from tool: systemInstance.globalTenantId>"

capabilityImplementations:
  - capabilityId: "<from tool: extensions[].params.capabilityId>"
    hooks: []
```

#### If neither tools nor hooks are supported

```yaml
_schema-version: "0.1.0"
kind: Extension

metadata:
  name: "<agent-extension-name>"

agent:
  ordId: "<from tool: ordId>"
  identifier: "<from tool: systemInstance.globalTenantId>"

capabilityImplementations:
  - capabilityId: "<from tool: extensions[].params.capabilityId>"
```

**Important rules for extension.yaml:**
- The `capabilityId` must match the `params.capabilityId` from the agent's extension capabilities
- Only include `tools` property if `extensions[].params.tools.additions.enabled` is `true`
- Only include `hooks` property if `extensions[].params.supportedHooks` is non-empty
- Only add `instruction` if `extensions[].params.instructionSupported` is `true`
- Do NOT include `tools` or `hooks` properties at all when the capability does not support them

## Step 3: Setup Solution

After bootstrapping, automatically execute the `setup-solution` skill to create an `asset.yaml` in this same directory with `buildPath: .` and `/.well-known/agent.json` health probes.

**CRITICAL: The `asset.yaml` MUST include a `requires` entry referencing the agent.** This is essential for the platform to understand the dependency. After the `setup-solution` skill generates the initial `asset.yaml`, ensure it contains:

```yaml
requires:
  - name: "<agent-title-kebab-case>"
    type: agent
    version: "<from tool: version>"
    ordId: "<from tool: ordId>"
```

If the `setup-solution` skill does not add this automatically, you MUST add it yourself. The `asset.yaml` is incomplete without the agent dependency.

## Step 4: Configure Hooks for n8n Workflows from Same Solution

This step applies **only when a new n8n workflow was created in the same solution** and needs to be connected as a hook to the agent extension. Skip this step if the workflow comes from UMS (external fetch).

### Step 4.1: Gather Hook Configuration

#### From Base Agent (do NOT ask user)

| Parameter | Source |
|---|---|
| `hookId` | Read from `extensions[].params.supportedHooks` in agent discovery response |

#### Required inputs from User (MANDATORY: Ask user before proceeding)

#### Fast Track Mode
**IMPORTANT:**

In Fast Track Mode, use the **default values** from the table above. Do NOT ask the user for hook configuration parameters.

#### Normal Mode (Non-Fast Track)

**CRITICAL: You MUST ask the user for the following values if they were not explicitly provided in the conversation. Do NOT guess or use default values — always ask the user first.**

**For pre-hooks (hookType = BEFORE) — ask ALL 4 parameters:**

| Parameter | Allowed Values | Description |
|---|---|---|
| `hookType` | `BEFORE` or `AFTER` | BEFORE = pre-hook, AFTER = post-hook |
| `timeout` | `5`, `10`, `30`, `60`, or `120` | Timeout in seconds for hook execution |
| `onFailure` | `BLOCK` or `CONTINUE` | BLOCK (stop on error) or CONTINUE (ignore errors) - block or continue agent execution on error during hook invocation |
| `canShortCircuit` | `true` or `false` | Whether this hook can stop agent execution |

**For post-hooks (hookType = AFTER) — ask only 2 parameters:**

| Parameter | Allowed Values | Description |
|---|---|---|
| `hookType` | `BEFORE` or `AFTER` | BEFORE = pre-hook, AFTER = post-hook |
| `timeout` | `5`, `10`, `30`, `60`, or `120` | Timeout in seconds for hook execution |

For post-hooks, do NOT ask for `onFailure` and `canShortCircuit`. Use default values: `onFailure: CONTINUE` and `canShortCircuit: true`.

**Example prompt for pre-hook (BEFORE):**
> To configure the hook for the workflow, I need the following information:
> 1. **Hook Type**: Should this hook run BEFORE (pre-hook) or AFTER (post-hook) the agent processing?
> 2. **Timeout**: What timeout in seconds? Options: 5, 10, 30, 60, 120
> 3. **On Failure**: If the hook fails, should the agent BLOCK (stop on error) or CONTINUE (ignore errors)?
> 4. **Can Short-Circuit**: Can this hook stop agent execution? (true/false)

**Example prompt for post-hook (AFTER):**
> To configure the hook for the workflow, I need the following information:
> 1. **Hook Type**: Should this hook run BEFORE (pre-hook) or AFTER (post-hook) the agent processing?
> 2. **Timeout**: What timeout in seconds? Options: 5, 10, 30, 60, 120

**Do NOT proceed to Step 4.2 until you have all required values from the user.**

### Step 4.2: Read Workflow Configuration

Fetch workflow asset files from the same solution. Then follow these steps:

1. **Get workflow name**: Read the workflow's `asset.yaml` and extract the name from `provides.workflows[].name` (NOT from `metadata.name`).

2. **Get HTTP method**: 
   - From the workflow's `asset.yaml`, get the file path from `provides.workflows[].file` (or `provides.workflows[].definitionFile` as fallback)
   - Read that workflow JSON file
   - Find the node object that has a `webhookId` property
   - Extract `parameters.httpMethod` from that same node object

3. **Generate hook entry in `extension.yaml`**:

**For pre-hooks (BEFORE):**
```yaml
hooks:
  - hookId: "<from base agent supportedHooks>"
    name: "<descriptive name>"
    hookType: BEFORE
    deploymentType: N8N
    onFailure: "<BLOCK or CONTINUE - from user input>"
    timeout: <5|10|30|60|120 - from user input>
    canShortCircuit: <true|false - from user input>
    n8nWorkflowConfig:
      name: "<from workflow asset.yaml: provides.workflows[].name>"
      method: "<from workflow JSON: parameters.httpMethod of node with webhookId>"
```

**For post-hooks (AFTER):**
```yaml
hooks:
  - hookId: "<from base agent supportedHooks>"
    name: "<descriptive name>"
    hookType: AFTER
    deploymentType: N8N
    onFailure: CONTINUE
    timeout: <5|10|30|60|120 - from user input>
    canShortCircuit: true
    n8nWorkflowConfig:
      name: "<from workflow asset.yaml: provides.workflows[].name>"
      method: "<from workflow JSON: parameters.httpMethod of node with webhookId>"
```

### Step 4.3: Add Workflow Dependency to `asset.yaml`

Add a `requires` entry for the n8n workflow to the agent extension's `asset.yaml`:

```yaml
requires:
  - name: "<agent-title-kebab-case>"
    type: agent
    version: "<from tool: version>"
    ordId: "<from tool: ordId>"
  - name: "<from workflow asset.yaml: provides.workflows[].name>"
    type: n8n-workflow
```

**Important**: The `name` in the requires entry MUST match the `name` from `provides.workflows[].name` in the workflow's `asset.yaml`, NOT the `metadata.name`.

### Hook Configuration Example

Given a workflow folder `assets/n8n/invoice-notification/` with:

**asset.yaml:**
```yaml
apiVersion: asset.sap/v1
kind: Asset
metadata:
  name: invoice-notification-workflow
  version: 1.0.0
type: n8n-workflow
sourceRoot: "workflows"
provides:
  workflows:
    - file: invoice-unmatched.n8n.json
      name: invoice-unmatched-notification
```

**workflows/invoice-unmatched.n8n.json:**
```json
{
  "nodes": [
    {
      "parameters": {
        "httpMethod": "POST",
        "path": "invoice-webhook-abc123"
      },
      "type": "n8n-nodes-base.webhook",
      "webhookId": "abc123-def456"
    }
  ]
}
```

The resulting hook in `extension.yaml`:
```yaml
hooks:
  - hookId: agent_pre_hook
    name: Invoice Notification
    hookType: BEFORE
    deploymentType: N8N
    onFailure: CONTINUE
    timeout: 30
    canShortCircuit: true
    n8nWorkflowConfig:
      name: invoice-unmatched-notification
      method: POST
```

And the `requires` entry in `asset.yaml`:
```yaml
requires:
  - name: base-agent
    type: agent
    version: "1.0.0"
    ordId: "sap.ai:agent:example:v1"
  - name: invoice-unmatched-notification
    type: n8n-workflow
```

## Step 5: Set `extensionUrl`

After the solution is set up, fetch the solution URL to retrieve the platform URL for the solution. Then **add only** the `extensionUrl` field to the existing `metadata` block in `extension.yaml` — do NOT overwrite or remove other metadata fields like `name`:

```yaml
metadata:
  name: "<keep existing>"
  extensionUrl: "<fetched solution URL>"
```

This step MUST be performed as the final step — the `extensionUrl` is the complete host URL for the solution.
