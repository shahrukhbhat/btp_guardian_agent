---
name: sap-aeval-framework
description: Aeval evaluation framework toolkit for generating tool schemas, test cases, and selecting relevant tools for agent evaluation scenarios.
metadata:
  version: 1.0.0
  author: sap-joule-studio
---

# Aeval Framework

A unified toolkit for agent evaluation using the Aeval framework. This skill provides three operations:

| Operation | Description | Output |
|-----------|-------------|--------|
| **Generate Tool Schema** | Extract tool definitions from a Python agent codebase | `tools.json` |
| **Generate Test Cases** | Create eval criteria and YAML test cases from a PRD | `aeval/eval.yaml`, `aeval/testcases/*.yaml` |
| **Select Tools** | Filter tools relevant to a specific evaluation scenario | `tools-selected.json` |

---

## Quick Start

### Generate Tool Schema
Extract tool definitions from your agent's Python codebase:
```
sap-aeval-framework generate-tool-schema <codebase_path>
```

### Generate Test Cases
Create evaluation test cases from a PRD:
```
sap-aeval-framework generate-testcase <prd_path> <tools_path_or_code_path> [num_testcases]
```

### Select Tools
Filter tools for a specific scenario (uses context from conversation):
```
sap-aeval-framework select-tools
```

---

## Routing

If arguments were passed via `$ARGUMENTS`, parse the first token as the operation:

| First Argument | Action |
|----------------|--------|
| `generate-tool-schema` | Follow [Generate Tool Schema Procedure](procedures/generate-tool-schema.md) with remaining args |
| `generate-testcase` | Follow [Generate Test Cases Procedure](procedures/generate-testcase.md) with remaining args |
| `select-tools` | Follow [Select Tools Procedure](procedures/select-tools.md) |

If no arguments are provided or the operation is unclear, ask the user:

> Which Aeval operation would you like to perform?
> 1. **Generate Tool Schema** — Extract tool definitions from a Python codebase
> 2. **Generate Test Cases** — Create eval criteria and YAML test cases from a PRD
> 3. **Select Tools** — Filter tools relevant to a specific evaluation scenario

Once the operation is determined, follow the corresponding procedure document.

---

## Procedures

- [Generate Tool Schema](procedures/generate-tool-schema.md) — Scan Python code, extract tool functions, write `tools.json`
- [Generate Test Cases](procedures/generate-testcase.md) — Analyze PRD, generate eval criteria, produce YAML test cases
- [Select Tools](procedures/select-tools.md) — Given a scenario, select the subset of tools that would be invoked

---

## Workflow Example

A typical evaluation workflow:

1. **Generate tool schema** from your agent's codebase → produces `tools.json`
2. **Generate test cases** using your PRD and `tools.json` → produces `aeval/eval.yaml` and `aeval/testcases/*.yaml`
3. Review and update placeholder values in the generated YAML files
4. Run `aeval run` to evaluate your agent

For scenario-specific tool filtering (e.g., testing a single use case):
1. Use **Select Tools** with scenario context → produces `tools-selected.json`
2. Use `tools-selected.json` for focused evaluation
