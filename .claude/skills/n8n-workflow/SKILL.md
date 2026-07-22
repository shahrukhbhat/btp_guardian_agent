---
name: n8n-workflow
description: "Writes or edits n8n workflow JSON files (.n8n.json). This skill is invoked only through the orchestration chain: `intent-analysis` → `specification` → `n8n-workflow`, or when updating an existing n8n workflow."
metadata:
  version: 1.0.0
  author: sap-joule-studio
---

# n8n Workflow Skill

## ⛔ CRITICAL RULES — read first, always follow

1. **NEVER** use `$env.*` variables in workflow JSON → use placeholder URLs like `https://your-sap-system.com/api`
2. **ALWAYS** use `.n8n.json` extension and write to `assets/n8n/workflows/`
3. **DO NOT explain credentials, deployment, or setup steps in your response.** The n8n platform automatically detects missing credentials when the workflow is imported and prompts users via its UI. Mentioning this in your response is redundant and creates noise. Keep your response focused on what was created — not what the user needs to do next.
4. **ALL changes to a workflow file MUST be applied in a single `Write` call.** Never use multiple sequential `Edit` or `Write` calls on the same file — doing so leaves the file in an invalid/incomplete state between writes, which can corrupt the workflow. Construct the entire final JSON in memory first, validate it (see rule 5), then write it once. This applies equally to new files and edits to existing ones.
5. **Validate the workflow JSON BEFORE writing the file.** Use the `validate-n8n-workflow` MCP tool on the fully constructed JSON before any file write. Only proceed with the `Write` call after validation passes (or after understanding which errors are expected and acceptable, e.g. missing credential IDs).

## Invocation Rule

**Do not invoke this skill directly from an end-user request.**

This skill must be reached only through the orchestration flow:
`intent-analysis` → `specification` → `n8n-workflow`

## Key Constraints

- **NEVER** answer with the n8n URL in the message
- The n8n workflow asset is always named `n8n` and always uses `type: n8n-workflow`.
- Do not derive its type from naming conventions.

## References

- [workflows-hooks.md](./references/workflows-hooks.md): ONLY read this file when the user is creating a **pre-hook or post-hook** workflow for an **agent extension** scenario. It contains the A2A message protocol, hook-specific response patterns, and examples. Do NOT read it for regular (non-hook) workflows.

## SAP Node Detailed Rules (Conditional Loading)

**IMPORTANT:** Before generating any workflow, analyze the user prompt. Invoke SAP subskills only when the request clearly requires SAP-specific nodes or SAP system integration.

| Signal type | Examples | Action |
|-------------|----------|--------|
| Strong SAP-specific signals | `SAP Task Center`, `SAP AI Core`, `SAP Agent`, `SAP MCP Client`, `S/4HANA` | Invoke the matching subskill (`n8n-sap-task-center`, `n8n-sap-ai-core`, `n8n-sap-agent`, `n8n-sap-mcp-client`) |
| Contextual SAP workflow signals | `purchase requisition in S/4HANA`, `SAP sales order`, `SAP invoice extraction`, `approval in SAP Task Center`, `SAP agent orchestration`, `extract with SAP AI Core` | Invoke the matching subskill |
| Ambiguous generic terms | `task`, `AI`, `analyze`, `agent`, `chat`, `approval`, `invoice`, `purchase requisition`, `sales order` (without SAP context) | Do not invoke a SAP subskill unless SAP context is explicit |

**Mapping table:**
- **SAP Task Center** → `n8n-sap-task-center` skill (mandatory Switch node pattern after Task Center)
- **SAP AI Core** → `n8n-sap-ai-core` skill (NEVER use OpenAI/Gemini, only SAP AI Core nodes)
- **SAP Agent** → `n8n-sap-agent` skill (SAP Agent node configuration and credentials)
- **SAP MCP Client / S/4HANA** → `n8n-sap-mcp-client` skill (NEVER use HTTP Request for SAP, use MCP Client)

**Example:** User prompt "Create workflow with SAP Agent for approval escalation"
→ Detected strong signals: "SAP Agent", contextual signal: "approval escalation"
→ MUST invoke: `n8n-sap-agent`, `n8n-sap-task-center`
→ Read and follow ALL rules from both skill files

## Steps

### **Set up project folder**
If a ``assets/n8n/workflows/`` folder already exists, use it. Otherwise create it by executing:
   ```bash
   mkdir -p assets/n8n/workflows/
   ```

### **MANDATORY: Setup the Solution**
If not already run, run the `setup-solution` skill. The final `assets/n8n/asset.yaml` should follow the template provided in [./assets/asset.yaml](./assets/asset.yaml) (co-located with this SKILL.md). The n8n asset should be named `n8n` and use `type: n8n-workflow` — avoid deriving these from project naming conventions. When part of a multi-asset solution, add the following entry to `solution.yaml`:
```yaml
  - ref: ./assets/n8n/asset.yaml
```

### **MANDATORY: Look up nodes from the catalog (MUST do this for EVERY node)**
Before writing ANY node in the workflow JSON, you MUST look up its exact `type` and `typeVersion` from the catalog. Call the `search-nodes-catalog` MCP tool for all nodes you need:
   - Pass all needed node keywords in a single call (e.g. `webhook`, `http request`, `if`, `schedule`, `slack`, `task center`). The tool searches by displayName, name, and description.
   - Use the returned `name` as `type` and `version` as `typeVersion` in the workflow JSON.
   - NEVER guess node type names or typeVersion — only use values returned by the tool.
   - Custom/SAP nodes (e.g. `CUSTOM.approvalTask`) include full `properties` in the output to help you configure them correctly.

### **MANDATORY: Create the file in the filesystem**
Write workflow files to `assets/n8n/workflows/` only. The file **MUST** use the `.n8n.json` extension (for example, `my-workflow.n8n.json`). NEVER use MCP to create or update workflow files.

Every workflow JSON **MUST** include a `"description"` field at the top level that accurately summarises what the workflow does. When editing an existing workflow, update the `"description"` to reflect any changes made.

### **MANDATORY: Resolve credentials before writing the workflow**
Any node that requires authentication (HTTP Request, SAP AI Core, SAP Task Center, etc.) needs a `credentials` field with a real credential `id` and `name` from the n8n instance. **Never leave credential fields as empty strings, placeholders, or omit them — always resolve them first.**

1. Call the `get-credentials` MCP tool with only `global: true` — do **not** pass `type` or `name` filters. Fetching all global credentials at once gives you the full picture and makes it easier to match each credential to the nodes that need it.
2. From the returned list, match each node to the credential whose `type` corresponds to that node's expected credential type. If no match exists, do **not** fall back to non-global ones — inform the user that a global credential of the required type must be created first.
3. In the workflow JSON, set the node's `credentials` field using the returned `id` and `name`:

```json
"credentials": {
  "<credentialType>": {
    "id": "<id from get-credentials>",
    "name": "<name from get-credentials>"
  }
}
```

**Example** — assigning an SAP AI Core credential to a node:
```json
"credentials": {
  "sapAiCoreApi": {
    "id": "aB3xY9z",
    "name": "SAP AI Core (Global)"
  }
}
```

If `get-credentials` returns no results, **omit the `credentials` field entirely** and inform the user that a global credential of the required type must be created in the n8n UI. Never guess, invent, or reuse a credential from a different node or example — an absent credential is always preferable to an invalid one.

### **Validate the workflow**
After writing the workflow file, use the `validate-n8n-workflow` MCP tool to validate the workflow content. If validation returns errors, use the message to fix the workflow. Some errors are expected and cannot be fixed by the agent — for example, credentials with `"id": "MISSING"`. In those cases, inform the user and leave the credential configuration to be done manually in the n8n UI.

### **Delete workflows**
Use the n8n MCP tool only for deletion from the remote n8n instance.

## CRITICAL: Node parameter rules

- **ONLY use parameters exactly as shown in the example below**. NEVER invent or add extra parameters.
- **NEVER use n8n environment variables** (e.g. `$env.MY_VAR`, `={{ $env.SOME_VALUE }}`) in any generated workflow.
- **NEVER include post-import setup instructions in your response** — do NOT mention configuring credentials, updating placeholders, deploying, or any setup step. The workflow file is the only deliverable. Your response must end immediately after stating where the workflow was created. Forbidden phrases include: "Before deploying", "Configuration Required", "You'll need to configure", "Before activating", "Update the placeholders", "Credentials Required", or any similar guidance.
- **Webhook `path` MUST be `{workflow-slug}-{webhookId}`** (e.g. workflow `"Travel Expense Approval"` + webhookId `"e57d9e77-..."` → path `"travel-expense-e57d9e77-..."`). Never use a plain human-readable string. Never reuse UUIDs from examples — always generate a fresh UUID v4 for each webhook.
- **SAP Task Center recipients**: At least one recipient target must be configured (`recipients` or `recipientGroups`). For exact parameter shapes, defaults, and naming rules, follow the dedicated `n8n-sap-task-center` skill.

**For SAP-specific nodes:** All detailed rules (parameters, credentials, patterns) are in the dedicated SAP skills. Always invoke the appropriate skill (`n8n-sap-task-center`, `n8n-sap-ai-core`, `n8n-sap-agent`, `n8n-sap-mcp-client`) as indicated by the routing table above.
