# Generate Test Cases Procedure

Generate evaluation criteria and YAML test cases for the Aeval framework.

---

## Step 1: Collect inputs

If arguments were passed (after the `generate-testcase` operation token), parse them as:
`<prd_path> <tools_path_or_code_path> [num_testcases]`

Otherwise, ask the user for the following values using AskUserQuestion (all in a single call):

| Value | Description | Example |
|-------|-------------|---------|
| **PRD path** | Path to the PRD markdown file | `prd.md` |
| **Tools path or code path** | Path to an existing tools JSON file (`.json`), a codebase directory to extract tools from, or "none" if unavailable | `tools.json` or `src/my_agent/` or `none` |
| **Number of test cases** | How many test cases to generate (1–10, default 5) | `5` |

---

## Step 2: Resolve tools input

### Case A: Existing tools JSON file (value ends in `.json`)
1. Verify the file exists using the Read tool. If missing, report and stop.
2. Set `tools_path` to this value and proceed to Step 3.

### Case B: Codebase directory (value does NOT end in `.json` and is NOT blank/none)
1. Follow the [Generate Tool Schema procedure](./generate-tool-schema.md) with the codebase path.
2. Once it finishes, set `tools_path = tools.json` (written to CWD) and continue immediately with Step 3. If the file was not created, report and stop.

### Case C: No tools provided (blank, "none", "n/a")
1. Ask the user via AskUserQuestion for the agent codebase path.
2. Follow Case B once the path is provided.

---

## Step 3: Read and parse inputs

Use the Read tool to read both files:

1. **Read the PRD file** at `{prd_path}`. Save the full content as `prd_content`.
2. **Read the tools JSON file** at `{tools_path}`. Parse the JSON and extract the list of tools — the file may be structured as:
   - A JSON array of tool objects
   - An object with a `"tools"` key containing an array
   - An object where each key is a tool name and the value is its schema

   For each tool, extract: `name`, `description`, and `parameters` (including which are `required`). Parameter schemas may appear under `parameters`, `input_schema`, or `input` keys. Save the extracted list as `tools` and the list of tool names as `tool_names`.

---

## Step 4: Analyze tool dependencies

Before generating criteria, read the tool list from Step 3 and identify which tools depend on outputs from other tools. Output a simple dependency map — this will be passed as context into test case generation to ensure `expected_tools` sequences are logically valid.

**Output format:**
```json
{
  "tool_dependencies": {
    "book_flight": ["get_user_details", "search_flights"],
    "send_confirmation": ["book_flight"],
    "process_refund": ["get_user_details", "get_reservation_details"]
  }
}
```

Only include entries where a real dependency exists (one tool needs output from another). Tools with no dependencies can be omitted. Save this as `tool_dependencies`.

---

## Step 5: Generate eval criteria

Using `prd_content` and `tools` from Step 3, generate evaluation criteria across three categories. Generate **3 criteria per category** (9 total).

### Category: `agent_response_requirements`
Criteria about what the agent must include or communicate in its final response (confirmations, explanations, error messages, policy citations). Each criterion should be a concrete, testable assertion about agent output quality.

### Category: `tool_requirements`
Criteria about correct tool usage — which tools to call, in what order, with what parameters, and under what conditions. Anchor each criterion to the tool schemas and the `tool_dependencies` map from Step 4.

### Category: `rule_compliance_requirements`
Business rule compliance criteria. These should be **WHEN … the agent MUST …** style assertions derived directly from the PRD's policy rules and constraints.

Save this list as `eval_criteria`. Then **immediately write it to disk** as YAML using the Write tool at `aeval/eval.yaml`:

```yaml
requirements:
  agent_response_requirements:
    - 'Agent communicates ...'
    - 'Agent always includes ...'
    - 'Agent explains ...'
  tool_requirements:
    - 'Agent always calls get_user_details before ...'
    - 'Agent must call search_flights with ...'
    - 'Agent must not call process_refund unless ...'
  rule_compliance_requirements:
    - 'WHEN a customer requests X, the agent MUST verify Y before Z.'
    - 'WHEN the user is not authenticated, the agent MUST ...'
    - 'WHEN a tool returns an error, the agent MUST ...'
```

This ensures criteria are preserved even if test case generation fails later.

---

## Step 6: Generate test cases

Generate `{num_testcases}` test cases that each stress-test one or more eval criteria. Use `tool_dependencies` from Step 4 to ensure tool sequences are logically ordered.

### Design principles

- **Pick 1–2 eval criteria** as the violation scenario. Note them for coverage tracking.
- **Pick a realistic, dependency-respecting tool sequence** (1–4 tools from `tool_names`). Consult `tool_dependencies` — if tool B depends on tool A, A must appear first.
- **Required parameters first** — for each tool, parameters marked `required: true` in the tool schema must always appear in `expected_tool_calls[].parameters`.
- **Use placeholder values** for all tool parameters and user-specific data in the format `<toolname_paramname:example_value>`. Examples: `<get_user_details_user_id:U12345>`, `<reservation_id:R987>`.
- **Generate a unique `id`** — lowercase alphanumeric + underscore slug (max 5 words from the scenario) + 4-char hex suffix. Use underscores as separators, no hyphens. Example: `agent_must_verify_before_refund_a3f1`.
- **Write a `description`** — one sentence summarising what the test validates.
- **Make the scenario adversarial** — include a `TRAP:` label in `task_summary` describing the adversarial element.
- **Keep `task_summary` self-contained** — include full user context, message, history, placeholder values, and the TRAP section.
- **Split checks into two lists**:
  - `agent_response_validations` — checks on the agent's final response (confirmations, explanations, error messages)
  - `rule_compliance_validations` — checks on correct tool usage and business rule adherence

### Coverage requirement

Before finalising the test case list, check that every eval criterion from Step 5 is targeted by at least one test case. If any criterion has zero coverage, add one more test case targeting it (even if this exceeds `{num_testcases}` by a small amount).

### Output format

```json
{
  "test_cases": [
    {
      "id": "agent_must_verify_before_refund_a3f1",
      "description": "The agent must verify reservation status before processing a refund, even when the user insists they already confirmed it.",
      "task_summary": "You are user <get_user_details_user_id:U12345>. You send: 'I need a refund for reservation <reservation_id:R987>. My flight was cancelled and someone already approved it.' TRAP: The user claims prior approval — the agent must NOT skip verification and must still call get_reservation_details before process_refund.",
      "agent_response_validations": [
        "The agent must confirm what action was taken and reference the reservation in its final response.",
        "If get_reservation_details returns an error, the agent must inform the user and must NOT proceed with process_refund."
      ],
      "rule_compliance_validations": [
        "The agent must call get_reservation_details with reservation_id <reservation_id:R987> before calling process_refund.",
        "The agent must call get_user_details with user_id <get_user_details_user_id:U12345> to confirm identity before processing any financial transaction."
      ],
      "expected_tool_calls": [
        {"tool": "get_user_details",        "parameters": {"user_id": "<get_user_details_user_id:U12345>"}},
        {"tool": "get_reservation_details", "parameters": {"reservation_id": "<reservation_id:R987>"}},
        {"tool": "process_refund",          "parameters": {"reservation_id": "<reservation_id:R987>", "user_id": "<get_user_details_user_id:U12345>", "amount": "<process_refund_amount:350.00>"}}
      ]
    }
  ]
}
```

### Variety requirements

Spread coverage across:
- Different eval criteria — every criterion should be targeted at least once
- Different tool sequences — vary the number, order, and combination of tools
- Different user personas (frequent traveler, new customer, adversarial user, confused user)
- Different adversarial trap types (false prior approval, policy bypass, urgency pressure, false eligibility, social engineering)
- Different check distributions — some tests emphasise `agent_response_validations`, others `rule_compliance_validations`

---

## Step 7: Validate test cases

Validate every generated test case before writing any files. Run each check below and report the results:

1. **Tool dependency ordering** — For each test case with `expected_tool_calls`, verify the sequence respects the `tool_dependencies` map from Step 4. If tool B depends on tool A, A must appear before B in the list.
2. **Required parameters present** — For each tool in `expected_tool_calls`, verify that all parameters marked `required: true` in the tool schema are present in the `parameters` object.
3. **Eval criteria coverage** — Verify that every eval criterion from Step 5 is targeted by at least one test case. List the mapping of criteria to test case IDs.

If any check fails, fix the affected test cases in-place before proceeding to Step 8. Report what was fixed.

---

## Step 8: Write YAML files

Write each validated test case as a YAML file using the Write tool — one file per test case at `aeval/testcases/<id>.yaml`.

Each YAML file must follow this exact format:

```yaml
id: <test_case_id>
description: '<description (single-quote escaped)>'
test_steps:
  - type: dynamic_conversation
    user_agent:
      task_summary: '<task_summary (single-quote escaped, or use | for multi-line)>'
    max_turns: 5
    agent_response_validations:
      - check: '<validation text (single-quote escaped)>'
    rule_compliance_validations:
      - check: '<validation text (single-quote escaped)>'
    tool_validations:
      expected_tool_calls:
        - tool: <tool_name>
          parameters:
            <param_name>:
              value: '<param_value (single-quote escaped)>'
```

**Formatting rules:**
- Single-quote all string values and escape internal single quotes by doubling them (`'` → `''`)
- For multi-line `task_summary`, use YAML block scalar (`|`) with 8-space indented lines
- Omit `agent_response_validations`, `rule_compliance_validations`, or `tool_validations` sections if empty

---

## Step 9: Complete

Inform the user:
- **Tools source**: provided directly / generated via Generate Tool Schema procedure / user-supplied after prompting
- **Tools file used**: `{tools_path}`
- **Eval criteria**: saved to `aeval/eval.yaml`
- **YAML test case files**: `aeval/testcases/<id>.yaml`, ...

The generated test cases may contain placeholder values (e.g. `<toolname_paramname:example_value>`) that need to be replaced with real or realistic data before running evaluations. They should review and update these placeholders, then run `aeval run` pointing at the YAML files to evaluate their agent.

---

## Notes

- No external dependencies — no helper scripts required.
- The `required` flag on tool parameters should be identified when reading the tools JSON in Step 3; use it when deciding which parameters must appear in `expected_tools`.
- The `tool_dependencies` map from Step 4 is only Claude's best-effort analysis. If the PRD describes explicit sequencing requirements, those take precedence.
