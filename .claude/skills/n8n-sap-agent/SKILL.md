---
name: n8n-sap-agent
description: Generates SAP Agent node for agent orchestration from n8n workflows. Handles agent invocation, parameter passing, response processing, and multi-agent coordination.
metadata:
  version: 1.0.0
  author: sap-joule-studio
---

# SAP Agent Node Generation

## Overview

This skill ensures correct generation of **SAP Agent** nodes for orchestrating AI agents from n8n workflows. It covers agent invocation, parameter passing, response handling, and integration with other workflow nodes.

**Critical Mission:** Enable workflows to invoke SAP AI agents and process their responses, creating powerful agent-workflow orchestration patterns.

---

## CRITICAL RULES (Never Skip)

### 1. **ALWAYS use MCP to get node metadata**

**BEFORE generating an SAP Agent node:**
```javascript
// Call MCP tool
search-nodes-catalog("SAP Agent")
// If not found, fallback to:
search-nodes-catalog("AI Agent")
```

**Use the returned values:**
- `name` → use as `type` in workflow
- `version` → use as `typeVersion` in workflow
- `properties` → use to understand available parameters

**NEVER hardcode typeVersion** - always get it from MCP.

---

### 2. **SAP Agent Node Purpose**

The SAP Agent node allows workflows to:
- ✅ Invoke AI agents from n8n
- ✅ Pass context and parameters to agents
- ✅ Receive agent responses
- ✅ Process agent outputs in subsequent nodes
- ✅ Orchestrate multiple agents in sequence

---

### 3. **Mandatory Parameters**

**Always required:**
- `agentName` (string) - Name or ID of the agent to invoke
- `input` - Prefer a string for prompt-style input. Use an expression that resolves to an object only when the MCP `properties` metadata explicitly supports structured input.
- `sessionId` (string, optional but recommended) - Preserves conversation continuity

**Guidance:**
- Use a string expression for prompt-style inputs
- Use an expression that resolves to an object only when confirmed by `properties` from MCP
- Note: expression syntax `={{ ... }}` is distinct from JSON shape — the expression is evaluated at runtime to produce the actual object

**Example with string input (preferred for prompts):**
```json
{
  "type": "<from MCP>",
  "typeVersion": "<from MCP>",
  "name": "Invoke AP Invoice Agent",
  "parameters": {
    "agentName": "ap-invoice-agent",
    "input": "={{ $json.invoiceText }}",
    "sessionId": "={{ $json.requestId }}"
  },
  "credentials": {
    "sapAgentApi": {
      "id": "<from get-credentials>",
      "name": "<from get-credentials>"
    }
  }
}
```

**Example with object input (only if MCP properties confirm support):**
```json
{
  "type": "<from MCP>",
  "typeVersion": "<from MCP>",
  "name": "Invoke AP Invoice Agent",
  "parameters": {
    "agentName": "ap-invoice-agent",
    "input": "={{ ({ invoiceData: $json.invoiceData, source: $json.source }) }}",
    "sessionId": "={{ $json.requestId }}"
  },
  "credentials": {
    "sapAgentApi": {
      "id": "<from get-credentials>",
      "name": "<from get-credentials>"
    }
  }
}
```

---

### 4. **Agent Orchestration Patterns**

#### Pattern 1: Single Agent Invocation
```
Webhook → SAP Agent → Process Response → Respond
```

#### Pattern 2: Sequential Multi-Agent
```
Trigger → Agent 1 (Analysis) → Agent 2 (Action) → Agent 3 (Verification) → Done
```

#### Pattern 3: Agent + HITL (Human-in-the-Loop)
```
Webhook → SAP Agent → Check Confidence → [Low: Task Center, High: Auto-process]
```

#### Pattern 4: Agent + Error Handling
```
Webhook → SAP Agent → Check Result → [Error: Task Center for manual review]
```

---

### 5. **Agent Response Handling**

Agent responses typically include:
```json
{
  "response": "...",      // Agent's text response
  "confidence": 0.95,     // Confidence score
  "actions": [],          // Actions taken by agent
  "metadata": {}          // Additional context
}
```

**Always check confidence/success before proceeding:**
```json
{
  "type": "n8n-nodes-base.if",
  "name": "Check Confidence",
  "parameters": {
    "conditions": {
      "conditions": [{
        "leftValue": "={{ $json.confidence }}",
        "rightValue": 0.8,
        "operator": { "type": "number", "operation": "gte" }
      }]
    }
  }
}
```

---

## Common Errors to Avoid

❌ **ERROR 1:** Missing agent credentials
```json
{
  "type": "<SAP Agent>",
  "parameters": { ... }
  // Missing credentials
}
```
✅ **FIX:** Add credentials from `get-credentials` MCP

---

❌ **ERROR 2:** Not handling agent errors
```
Agent → Process Response → Done
// No error handling
```
✅ **FIX:** Add error/confidence check
```
Agent → Check Success → [Error: Notify/Escalate, Success: Continue]
```

---

❌ **ERROR 3:** Losing context in multi-agent flows
```json
"input": "={{ $json.data }}"  // Only has current node data
```
✅ **FIX:** Reference previous nodes
```json
"input": {
  "currentData": "={{ $json.data }}",
  "previousAnalysis": "={{ $('Agent 1').item.json.analysis }}"
}
```

---

## Testing Checklist

Before considering an Agent workflow complete:

- [ ] Used MCP `search-nodes-catalog` to get typeVersion
- [ ] Agent name matches existing agent
- [ ] Input parameters properly formatted
- [ ] Credentials resolved via `get-credentials` MCP
- [ ] Response processing logic in place
- [ ] Error handling implemented
- [ ] Workflow validates with `validate-n8n-workflow` MCP

---

## Integration with Other Skills

**Agent + Task Center:**
```
Agent → Check Confidence → [Low: Task Center HITL, High: Auto-process]
```
See: `n8n-sap-task-center` skill

**Agent + AI Core:**
```
AI Core (Analysis) → Agent (Action) → AI Core (Verification)
```
See: `n8n-sap-ai-core` skill
