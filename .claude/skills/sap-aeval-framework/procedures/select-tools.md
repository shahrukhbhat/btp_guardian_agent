# Select Tools Procedure

Given an evaluation scenario, select the subset of tools from the agent's `tools.json` catalog that would realistically be called when the agent handles that scenario. Write the filtered list to `tools-selected.json`.

---

## Step 1: Collect inputs

**Agent folder:** Use the current working directory as the agent folder. No path needs to be provided by the user.

**Evaluation scenario:** The four scenario fields are expected to already be present in the conversation context:

| Field | Description | Example |
|---|---|---|
| **Initial message** | The message sent to the agent to trigger the scenario | `"What health plans am I enrolled in?"` |
| **Scenario description** | What the agent must do to handle this scenario | `"Employee asks about their current benefits enrollments"` |
| **Validation criteria** | What the agent response must satisfy | `"Response must include plan names and enrollment status"` |
| **Rules and constraints** | Rules the agent must follow | `"Must not reveal salary data; must identify employee before responding"` |

If any of the four scenario fields are missing from context, ask for them before proceeding.

---

## Step 2: Locate the tool catalog

Search for a `tools.json` file in the agent folder using the following priority order:

1. `<agent_folder>/tools.json`
2. `<agent_folder>/assets/*/tools.json` — inside any immediate asset subfolder
3. `<agent_folder>/specification/tools.json`

### If `tools.json` is found
Proceed to Step 3.

### If `tools.json` is NOT found
Follow the [Generate Tool Schema procedure](./generate-tool-schema.md), passing `<agent_folder>` as the codebase path. Inform the user before doing so:
> "No `tools.json` found in `<agent_folder>` — running Generate Tool Schema procedure to generate the tool catalog first…"

Once the Generate Tool Schema procedure completes:
- If it produced a `tools.json` in the current working directory → use that file as the tool catalog and proceed to Step 3.
- If it reported that no tool files were found → proceed to Step 5 with an empty tool list and inform the user:
  > "No tools were found in `<agent_folder>`. This agent has no tools — writing an empty selection."

---

## Step 3: Load the tool catalog

Read the `tools.json` file located in Step 2. Parse it as a JSON array where each entry has the structure:

```json
{
  "name": "<tool_name>",
  "description": "<description>",
  "parameters": {
    "type": "object",
    "properties": { ... },
    "required": [ ... ]
  }
}
```

Accept these alternate envelope formats and normalise to a plain array:
- `{ "tools": [ ... ] }` → use the `tools` array
- `{ "<name>": { "description": ..., "parameters": ... }, ... }` → convert only entries whose values are objects containing at least `description` or `parameters`

If the parsed JSON does not match one of these supported formats, stop and report:
> "`tools.json` has an unrecognized structure. Expected a JSON array, a `{"tools": [...]}` envelope, or an object map of tool definitions."

Report: `"Loaded N tools from <path>"`

---

## Step 4: Analyse the scenario and select tools

For each tool in the catalog, silently reason whether it would be invoked when the agent handles this specific scenario. Use all four scenario fields as evidence:

- **Initial message**: Does the user's request directly trigger this tool? (e.g. "What health plans am I enrolled in?" → `get_benefits_plans` is needed)
- **Scenario description**: Does the described agent action require this tool's capability?
- **Validation criteria**: Does the expected output depend on data this tool would return?
- **Rules and constraints**: Does a rule explicitly require or prohibit calling this tool?

**Include** a tool if the scenario would realistically cause the agent to call it at least once.

**Exclude** a tool if:
- The scenario is unrelated to the tool's capability
- A constraint explicitly prohibits calling it (e.g. "must not make any tool calls" → exclude all tools)
- The tool is a clear duplicate of another selected tool with the same functional purpose

When in doubt, **prefer inclusion over exclusion** — if multiple tools may realistically be invoked, include all of them to preserve realistic tool-call coverage.

When the scenario involves no tool calls at all (e.g. a pure LLM greeting or a rule that prohibits tools), the result is an empty list — this is correct and expected.

---

## Step 5: Build the filtered tool list

Assemble the selected tools into a JSON array. Each entry is copied **exactly** as it appeared in `tools.json` — no modifications to name, description, or parameters.

- Preserve the original order of tools as they appeared in `tools.json`
- If no tools were selected (or the agent has no tools), use `[]`

Example output for a scenario requiring two tools:

```json
[
  {
    "name": "get_employee_profile",
    "description": "Retrieve the employee's profile and personal details from SAP SuccessFactors.",
    "parameters": {
      "type": "object",
      "properties": {
        "user_id": {
          "type": "string",
          "description": "The SuccessFactors userId of the employee."
        }
      },
      "required": ["user_id"]
    }
  },
  {
    "name": "get_benefits_plans",
    "description": "Retrieve available and enrolled benefits plans from SAP SuccessFactors.",
    "parameters": {
      "type": "object",
      "properties": {
        "user_id": {
          "type": "string",
          "description": "The SuccessFactors userId of the employee."
        },
        "country": {
          "type": "string",
          "description": "Optional ISO country code to filter plans by country (e.g. 'US', 'DE')."
        }
      },
      "required": ["user_id"]
    }
  }
]
```

---

## Step 6: Write tools-selected.json

Write the filtered JSON array to `tools-selected.json` in the current working directory using the Write tool. Use 2-space indentation.

---

## Step 7: Report to user

Summarise the results:

- **Agent folder**: path used
- **Catalog loaded from**: path to `tools.json`, or `not found` if no tools were available
- **Catalog size**: total number of tools available (0 if no catalog was found)
- **Selected** (N tools): list each selected tool by name with a one-sentence reason why it is needed for this scenario
- **Excluded**: list excluded tool names only (no explanations needed)
- **Output written**: `tools-selected.json` in the current working directory
