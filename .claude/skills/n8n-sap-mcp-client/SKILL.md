---
name: n8n-sap-mcp-client
description: Generates SAP MCP Client nodes for invoking SAP MCP server tools from n8n workflows. ALWAYS use this node — never use HTTP Request — whenever the workflow needs to contact S/4HANA or any SAP MCP server.
metadata:
  version: 1.0.0
  author: sap-joule-studio
---

# SAP MCP Client Node Generation

## Overview

This skill ensures correct generation of **SAP MCP Client** nodes for calling SAP MCP (Model Context Protocol) server tools from n8n workflows. It covers server selection, tool invocation, argument passing, response handling, and integration patterns.

**Critical Mission:** ALWAYS use the SAP MCP Client node — never use a generic HTTP Request node — whenever the workflow needs to contact S/4HANA or any SAP system exposed via an MCP server. This node handles authentication automatically via the configured SAP Agent Gateway credential.

---

## CRITICAL RULES (Never Skip)

### 1. **ALWAYS use MCP to get node metadata**

**BEFORE generating an SAP MCP Client node:**
```javascript
// Call MCP tool
search-nodes-catalog("SAP MCP Client")
```

**Use the returned values:**
- `name` → use as `type` in workflow
- `version` → use as `typeVersion` in workflow
- `properties` → use to understand available parameters

**NEVER hardcode typeVersion** — always get it from MCP.

---

### 2. **This Node is MANDATORY for All S/4HANA Interactions**

**Whenever the workflow needs to:**
- ✅ Read or write data in S/4HANA (GL accounts, purchase orders, vendors, customers, materials, etc.)
- ✅ Invoke any SAP business function or API exposed via MCP
- ✅ Query SAP Finance, Procurement, HR, or any other SAP module
- ✅ Execute SAP transactions or operations programmatically

**NEVER use these alternatives for SAP/S/4HANA calls:**
- ❌ `n8n-nodes-base.httpRequest` (generic HTTP Request node)
- ❌ Any OData/REST calls directly to SAP endpoints
- ❌ RFC/BAPI nodes that bypass MCP

**Rationale:** The SAP MCP Client handles authentication (SAP Agent Gateway credential), protocol translation, and error handling for all SAP system interactions.

---

### 3. **Mandatory Parameters**

**Always required:**
- `mcpServer` (string) — ID of the SAP MCP server (from dropdown / catalog)
- `toolSelectionMode` (enum) — `"list"` (browse) or `"id"` (direct)
- When `toolSelectionMode = "list"`: `tool` (string) — tool selected from dropdown
- When `toolSelectionMode = "id"`: `toolId` (string) — exact tool ID (case-sensitive)
- `inputMode` (enum) — `"json"` (recommended) or `"manual"`
- When `inputMode = "json"`: `toolArguments` (object/expression) — JSON arguments
- When `inputMode = "manual"`: `manualArguments` (array of `{ name, value }` pairs)

**Example — JSON mode (recommended):**
```json
{
  "type": "<from MCP>",
  "typeVersion": "<from MCP>",
  "name": "Get GL Account Balance",
  "parameters": {
    "mcpServer": "sap-finance-server",
    "toolSelectionMode": "list",
    "tool": "get_gl_account_balance",
    "inputMode": "json",
    "toolArguments": {
      "accountNumber": "={{ $json.accountNumber }}",
      "companyCode": "1000",
      "fiscalYear": "={{ $json.fiscalYear }}"
    }
  },
  "credentials": {
    "sapClientCredentials": {
      "id": "<from get-credentials>",
      "name": "SAP Agent Gateway|sap.agw"
    }
  }
}
```

---

### 4. **Credential Resolution**

The SAP MCP Client node uses the `sapClientCredentials` credential type. Generated workflows must explicitly include the resolved credential reference.

When resolving credentials via `get-credentials` MCP:
- Look for credentials with `type: "sapClientCredentials"`
- Prefer a credential whose name contains `"SAP Agent Gateway"` or `"sap.agw"`
- Use the returned `id` and `name` exactly as provided

```json
"credentials": {
  "sapClientCredentials": {
    "id": "<id from get-credentials>",
    "name": "<name from get-credentials>"
  }
}
```

---

### 5. **Tool Selection Modes**

| Mode | Parameter | When to Use |
|------|-----------|-------------|
| `"list"` | `tool` (dropdown value) | During development; tool names are discovered interactively |
| `"id"` | `toolId` (exact string) | In production or when tool ID is known from documentation |

**Always prefer `"list"` mode** during initial workflow generation so tool names are validated against the live server catalog.

---

### 6. **Input Modes**

| Mode | Parameter | When to Use |
|------|-----------|-------------|
| `"json"` | `toolArguments` (JSON object) | Default — supports nested structures and n8n expressions |
| `"manual"` | `manualArguments` (key-value array) | Simple single-value arguments; avoids JSON syntax |

**Always prefer `"json"` mode** for complex arguments or when using n8n expressions.

**JSON mode with expressions:**
```json
{
  "inputMode": "json",
  "toolArguments": {
    "vendor": "={{ $json.vendor_id }}",
    "items": "={{ $json.order_items }}",
    "deliveryDate": "={{ $json.requested_delivery_date }}",
    "purchasingGroup": "001"
  }
}
```

**Manual mode:**
```json
{
  "inputMode": "manual",
  "manualArguments": {
    "values": [
      { "name": "employeeId", "value": "={{ $json.emp_id }}" },
      { "name": "includePayroll", "value": "true" }
    ]
  }
}
```

---

### 7. **Additional Options**

These are optional but should be applied when relevant:

| Option | Type | Default | When to Set |
|--------|------|---------|-------------|
| `requestId` | string | auto UUID | Set for traceability in production: `"={{ $workflow.id }}-{{ $itemIndex }}"` |
| `timeout` | number (ms) | 30000 | Increase for batch/complex operations; decrease for quick lookups |
| `includeRequestMetadata` | boolean | false | Enable during development/debugging |
| `continueOnFail` | boolean | false | Enable when the SAP call is optional or errors are handled downstream |

---

## Common Use Cases

### Use Case 1: Read SAP Finance Data

**Scenario:** Retrieve GL account balance

```json
{
  "parameters": {
    "mcpServer": "sap-finance-server",
    "toolSelectionMode": "list",
    "tool": "get_gl_account_balance",
    "inputMode": "json",
    "toolArguments": {
      "accountNumber": "100000",
      "companyCode": "1000",
      "fiscalYear": "2026"
    }
  }
}
```

### Use Case 2: Create SAP Document from Workflow Data

**Scenario:** Create a purchase order using data from a previous node

```json
{
  "parameters": {
    "mcpServer": "sap-procurement-server",
    "toolSelectionMode": "id",
    "toolId": "create_purchase_order",
    "inputMode": "json",
    "toolArguments": {
      "vendor": "={{ $json.vendor_id }}",
      "items": "={{ $json.order_items }}",
      "deliveryDate": "={{ $json.requested_delivery_date }}",
      "purchasingGroup": "001"
    }
  }
}
```

### Use Case 3: Batch Processing SAP Records

**Scenario:** Process a list of SAP records using Split in Batches + SAP MCP Client

```
Trigger → Get List → Split in Batches → SAP MCP Client → Merge → Respond
```

### Use Case 4: Conditional SAP Operation

**Scenario:** Only create a PO if inventory is below threshold

```
Trigger → Check Inventory (SAP MCP Client) → IF (low stock) → Create PO (SAP MCP Client)
                                                             → ELSE → Continue
```

---

## Response Handling

The node outputs the tool's response under `$json.result`. If metadata is enabled, `$json.metadata` is also available.

```json
{
  "result": {
    // Tool-specific SAP response data
  },
  "metadata": {  // Only if includeRequestMetadata = true
    "serverId": "sap-finance-server",
    "toolId": "get_gl_account_balance",
    "requestId": "uuid-here",
    "executionTime": 1234
  }
}
```

**Accessing response data in subsequent nodes:**
```
{{ $json.result }}                    → full tool response
{{ $json.result.balance }}            → specific field
{{ $json.metadata.executionTime }}    → execution time (if metadata enabled)
```

---

## Common Errors to Avoid

❌ **ERROR 1:** Using HTTP Request instead of SAP MCP Client for S/4HANA
```json
{
  "type": "n8n-nodes-base.httpRequest",
  "parameters": { "url": "https://s4hana.example.com/..." }
}
```
✅ **FIX:** Always use SAP MCP Client node for S/4HANA and SAP MCP server calls

---

❌ **ERROR 2:** Hardcoded `typeVersion`
```json
{ "typeVersion": 1 }  // WRONG — may be outdated
```
✅ **FIX:** Always call `search-nodes-catalog("SAP MCP Client")` and use the returned version

---

❌ **ERROR 3:** Missing or invalid credentials
```json
{ "credentials": {} }  // WRONG
```
✅ **FIX:** Always resolve `sapClientCredentials` via `get-credentials` MCP

---

❌ **ERROR 4:** Wrong tool ID (typo or wrong case)
```json
{ "toolId": "Get_GL_Account_Balance" }  // WRONG — tool IDs are case-sensitive
```
✅ **FIX:** Use `"list"` mode during development to confirm exact tool IDs; switch to `"id"` mode in production only after confirming

---

❌ **ERROR 5:** Not handling SAP tool errors
```
SAP MCP Client → Process Data → Done  // No error handling
```
✅ **FIX:** Add error checking or set `continueOnFail: true` with downstream error handling
```
SAP MCP Client → IF (error?) → [Error: Notify/Task Center, Success: Continue]
```

---

## Integration Patterns

### Pattern 1: Sequential SAP Operations
```
Trigger → Get Customer (SAP MCP) → Get Orders (SAP MCP) → Create Invoice (SAP MCP) → Notify
```

### Pattern 2: SAP MCP + Agent Orchestration
```
Webhook → SAP Agent → SAP MCP Client (execute SAP action) → Respond
```
See: `n8n-sap-agent` skill

### Pattern 3: SAP MCP + Human Approval
```
Trigger → SAP MCP (fetch data) → Task Center (approve) → SAP MCP (execute) → Notify
```
See: `n8n-sap-task-center` skill

### Pattern 4: Error Recovery with Retry
```
Trigger → SAP MCP Client (continueOnFail: true) → IF (error) → Wait → Retry → Continue
```

---

## Testing Checklist

Before considering a workflow with SAP MCP Client complete:

- [ ] Used MCP `search-nodes-catalog` to get `type` and `typeVersion`
- [ ] SAP MCP Client used for ALL S/4HANA / SAP system calls (no HTTP Request substitutes)
- [ ] `mcpServer` ID selected from the live catalog (not hardcoded)
- [ ] Tool confirmed via `"list"` mode before using `"id"` mode
- [ ] All tool arguments provided with correct data types
- [ ] Credentials resolved via `get-credentials` MCP (`sapClientCredentials`)
- [ ] Error handling in place (error branch or `continueOnFail`)
- [ ] Workflow validates with `validate-n8n-workflow` MCP
