# Specification: {{asset-name}}

> **Guidelines**: Read [guidelines.md](../guidelines.md) and [guidelines-agent.md](../guidelines-agent.md) before executing ANY tasks below. Follow all constraints described there throughout execution.

## Basic Setup

- [ ] Read the project input (`product-requirements-document.md`, `intent.md`, or the user prompt that triggered this specification)
- [ ] Bootstrap agent code in `assets/{{asset-name}}/` using skill `sap-agent-bootstrap` (invoke from inside `assets/{{asset-name}}/`, use copy commands — do NOT create files manually)
- [ ] Install dependencies, validate the agent starts and responds at `/.well-known/agent.json`

> **Before generating the tasks below**, read the "Runtime Skills" section of [guidelines-agent.md](../guidelines-agent.md) and decide — based on the PRD/intent — whether the agent needs runtime skills. Emit a TODO for each skill and each asset file that should be authored. If none apply, skip.

{{project-specific-tasks}}

- [ ] Delete the template runtime skill: `rm -rf assets/{{asset-name}}/app/skills/template-skill/`
- [ ] Implement business step instrumentation for each milestone from the PRD: structured logging with pattern `[MILESTONE_ID].[achieved|missed]: [description]` and OpenTelemetry custom spans. Extract business logic from `stream()` into a plain async helper (e.g. `_run_agent()`) and instrument that helper — never wrap a `yield` inside `with tracer.start_as_current_span(...)` (causes `GeneratorExit` context errors in async generators). See `guidelines-agent.md` for the full constraint.
- [ ] Verify `auto_instrument()` is called at top of `main.py` before any AI framework imports

{{#if any SAP API integration exists (S/4HANA, etc)}}

- [ ] Verify `api-discovery-results.md` exists at workspace root with ORD IDs for all required APIs
- [ ] If `specification/<asset-name>/api-specs/` exists:
  - [ ] Invoke `mcp-translation-file` skill — **if the skill is not available in this environment, skip this item and the next item (MCP server asset creation). The agent will rely solely on existing MCP servers from Path B. Do NOT fail the process.**
  - [ ] Then invoke `setup-solution` to create/register MCP assets (skip if `mcp-translation-file` was skipped)
- [ ] Wire MCP tool loading in `agent.py` using `get_mcp_tools()` from `sap_cloud_sdk.agentgateway` — see guidelines for canonical pattern. NEVER create direct HTTP clients (`requests`, `httpx`, OData client) for SAP APIs.
- [ ] Lastly, after `mcp-specs` were generated and / or `mcp-translation-file` completed generating the new MCP assets, generate `mcp-mock.json` using the `mcp-mock-config` skill (required before tests can run). If `mcp-translation-file` was skipped (unavailable) and no `mcp-specs/` exist either, skip mock generation.

{{/if}}

## Testing

- [ ] `conftest.py` only sets `IBD_TESTING=true` — this causes the agent to run with mock MCP tool results during tests
- [ ] Write unit tests in `assets/{{asset-name}}/tests/` — exactly one per tool, run each immediately after writing
- [ ] Write one integration test executing end-to-end agent flow with real LLM by calling the agent's `invoke` function (AI Core env vars always available; mock external non-AI systems only)
- [ ] Run `pytest` from `assets/{{asset-name}}/` (no args, no extra flags — `pytest.ini` configures everything) — if coverage < 70%, add tests until threshold met
- [ ] Verify `assets/{{asset-name}}/app/agent.py` has exactly 3 decorated functions from the bootstrap template (`@agent_model`, `@agent_config` for temperature, `@prompt_section`) — run `grep -c "^@agent_model\|^@agent_config\|^@prompt_section" assets/{{asset-name}}/app/agent.py` and confirm it returns 3. If it returns more than 3, remove the extra decorators and replace them with plain Python constants
- [ ] Run `pytest` again from `assets/{{asset-name}}/` (no args) to generate final `test_report.json`
- [ ] Verify `test_report.json` exists in `assets/{{asset-name}}/` — if not, run pytest again until it does. The test report is automatically generated according to the `pytest.ini` and `conftest.py` when all tests are executed by running `pytest` with no extra args.
