# Generate Tool Schema Procedure

Scan a Python agent codebase, extract all tool function signatures and docstrings, and write a `tools.json` file.

---

## Step 1: Collect inputs

If arguments were passed (after the `generate-tool-schema` operation token), parse them as:
`<codebase_path>`

Otherwise ask the user:

| Value | Description | Example |
|-------|-------------|---------|
| **Codebase path** | Root directory of the agent source code | `app/` or `src/my_agent/` |

---

## Step 2: Discover tool files

Search the codebase for Python files that likely contain tool definitions. Use the following strategies **in order**, collecting a deduplicated list of candidate files:

### Strategy A — `tools/` directory
Look for any directory named `tools` inside `<codebase_path>`. Collect all `*.py` files inside it (excluding `__init__.py`).

### Strategy B — filename pattern
Look for any `*.py` file whose name ends with `_tool.py` or `_tools.py` anywhere under `<codebase_path>`.

### Strategy C — `@tool` decorator
Search all `*.py` files under `<codebase_path>` for the string `@tool`. Collect any file that contains it.

### Strategy D — README.md discovery
Look for a `README.md` file in `<codebase_path>`. If found, read it to identify the project structure and locate the tools directory (e.g. `app/tools/` or `src/tools/`). Collect all `*.py` files (excluding `__init__.py`) from any tools directory mentioned in the README.

**If no files are found after all four strategies**, stop and inform the user:
> "No tool files found in `<codebase_path>`. Make sure the path points to the agent source directory containing tool definitions."

---

## Step 3: Extract tool functions from each file

For each discovered file, use the Read tool to read its contents. Then extract every **public function** that represents an agent tool. A function qualifies if ANY of the following is true:
- It is decorated with `@tool` (LangChain tool decorator)
- The file was discovered via Strategy A or B (all public functions in those files are tools)
- Its name does not start with `_`

Skip private helpers (names starting with `_`) and class methods.

### For each qualifying function, extract:

**1. Tool name**
Use the function name exactly as written (e.g. `acknowledge_customer_tool`).

**2. Description**
Take the first non-empty line of the function's docstring. If no docstring exists, use `"No description available."`.

**3. Parameters**
For each parameter (excluding `self`):

- **name**: parameter name as written
- **type**: map the Python type hint to a JSON Schema type using the table below
- **description**: extract from the `Args:` block in the docstring — find the line matching `<param_name>: <description>` and use the description text. If not found, leave as `""`
- **required**: `true` if the parameter has **no default value**; `false` if it has a default (including `= None`)

**Python → JSON Schema type mapping:**

| Python type hint | JSON Schema type |
|---|---|
| `str` | `string` |
| `int` | `integer` |
| `float` | `number` |
| `bool` | `boolean` |
| `Optional[str]` | `string` (required: false) |
| `Optional[int]` | `integer` (required: false) |
| `Optional[bool]` | `boolean` (required: false) |
| `Optional[X]` | map X, required: false |
| `List[X]` / `list` | `array` |
| `Dict[...]` / `dict` | `object` |
| `Any` | `string` |
| No hint / unknown | `string` |

---

## Step 4: Build the tools JSON structure

Assemble the extracted tools into a JSON array. Each entry follows this exact structure:

```json
{
  "name": "<function_name>",
  "description": "<first line of docstring>",
  "parameters": {
    "type": "object",
    "properties": {
      "<param_name>": {
        "type": "<json_schema_type>",
        "description": "<param description from Args: block>"
      }
    },
    "required": ["<param1>", "<param2>"]
  }
}
```

Rules:
- The `"required"` array must contain **only** parameters where `required: true`
- If a tool has no parameters, set `"properties": {}` and `"required": []`
- Preserve the original parameter order from the function signature

---

## Step 5: Validate

Before writing, run these checks and fix any issues found:

1. **No duplicate tool names** — if two files define a function with the same name, keep the one from the more specific discovery strategy (A > B > C) and warn the user about the duplicate.
2. **At least one tool extracted** — if the list is empty after processing all files, stop and report which files were read and why no tools were found.
3. **All required parameters have a type** — if a required parameter has no type hint and no docstring hint, default to `"string"` and note it in the summary.

---

## Step 6: Write tools.json

Write the validated JSON array to `tools.json` in the current working directory using the Write tool. Use 2-space indentation.

Example output for a single tool:

```json
[
  {
    "name": "intake_complaint_tool",
    "description": "Intake a new complaint and store it.",
    "parameters": {
      "type": "object",
      "properties": {
        "channel": {
          "type": "string",
          "description": "Intake channel (email, chat, phone, social_media)"
        },
        "customer_id": {
          "type": "string",
          "description": "Customer identifier"
        },
        "content": {
          "type": "string",
          "description": "Raw complaint content"
        }
      },
      "required": ["channel", "customer_id", "content"]
    }
  }
]
```

---

## Step 7: Report to user

Summarise the results:

- **Files scanned**: list of files read
- **Tools extracted**: number and names of tools found
- **Output written**: `tools.json` in the current working directory
- **Warnings**: any duplicate names, missing type hints, or missing docstrings

Then inform the user:
> "You can now use `tools.json` with the **Generate Test Cases** operation or pass it to other Aeval procedures."
