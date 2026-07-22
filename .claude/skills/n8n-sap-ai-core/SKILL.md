---
name: n8n-sap-ai-core
description: Generates SAP AI Core nodes for LLM operations (chat, extraction, summarization, OCR). NEVER use OpenAI/Gemini/HTTP Request for LLM calls - always use SAP AI Core dedicated nodes.
metadata:
  version: 1.0.0
  author: sap-joule-studio
---

# SAP AI Core Node Generation

## Overview

This skill ensures correct generation of **SAP AI Core** nodes for all LLM and AI operations in n8n workflows. It enforces the use of SAP AI Core nodes instead of third-party alternatives.

**Critical Mission:** NEVER use OpenAI, Gemini, or generic HTTP Request nodes for LLM operations. ALWAYS use SAP AI Core dedicated nodes.

---

## CRITICAL RULES (Never Skip)

### 1. **ALWAYS use MCP to get node metadata**

**BEFORE generating an SAP AI Core node:**
```javascript
// Call MCP tool
search-nodes-catalog("sap ai core")
```

**Use the returned values:**
- `name` → use as `type` in workflow
- `version` → use as `typeVersion` in workflow
- `properties` → use to understand available parameters

**NEVER hardcode typeVersion** - always get it from MCP.

---

### 2. **SAP AI Core Available Nodes**

SAP AI Core provides multiple specialized nodes powered by the [SAP Cloud SDK for AI](https://sap.github.io/ai-sdk/docs/js/overview-cloud-sdk-for-ai-js).

**Reference:** For detailed documentation, samples, and advanced workflows, see the [n8n-nodes-sap-ai-core repository](https://github.tools.sap/AP-AC-Products-Services/n8n-nodes-sap-ai-core).

#### Main Nodes (Workflow Operations)

| Node Type | Use Case | Operations | Key Features |
|-----------|----------|------------|--------------|
| **SAP Orchestration** | Chat completion with enterprise features | Chat Completion, Streaming | Content filtering (Azure), data masking (SAP DPI), document grounding, translation, multi-modal (image/file), JSON response, message history |
| **SAP Prompt Registry** | Manage prompt templates | List, Create/Update, Delete templates and configs | Centralized prompt management, version control |
| **SAP Document Grounding** | RAG: collections, documents, search | Collection CRUD, Document Create, Retrieval Search, Pipeline status | Vector-based document retrieval for context |
| **SAP HANA Database** | Execute SQL queries | Select, Insert, Update, Delete, Execute Query | Schema/table picker, parameterized WHERE, query timeout |
| **SAP AI Core Management** | Manage AI Core resources | Deployment, Configuration, Scenario, Model, Artifact, Execution operations | Full lifecycle management of AI Core resources |

#### Sub-Nodes (Connect to AI Agent or Chains)

| Node Type | Output Type | Use Case | Features |
|-----------|-------------|----------|----------|
| **SAP Orchestration Chat Model** | `AiLanguageModel` | Connect to AI Agent v3.1 or LangChain | Model selection, content filtering, data masking, translation, multi-modal |
| **SAP Orchestration Embeddings** | `AiEmbedding` | Generate text embeddings | Text embeddings with optional data masking |
| **SAP HANA Vector Store** | `AiVectorStore` or `AiTool` | Vector storage and retrieval | Insert, Load, Retrieve modes; cosine/euclidean distance; metadata filtering |

#### When to Use Each Node

**For LLM Operations:**
- Use **SAP Orchestration** (main node) for direct chat completion with enterprise features
- Use **SAP Orchestration Chat Model** (sub-node) when connecting to AI Agent v3.1

**For Data Processing:**
- Use **SAP Document Grounding** for RAG workflows (context retrieval)
- Use **SAP HANA Vector Store** for embedding storage and similarity search
- Use **SAP HANA Database** for SQL operations and data preparation

**For Management:**
- Use **SAP Prompt Registry** to centralize and version prompts
- Use **SAP AI Core Management** to manage deployments and configurations

---

### 3. **NEVER Use These Alternatives**

❌ **FORBIDDEN NODES:**
- Any third-party LLM, chat model, assistant, tool, or embeddings node
- Any `@n8n/n8n-nodes-langchain.*` provider node that is not SAP AI Core
- `n8n-nodes-base.httpRequest` for direct LLM API calls

**Examples:**
- `@n8n/n8n-nodes-langchain.openAi`
- `@n8n/n8n-nodes-langchain.anthropic`
- `@n8n/n8n-nodes-langchain.googleGemini`
- `@n8n/n8n-nodes-langchain.lm*` (all LangChain language models)
- `@n8n/n8n-nodes-langchain.embeddings*` (all LangChain embeddings)

✅ **ALWAYS USE:**
- SAP AI Core dedicated nodes only

**Rationale:** SAP AI Core provides:
- Enterprise security and compliance with SAP policies
- Unified billing and monitoring
- Integration with SAP systems
- Centralized model governance

---

### 4. **Enterprise Features (SAP Orchestration)**

SAP AI Core provides enterprise-grade capabilities not available in third-party nodes:

| Feature | Description | Use Case |
|---------|-------------|----------|
| **Content Filtering** | Azure Content Safety integration | Filter harmful content (hate, violence, self-harm, sexual) |
| **Data Masking** | SAP Data Privacy Integration (DPI) | Mask/anonymize PII in prompts and responses |
| **Document Grounding** | RAG with SAP Document Grounding | Add context from vector collections |
| **Translation** | SAP Translation Hub integration | Input/output translation (multi-language) |
| **Multi-modal** | Image and file input support | Analyze images, process documents |
| **Streaming** | Real-time response streaming | Long responses with progressive output |
| **JSON Response** | Structured output format | Extract structured data reliably |

**Why these matter:**
- **Content Filtering** + **Data Masking** = compliance-ready workflows
- **Document Grounding** = production RAG without custom setup
- **Translation** = global workflows with automatic localization

---

### 5. **Mandatory Parameters (SAP Orchestration)**

**Always required:**
- `prompt` (string or expression) - The input prompt/question
- `model` (string) - A model identifier supported by the configured SAP AI Core setup

**Prompt Sources (choose one):**
- **Inline** - Direct prompt text or expression
- **Prompt Registry Template** - Reference a stored template with placeholders
- **Orchestration Config** - Reference a pre-configured orchestration setup

**Recommended:**
- Resolve the available model values from node metadata or environment-specific configuration
- Do not assume public provider model IDs are always available

**Optional but recommended:**
- `temperature` (number 0-1) - Creativity level (default: 0.7)
- `maxTokens` (number) - Maximum response length
- `systemMessage` (string) - System prompt for behavior control

**Enterprise features (optional):**
- Enable only the feature parameters exposed by the current node metadata from MCP
- Do not assume parameter names are identical across node versions or environments
- Common enterprise features may include content filtering, data masking, document grounding, and translation — but always verify exact parameter names via MCP `properties` before using them

**Example:**
```json
{
  "type": "<SAP Orchestration from MCP>",
  "typeVersion": "<from MCP>",
  "name": "Analyze Invoice",
  "parameters": {
    "prompt": "={{ $json.invoiceText }}",
    "model": "<supported SAP AI Core model>",
    "temperature": 0.3,
    "systemMessage": "You are an expert invoice analyst.",
    "<enterprise-feature-key-from-MCP>": "<value confirmed by MCP metadata>"
  },
  "credentials": {
    "sapAiCoreApi": {
      "id": "<from get-credentials>",
      "name": "<from get-credentials>"
    }
  }
}
```

---

## Common Use Cases & Patterns

### Use Case 1: Simple Chat Completion
**Workflow:** `Webhook → SAP Orchestration → Respond`

Use **SAP Orchestration** for basic chat:
```json
{
  "type": "<SAP Orchestration from MCP>",
  "parameters": {
    "operation": "chatCompletion",
    "prompt": "={{ $json.userMessage }}",
    "model": "<supported SAP AI Core model>"
  }
}
```

### Use Case 2: Chat with Enterprise Safety
**Workflow:** `Webhook → SAP Orchestration (with filtering + masking) → Respond`

Add content filtering and data masking for compliance:
```json
{
  "type": "<SAP Orchestration from MCP>",
  "parameters": {
    "prompt": "={{ $json.userMessage }}",
    "model": "<supported SAP AI Core model>",
    "contentFiltering": {
      "enabled": true,
      "hate": 0,
      "violence": 0,
      "selfHarm": 0,
      "sexual": 0
    },
    "dataMasking": {
      "method": "anonymization",
      "entities": ["email", "phone", "person"]
    }
  }
}
```

### Use Case 3: RAG with Document Grounding
**Workflow:** `Webhook → SAP Document Grounding (search) → SAP Orchestration (grounded chat) → Respond`

1. Search documents:
```json
{
  "type": "<SAP Document Grounding from MCP>",
  "parameters": {
    "operation": "search",
    "query": "={{ $json.question }}",
    "collectionId": "my-docs"
  }
}
```

2. Chat with context:
```json
{
  "type": "<SAP Orchestration from MCP>",
  "parameters": {
    "prompt": "={{ $json.question }}",
    "model": "<supported SAP AI Core model>",
    "grounding": {
      "documents": "={{ $json.searchResults }}"
    }
  }
}
```

### Use Case 4: AI Agent with SAP Chat Model
**Workflow:** `AI Agent (v3.1) → SAP Orchestration Chat Model (sub-node)`

Use **SAP Orchestration Chat Model** (sub-node) to connect to AI Agent:
```json
{
  "type": "<SAP Orchestration Chat Model from MCP>",
  "parameters": {
    "model": "<supported SAP AI Core model>",
    "contentFiltering": true,
    "dataMasking": { "method": "anonymization" }
  }
}
```
**Note:** AI Agent v3.1+ required. v1.x blocks custom nodes.

### Use Case 5: Embeddings + Vector Store
**Workflow:** `Data → SAP Orchestration Embeddings → SAP HANA Vector Store`

Generate and store embeddings:
```json
{
  "type": "<SAP Orchestration Embeddings from MCP>",
  "parameters": {
    "text": "={{ $json.document }}",
    "dataMasking": true
  }
}
```

### Use Case 6: Multi-modal (Image Analysis)
**Workflow:** `Webhook → SAP Orchestration (with image) → Respond`

Analyze images with vision models:
```json
{
  "type": "<SAP Orchestration from MCP>",
  "parameters": {
    "prompt": "What's in this image?",
    "model": "<vision model>",
    "imageUrl": "={{ $json.imageUrl }}"
  }
}
```

---

## Common Use Cases (Legacy - for reference)

**Prompt:** "Parse invoice and extract data"

**Solution:** Use SAP AI Core Information Extractor
```json
{
  "type": "<SAP AI Core Information Extractor from MCP>",
  "parameters": {
    "inputText": "={{ $json.documentText }}",
    "extractionSchema": {
      "invoiceNumber": "string",
      "totalAmount": "number",
      "date": "date",
      "vendor": "string"
    }
  }
}
```

### Use Case 2: Text Analysis

**Prompt:** "Analyze text for errors or classification"

**Solution:** Use SAP AI Core Model Chat
```json
{
  "type": "<SAP AI Core Model Chat from MCP>",
  "parameters": {
    "prompt": "Analyze this text: {{ $json.text }}",
    "model": "<supported SAP AI Core model>",
    "temperature": 0.2
  }
}
```

---

## Common Errors to Avoid

❌ **ERROR 1:** Using third-party LLM nodes
```json
"type": "@n8n/n8n-nodes-langchain.openAi"        // WRONG
"type": "@n8n/n8n-nodes-langchain.anthropic"    // WRONG
"type": "@n8n/n8n-nodes-langchain.googleGemini" // WRONG
"type": "n8n-nodes-base.mistralAi"              // WRONG
```
✅ **FIX:** Use SAP AI Core node from MCP

---

❌ **ERROR 2:** Generic HTTP Request for LLM
```json
{
  "type": "n8n-nodes-base.httpRequest",
  "parameters": {
    "url": "https://api.openai.com/v1/chat/completions"  // WRONG
  }
}
```
✅ **FIX:** Use SAP AI Core dedicated node

---

❌ **ERROR 3:** LangChain LM or Embeddings nodes
```json
"type": "@n8n/n8n-nodes-langchain.lmChatOpenAi"     // WRONG
"type": "@n8n/n8n-nodes-langchain.embeddingsOpenAi" // WRONG
```
✅ **FIX:** Use **SAP Orchestration** for direct LLM operations, or **SAP Orchestration Chat Model** when connecting to AI Agent. Legacy workflows may still reference **SAP AI Core Model Chat**.

---

## Testing Checklist

Before considering an SAP AI Core workflow complete:

- [ ] Used MCP `search-nodes-catalog("sap ai core")` or `search-nodes-catalog("sap orchestration")` to get node metadata
- [ ] Selected correct SAP AI Core node type:
  - **SAP Orchestration** for main chat operations
  - **SAP Orchestration Chat Model** for AI Agent connections
  - **SAP Orchestration Embeddings** for vector operations
  - **SAP Document Grounding** for RAG workflows
  - **SAP HANA Vector Store** for embedding storage
- [ ] NEVER used forbidden nodes (see complete list in section 3)
- [ ] All mandatory parameters provided (prompt, model)
- [ ] Model identifier is dynamic `"<supported SAP AI Core model>"`, not hardcoded
- [ ] Credentials resolved via `get-credentials` MCP with `type: "sapAiCoreApi"`
- [ ] Enterprise features configured if needed (contentFiltering, dataMasking, grounding, translation)
- [ ] For AI Agent: using v3.1+ (v1.x blocks custom nodes)
- [ ] Workflow validates with `validate-n8n-workflow` MCP

## Additional Resources

- **Full documentation & samples:** [n8n-nodes-sap-ai-core repository](https://github.tools.sap/AP-AC-Products-Services/n8n-nodes-sap-ai-core)
- **SDK reference:** [SAP Cloud SDK for AI](https://sap.github.io/ai-sdk/docs/js/overview-cloud-sdk-for-ai-js)
- **Sample workflows:** See `samples/` directory in the repository (20+ ready-to-import examples)
- **Advanced workflows:** See `workflows/` directory for multi-step patterns
