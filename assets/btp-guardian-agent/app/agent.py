import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncGenerator, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from opentelemetry import trace

try:
    from sap_cloud_sdk.agent_decorators import agent_config, agent_model, prompt_section
except ImportError:
    def _identity_decorator(*_dargs, **_dkwargs):
        def _wrap(fn):
            return fn
        return _wrap

    agent_model = _identity_decorator
    agent_config = _identity_decorator
    prompt_section = _identity_decorator

from api_client import Client

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

# ---------------------------------------------------------------------------
# BTP API destination names (resolved from env at startup)
# ---------------------------------------------------------------------------

DEST_ACCOUNTS = os.environ.get("BTP_ACCOUNTS_DESTINATION_NAME", "BTP_ACCOUNTS")
DEST_ENTITLEMENTS = os.environ.get("BTP_ENTITLEMENTS_DESTINATION_NAME", "BTP_ENTITLEMENTS")
DEST_RESOURCE_CONSUMPTION = os.environ.get(
    "BTP_RESOURCE_CONSUMPTION_DESTINATION_NAME", "BTP_RESOURCE_CONSUMPTION"
)
DEST_METRICS = os.environ.get("BTP_METRICS_DESTINATION_NAME", "BTP_METRICS")
DEST_USAGE_RECORDS = os.environ.get("BTP_USAGE_RECORDS_DESTINATION_NAME", "BTP_USAGE_RECORDS")
DEST_PROVISIONING = os.environ.get("BTP_PROVISIONING_DESTINATION_NAME", "BTP_PROVISIONING")
DEST_AUDIT_LOGS = os.environ.get("BTP_AUDIT_LOGS_DESTINATION_NAME", "BTP_AUDIT_LOGS")
# XSUAA Authorization API (xsuaa apiaccess plan) — per-subaccount XSUAA tenant
DEST_AUTHORIZATION = os.environ.get("BTP_AUTHORIZATION_DESTINATION_NAME", "BTP_AUTHORIZATION")
# XSUAA SCIM User Management API — same XSUAA tenant base URL
DEST_SCIM = os.environ.get("BTP_SCIM_DESTINATION_NAME", "BTP_SCIM")

MAX_PAGE_SIZE = int(os.environ.get("BTP_MAX_PAGE_SIZE", "100"))
# When True, write tools (role collections, user assignment, shadow users) are registered.
# Default off: the demo agent is read-only until this flag is set.
ALLOW_WRITES = os.environ.get("BTP_ALLOW_WRITES", "").lower() in ("1", "true", "yes")

# ---------------------------------------------------------------------------
# Agent model / config / prompt decorators
# ---------------------------------------------------------------------------


@agent_model(
    key="config.model",
    label="LLM Model",
    description="The language model powering BTP Guardian",
)
def get_model_name() -> str:
    return os.environ.get("AGENT_LLM_MODEL", "gpt-4o")


@agent_config(
    key="config.temperature",
    label="LLM Temperature",
    description="Controls randomness of responses (0.0 = deterministic, 1.0 = creative)",
)
def get_temperature() -> float:
    return 0.0


# gen_ai_hub's init_llm defaults max_tokens to 256, which clips long answers
# (topology/entitlement listings) mid-sentence. Set an explicit, larger cap.
def get_max_tokens() -> int:
    return int(os.environ.get("AGENT_LLM_MAX_TOKENS", "4096"))


@prompt_section(
    key="prompts.system",
    label="System Prompt",
    description="The full system prompt defining BTP Guardian's role and behaviour",
    validation={"format": "markdown", "max_length": 5000},
)
def get_system_prompt() -> str:
    write_policy = (
        """- Write operations are ENABLED (BTP_ALLOW_WRITES=1). You may create/delete role
  collections, assign/unassign users and roles, and create/delete shadow users using the
  provided tools. For DESTRUCTIVE operations (deleteRoleCollection, unassignUserFromRoleCollection,
  unassignRoleFromRoleCollection, deleteShadowUser): before calling the tool, echo back
  exactly what you are about to do — resource name, user, and IdP origin — and require the
  user to confirm with an explicit 'yes' before proceeding."""
        if ALLOW_WRITES else
        """- You are currently READ-ONLY: this guardrail is enforced here in the system prompt, so
  you must not perform any write or modify operation on BTP resources. When a user asks for
  a modify/write action (e.g. add or remove a user, create or delete a role collection,
  assign or unassign a role), do NOT attempt it. Instead: (1) state clearly that you are
  currently read-only because this restriction is set in the prompt, and (2) confirm that
  the capability exists and WOULD work once the read-only guardrail is lifted — briefly
  describe exactly what you would do (the target resource, the user/idp, and the operation),
  so the user knows it is ready to be enabled."""
    )
    return f"""You are BTP Guardian, an expert AI assistant for SAP BTP platform teams and FinOps stakeholders.

You provide accurate, real-time insights into BTP cloud consumption, costs, account topology,
entitlements, and governance posture by calling BTP platform APIs as tools.

## Rules
- Always set top or equivalent page-size parameters to a maximum of 100 on every tool call
  that accepts them; inform the user when this limit is applied.
- Never hallucinate data. If a tool returns no results, explicitly state that no data was found.
- When answering cost queries, always specify the time period and currency. If a cost
  tool (monthlySubaccountCmCosts / cloudCreditsDetails) returns no rows, do NOT just say
  "no data": explain the account may be a subscription/commitment model with no
  consumption-based cost, and offer the monthlyUsage report (usage metrics) instead.
- Resolve relative dates ("this month", "last month", "this year") using the CURRENT
  DATE provided at the end of this prompt — never assume a fixed year. Cost/usage tools
  expect the period as YYYYMM (e.g. billingPeriod eq '202607' for July 2026); build the
  value from the current date, not from memory.
- For governance queries, classify issues by severity: critical / warning / info.
- For audit log queries, default to the last 7 days if no time range is specified.
  When calling getAuditLogRecords without a specific category, set surfaceNotable=True
  to surface notable events. The tool returns pre-formatted markdown — copy it verbatim
  into your response without summarising, paraphrasing, or reformatting it. Never
  describe what the table contains instead of showing it.
- For "who has role X" or "list users with Y access" queries: use getSCIMGroups with
  filter='displayName eq "<role collection name>"' (or co for partial match). The response
  includes a members[] array with the assigned users — read it directly. Do NOT use
  getRoleCollections for this; it does not return members. If the exact role collection
  name is unknown, try common BTP names first: "Subaccount Administrator", "Subaccount
  Viewer", "Global Account Administrator", "Global Account Viewer".
- The XSUAA/SCIM tools (getSCIMGroups, getRoleCollections, etc.) are scoped to a single
  subaccount whose xsuaa apiaccess instance backs the BTP_AUTHORIZATION / BTP_SCIM
  destinations. They cannot enumerate global account-level role assignments; if asked
  about global account admins, explain this scope limitation clearly.
{write_policy}
- When a query requires multiple API calls (e.g. topology then cost), chain them step by step
  and synthesise a single, cohesive answer.
- To act on a subaccount referred to by NAME (e.g. "coena"), first call getSubaccounts with the
  `name` parameter to resolve it to a GUID, then pass that GUID to subaccount-scoped tools like
  getSubaccountAssignments(subaccountGUID=...). Never put a plain name in `labelSelector` — that
  field only accepts key=value label selectors. If the name resolves to no subaccount, say so.
- For app metrics/state queries (GET_accounts-…-metrics / GET_accounts-…-state), the
  subaccountName parameter is the subaccount's TECHNICAL NAME (subdomain), not its display name
  or GUID. Resolve it from the `subdomain` field in the getSubaccounts response.
- XSUAA app IDs follow the format 'appname!tNNNN' (e.g. 'myapp!t1234'). Use getXsuaaApps to
  enumerate them. Never guess an app ID.

## Summary vs. detail
- Entitlement and other large-list tools return a COMPACT SUMMARY by default: heavy nested
  arrays are collapsed to counts (e.g. a "servicePlansCount" or per-subaccount count). When you
  present a summary, say so and offer to drill into a specific service or subaccount for the
  full breakdown.
- To drill down, re-call the same tool with a scope filter set (e.g. assignedServiceName for
  global assignments, subaccountGUID for subaccount assignments) AND detailLevel="detail".
  detailLevel="detail" is ignored unless a scope filter is also set, so always narrow first.
- If a tool result contains a "_truncated" note or a [TRUNCATED] marker, the data was capped to
  fit context: tell the user it was capped and suggest narrowing by service, subaccount, or date
  range."""


# ---------------------------------------------------------------------------
# Response dataclass
# ---------------------------------------------------------------------------


@dataclass
class AgentResponse:
    status: Literal["input_required", "completed", "error"]
    message: str


# ---------------------------------------------------------------------------
# BTP Guardian Agent
# ---------------------------------------------------------------------------

COST_ALERT_PCT = 80
ENTITLEMENT_ALERT_PCT = 85


def _enforce_page_size(params: dict, key: str = "$top") -> dict:
    """Cap pagination param at MAX_PAGE_SIZE."""
    if key in params:
        try:
            params[key] = min(int(params[key]), MAX_PAGE_SIZE)
        except (ValueError, TypeError):
            params[key] = MAX_PAGE_SIZE
    return params


# Hard ceiling on characters returned from any tool to the LLM. gpt-4o's 128K
# context is ~512K chars. After reserving room for the system prompt, tool
# schemas, growing multi-turn history, and the output, a single tool result can
# safely be ~80K chars — enough to fit a full summarized entitlements payload
# for a large global account (~50K chars) without the backstop shredding it,
# while still catching pathological raw payloads (the ~1.5M-token entitlements
# dump is ~6M chars, far above this cap, so the backstop still fires when it
# genuinely must). Env-overridable for tuning without a redeploy.
MAX_TOOL_RESULT_CHARS = int(os.environ.get("BTP_MAX_TOOL_RESULT_CHARS", "80000"))

# Maximum non-system messages kept in the LangGraph state before a turn.
# Older messages are dropped at clean HumanMessage boundaries to prevent
# context_length_exceeded on long multi-turn conversations. Tool call / result
# pairs are always kept intact; we only trim at the start of a human turn.
MAX_HISTORY_MESSAGES = int(os.environ.get("BTP_MAX_HISTORY_MESSAGES", "40"))


def _trim_messages(messages: list, max_non_system: int) -> list:
    """Return messages trimmed to at most max_non_system non-system entries.

    Keeps the leading SystemMessage (if any) and the most recent
    max_non_system messages. Trimming only happens at HumanMessage
    boundaries so we never leave an orphaned tool-call without its
    corresponding ToolMessage responses.
    """
    from langchain_core.messages import SystemMessage as _SM

    if not messages:
        return messages

    # Split off any leading system messages (there should be at most one).
    system_prefix = []
    rest = list(messages)
    while rest and isinstance(rest[0], _SM):
        system_prefix.append(rest.pop(0))

    if len(rest) <= max_non_system:
        return system_prefix + rest

    # Walk backwards from the end collecting messages until we've gathered
    # max_non_system of them, stopping only at clean HumanMessage starts.
    from langchain_core.messages import HumanMessage as _HM

    kept = rest[-max_non_system:]
    # If the first kept message is not a HumanMessage, advance forward until
    # we find one (to avoid orphaned tool results / AI messages without prior
    # context). This may shrink the window slightly but keeps the state valid.
    while kept and not isinstance(kept[0], _HM):
        kept = kept[1:]

    if not kept:
        # Fallback: keep the last max_non_system regardless — better than empty.
        kept = rest[-max_non_system:]

    dropped = len(rest) - len(kept)
    if dropped:
        logger.warning(
            "context_trim: dropped %d old messages to stay within %d-message window",
            dropped,
            max_non_system,
        )
    return system_prefix + kept


def _summarize_record(record, summary_fields, list_keys):
    """Project one record to summary_fields, replacing heavy nested arrays.

    Nested lists whose key is in list_keys (or, defensively, ANY list-valued
    field not itself a summary_field) are replaced by an integer count under a
    "<key>Count" key. Nested dicts/lists under summary_fields are recursed into
    so nested per-plan arrays also get counted.
    """
    if not isinstance(record, dict):
        return record

    keep = set(summary_fields) if summary_fields else set(record.keys())
    heavy = set(list_keys) if list_keys else set()
    out: dict = {}
    for key, value in record.items():
        if isinstance(value, list) and (key in heavy or key not in keep):
            out[f"{key}Count"] = len(value)
            continue
        if key not in keep:
            continue
        if isinstance(value, list):
            out[key] = [
                _summarize_record(v, summary_fields, list_keys) for v in value
            ]
        elif isinstance(value, dict):
            out[key] = _summarize_record(value, summary_fields, list_keys)
        else:
            out[key] = value
    return out


def _shape_result(
    result,
    *,
    record_keys=None,
    summary_fields=None,
    list_keys=None,
    detail=False,
    scoped=False,
) -> str:
    """Serialize a tool result for the LLM, shrinking oversized payloads.

    - detail + scoped: return the raw payload untrimmed (the caller has narrowed
      to one service/subaccount/etc., so it is small and safe).
    - otherwise: for each record list named in record_keys, project every record
      to summary_fields and replace heavy nested arrays (list_keys) with a count.
    - universal backstop: if the serialized string still exceeds
      MAX_TOOL_RESULT_CHARS, truncate the largest record list and append a
      machine-readable note telling the model to narrow the query.
    """
    import json

    def _dump(obj):
        return json.dumps(obj, default=str)

    # Detailed + scoped drill-down: caller narrowed the query, return as-is
    # (still guarded by the char-cap backstop below).
    if not (detail and scoped) and summary_fields and isinstance(result, dict):
        shaped = dict(result)
        keys = record_keys or [
            k for k, v in result.items() if isinstance(v, list)
        ]
        for rk in keys:
            recs = result.get(rk)
            if isinstance(recs, list):
                shaped[rk] = [
                    _summarize_record(r, summary_fields, list_keys) for r in recs
                ]
        result = shaped

    serialized = _dump(result)
    if len(serialized) <= MAX_TOOL_RESULT_CHARS:
        return serialized

    # Backstop: still too big. Truncate the largest list-valued field and note it.
    if isinstance(result, dict):
        list_fields = [(k, v) for k, v in result.items() if isinstance(v, list)]
        if list_fields:
            biggest_key = max(list_fields, key=lambda kv: len(kv[1]))[0]
            recs = result[biggest_key]

            def _trial(n):
                t = dict(result)
                t[biggest_key] = recs[:n]
                t["_truncated"] = {
                    "field": biggest_key,
                    "returned": n,
                    "total": len(recs),
                    "note": (
                        "Result was capped to fit the model context. Narrow the "
                        "query (by service name, subaccount, or date range) and "
                        "set detailLevel='detail' to see specific records."
                    ),
                }
                return t

            # Proportionally estimate how many records fit (keeps far more than a
            # blind halving), then shrink by 10% steps until it actually fits.
            fit_ratio = MAX_TOOL_RESULT_CHARS / len(serialized)
            kept = max(1, int(len(recs) * fit_ratio * 0.9))
            while kept >= 1:
                trial_str = _dump(_trial(kept))
                if len(trial_str) <= MAX_TOOL_RESULT_CHARS:
                    return trial_str
                if kept == 1:
                    break
                kept = max(1, int(kept * 0.9))

    # Non-dict or unshrinkable: hard truncate the string with a note.
    note = (
        '\n\n[TRUNCATED: result exceeded the context limit and was cut. '
        "Narrow the query and set detailLevel='detail' to see specific records.]"
    )
    return serialized[: MAX_TOOL_RESULT_CHARS - len(note)] + note


def _parse_audit_record(r: dict) -> dict:
    """Normalise a raw Audit Log API record into a flat shape for rendering.

    The API returns object/attributes nested inside a JSON-encoded 'message'
    string rather than as top-level fields. This function parses that string
    and hoists the key fields up so the formatters can access them uniformly.
    """
    import json as _json
    out = dict(r)
    msg_str = r.get("message", "")
    if msg_str:
        try:
            msg = _json.loads(msg_str)
            out.setdefault("object", msg.get("object", {}))
            out.setdefault("attributes", msg.get("attributes", []))
            out.setdefault("data", msg.get("data", {}))
        except Exception:
            out["message_raw"] = msg_str[:200]
    # user is a plain string in this API (service account name), not a dict
    if isinstance(out.get("user"), str):
        out["user"] = {"id": out["user"]}
    return out


def _format_audit_log_markdown(records: list, title: str = "Audit Log Records") -> str:
    """Render audit log records as a markdown table."""
    if not records:
        return f"## {title}\n\nNo audit log records found for the given filters."
    lines = [
        f"## {title}", "",
        f"**{len(records)} record(s)**", "",
        "| Time | Category | User | Object | Change |",
        "|------|----------|------|--------|--------|",
    ]
    for raw in records:
        r = _parse_audit_record(raw)
        time_val = r.get("time", "")
        cat = r.get("category", "")
        user = r.get("user", {})
        user_id = user.get("id", str(user)) if isinstance(user, dict) else str(user)
        obj = r.get("object", {})
        obj_str = f"{obj.get('type', '')} `{obj.get('id', '')}`" if isinstance(obj, dict) else str(obj)
        attrs = r.get("attributes", [])
        change = "; ".join(
            f"{a.get('name')}: `{a.get('old')}` → `{a.get('new')}`"
            for a in attrs[:3] if isinstance(a, dict) and a.get("name")
        ) or "—"
        lines.append(f"| {time_val} | {cat} | {user_id} | {obj_str} | {change} |")
    return "\n".join(lines)


def _surface_notable_audit_events(records: list) -> str:
    """Classify audit records by severity and render a notable-events markdown summary."""
    critical: list = []
    warning: list = []
    info: list = []
    ROLE_KEYWORDS = {"role", "trust", "binding", "permission", "scope", "credential"}
    # Keywords in the raw message that indicate a genuine security incident vs.
    # normal OAuth token issuance (which also carries category=audit.security-events).
    INCIDENT_KEYWORDS = {"fail", "denied", "unauthorized", "revok", "invalid", "error",
                         "reject", "block", "suspend", "lockout"}

    for raw in records:
        r = _parse_audit_record(raw)
        cat = r.get("category", "")
        attrs = r.get("attributes", [])
        attr_names = {a.get("name", "").lower() for a in attrs if isinstance(a, dict)}
        # Only flag security-events as critical when the message content signals an
        # actual incident — routine token issuance is security-events but not notable.
        msg_lower = str(raw.get("message", "")).lower()
        is_incident = any(kw in msg_lower for kw in INCIDENT_KEYWORDS)

        if cat == "audit.security-events" and is_incident:
            critical.append(r)
        elif cat == "audit.configuration" and attr_names & ROLE_KEYWORDS:
            warning.append(r)
        else:
            info.append(r)

    sections = [
        "## Audit Log — Notable Events Summary", "",
        f"**Total records analysed:** {len(records)}  ",
        f"**Critical (security incidents):** {len(critical)} | "
        f"**Warning (config/role changes):** {len(warning)} | "
        f"**Info (routine):** {len(info)}",
        "",
    ]
    if critical:
        sections += ["### 🔴 Critical — Security Incidents",
                     _format_audit_log_markdown(critical[:20], title=""),
                     f"_Showing {min(20, len(critical))} of {len(critical)}_", ""]
    if warning:
        sections += ["### 🟡 Warning — Configuration Changes (role/trust/binding)",
                     _format_audit_log_markdown(warning[:20], title=""),
                     f"_Showing {min(20, len(warning))} of {len(warning)}_", ""]
    if info:
        sections += [
            f"### ℹ️ Info — Routine Events (showing first 10 of {len(info)})",
            _format_audit_log_markdown(info[:10], title=""),
        ]
    if not critical and not warning:
        sections.append("_No security incidents or notable configuration changes detected._")
    return "\n".join(sections)


def _build_domain_tools(
    accounts_client: Client,
    entitlements_client: Client,
    consumption_client: Client,
    metrics_client: Client,
    usage_records_client: Client,
    provisioning_client: Client,
    audit_logs_client: Client,
    authorization_client: Client | None = None,
    scim_client: Client | None = None,
) -> list:
    """Build LangChain StructuredTool instances backed by direct BTP REST clients."""
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    DETAIL_LEVEL_DESC = (
        "'summary' (default) returns compact fields with heavy nested arrays "
        "replaced by counts; 'detail' returns full records but ONLY when a "
        "specific scope filter is also set (e.g. assignedServiceName for "
        "entitlements, subaccountGUID for subaccount assignments). Use 'detail' "
        "on a follow-up turn when the user asks to drill into one service or "
        "subaccount."
    )

    # Fields kept when summarizing entitlement records, matching the real
    # Entitlements Service schema (EntitledAndAssignedServicesResponseObject).
    # Any nested list NOT in this set — assignmentInfo, resources, dataCenters,
    # sourceEntitlements — is collapsed to a "<key>Count" by _summarize_record.
    ENTITLEMENT_SUMMARY_FIELDS = [
        "name",
        "displayName",
        "description",
        "servicePlans",
        "amount",
        "remainingAmount",
        "autoAssign",
        "category",
        "unlimited",
        "provisioningMethod",
        "uniqueIdentifier",
    ]

    # -----------------------------------------------------------------------
    # Accounts Service tools
    # -----------------------------------------------------------------------

    class GetGlobalAccountInput(BaseModel):
        expand: bool = Field(default=False, description="Whether to expand child entities")

    async def get_global_account(expand: bool = False) -> str:
        result = await accounts_client.get(
            "/accounts/v1/globalAccount",
            params={"expand": str(expand).lower()},
        )
        return _shape_result(result)

    class GetSubaccountsInput(BaseModel):
        name: str | None = Field(
            default=None,
            description="Match a subaccount by its display name or subdomain "
            "(case-insensitive substring). Use this to resolve a subaccount NAME "
            "(e.g. 'coena') to its GUID before calling subaccount-scoped tools.",
        )
        directoryGUID: str | None = Field(default=None, description="Filter by directory GUID")
        labelSelector: str | None = Field(
            default=None,
            description="Kubernetes-style label selector, e.g. 'env=prod'. Must be a "
            "key=value (or key!=value) pair — do NOT pass a plain subaccount name here; "
            "use the 'name' parameter for name lookups.",
        )

    async def get_subaccounts(
        name: str | None = None,
        directoryGUID: str | None = None,
        labelSelector: str | None = None,
    ) -> str:
        params: dict = {}
        if directoryGUID:
            params["directoryGUID"] = directoryGUID
        if labelSelector:
            params["labelSelector"] = labelSelector
        params["derivedAuthorizations"] = "any"
        result = await accounts_client.get("/accounts/v1/subaccounts", params=params)

        # Client-side name match: the Accounts API has no display-name filter, so
        # resolve a name (e.g. "coena") here instead of misusing labelSelector.
        if name and isinstance(result, dict) and isinstance(result.get("value"), list):
            needle = name.strip().lower()
            matched = [
                sa
                for sa in result["value"]
                if needle in str(sa.get("displayName", "")).lower()
                or needle in str(sa.get("subdomain", "")).lower()
            ]
            result = {**result, "value": matched, "matchedName": name}

        return _shape_result(result)

    class GetDirectoriesInput(BaseModel):
        expand: bool = Field(default=True, description="Whether to include subaccounts in response")

    async def get_directories(expand: bool = True) -> str:
        # Directories are retrieved via global account with expand
        result = await accounts_client.get(
            "/accounts/v1/globalAccount",
            params={"expand": str(expand).lower()},
        )
        return _shape_result(result)

    class GetSubaccountInput(BaseModel):
        subaccountGUID: str = Field(description="GUID of the subaccount to retrieve")
        derivedAuthorizations: str | None = Field(
            default=None,
            description="Optional: 'any' to include subaccounts accessible via role hierarchy",
        )

    async def get_subaccount(subaccountGUID: str, derivedAuthorizations: str | None = None) -> str:
        params: dict = {}
        if derivedAuthorizations:
            params["derivedAuthorizations"] = derivedAuthorizations
        return _shape_result(await accounts_client.get(f"/accounts/v1/subaccounts/{subaccountGUID}", params=params))

    class UpdateGlobalAccountInput(BaseModel):
        displayName: str | None = Field(default=None, description="New display name for the global account")
        description: str | None = Field(default=None, description="New description")
        customProperties: list | None = Field(default=None, description="List of custom property key/value objects")

    async def update_global_account(
        displayName: str | None = None,
        description: str | None = None,
        customProperties: list | None = None,
    ) -> str:
        body: dict = {}
        if displayName is not None:
            body["displayName"] = displayName
        if description is not None:
            body["description"] = description
        if customProperties is not None:
            body["customProperties"] = customProperties
        return _shape_result(await accounts_client.patch("/accounts/v1/globalAccount", body=body))

    class CreateSubaccountInput(BaseModel):
        displayName: str = Field(description="Human-readable name for the new subaccount")
        region: str = Field(description="Region key, e.g. 'eu10', 'us10'. Call getAvailableEnvironments or check BTP cockpit for valid values.")
        subdomain: str = Field(description="Unique subdomain identifier (lowercase alphanumeric + hyphens). Must be unique globally.")
        betaEnabled: bool = Field(default=False, description="Enable beta features in this subaccount")
        description: str | None = Field(default=None, description="Optional description")
        usedForProduction: str | None = Field(default=None, description="'USED_FOR_PRODUCTION' or 'NOT_USED_FOR_PRODUCTION'")
        parentGUID: str | None = Field(default=None, description="GUID of parent directory; omit to place under global account root")

    async def create_subaccount(
        displayName: str,
        region: str,
        subdomain: str,
        betaEnabled: bool = False,
        description: str | None = None,
        usedForProduction: str | None = None,
        parentGUID: str | None = None,
    ) -> str:
        body: dict = {
            "displayName": displayName,
            "region": region,
            "subdomain": subdomain,
            "betaEnabled": betaEnabled,
        }
        if description is not None:
            body["description"] = description
        if usedForProduction is not None:
            body["usedForProduction"] = usedForProduction
        if parentGUID is not None:
            body["parentGUID"] = parentGUID
        return _shape_result(await accounts_client.post("/accounts/v1/subaccounts", body=body))

    class UpdateSubaccountInput(BaseModel):
        subaccountGUID: str = Field(description="GUID of the subaccount to update. Resolve via getSubaccounts first.")
        displayName: str | None = Field(default=None, description="New display name")
        description: str | None = Field(default=None, description="New description")
        betaEnabled: bool | None = Field(default=None, description="Enable/disable beta features")
        usedForProduction: str | None = Field(default=None, description="'USED_FOR_PRODUCTION' or 'NOT_USED_FOR_PRODUCTION'")
        customProperties: list | None = Field(default=None, description="Custom property key/value objects")

    async def update_subaccount(
        subaccountGUID: str,
        displayName: str | None = None,
        description: str | None = None,
        betaEnabled: bool | None = None,
        usedForProduction: str | None = None,
        customProperties: list | None = None,
    ) -> str:
        body: dict = {}
        if displayName is not None:
            body["displayName"] = displayName
        if description is not None:
            body["description"] = description
        if betaEnabled is not None:
            body["betaEnabled"] = betaEnabled
        if usedForProduction is not None:
            body["usedForProduction"] = usedForProduction
        if customProperties is not None:
            body["customProperties"] = customProperties
        return _shape_result(await accounts_client.patch(f"/accounts/v1/subaccounts/{subaccountGUID}", body=body))

    class DeleteSubaccountInput(BaseModel):
        subaccountGUID: str = Field(description="GUID of the subaccount to delete. This action is irreversible.")

    async def delete_subaccount(subaccountGUID: str) -> str:
        return _shape_result(await accounts_client.delete(f"/accounts/v1/subaccounts/{subaccountGUID}"))

    class MoveSubaccountInput(BaseModel):
        subaccountGUID: str = Field(description="GUID of the subaccount to move")
        targetAccountGUID: str = Field(description="GUID of the target directory or global account to move the subaccount into")

    async def move_subaccount(subaccountGUID: str, targetAccountGUID: str) -> str:
        return _shape_result(await accounts_client.post(
            f"/accounts/v1/subaccounts/{subaccountGUID}/move",
            body={"targetAccountGUID": targetAccountGUID},
        ))

    class MoveSubaccountsInput(BaseModel):
        subaccountGUIDs: list = Field(description="List of subaccount GUIDs to move")
        targetAccountGUID: str = Field(description="GUID of the target directory or global account")

    async def move_subaccounts(subaccountGUIDs: list, targetAccountGUID: str) -> str:
        return _shape_result(await accounts_client.post(
            "/accounts/v1/subaccounts/move",
            body={"subaccountGUIDs": subaccountGUIDs, "targetAccountGUID": targetAccountGUID},
        ))

    # Subaccount labels
    class GetSubaccountLabelsInput(BaseModel):
        subaccountGUID: str = Field(description="GUID of the subaccount")

    async def get_subaccount_labels(subaccountGUID: str) -> str:
        return _shape_result(await accounts_client.get(f"/accounts/v1/subaccounts/{subaccountGUID}/labels"))

    class CreateSubaccountLabelsInput(BaseModel):
        subaccountGUID: str = Field(description="GUID of the subaccount")
        labels: dict = Field(description="Label key-value pairs to assign, e.g. {'env': ['prod'], 'team': ['platform']}")

    async def create_subaccount_labels(subaccountGUID: str, labels: dict) -> str:
        return _shape_result(await accounts_client.put(
            f"/accounts/v1/subaccounts/{subaccountGUID}/labels",
            body={"labels": labels},
        ))

    class DeleteSubaccountLabelsInput(BaseModel):
        subaccountGUID: str = Field(description="GUID of the subaccount")

    async def delete_subaccount_labels(subaccountGUID: str) -> str:
        return _shape_result(await accounts_client.delete(f"/accounts/v1/subaccounts/{subaccountGUID}/labels"))

    # Subaccount settings
    class GetSubaccountSettingsInput(BaseModel):
        subaccountGUID: str = Field(description="GUID of the subaccount")

    async def get_subaccount_settings(subaccountGUID: str) -> str:
        return _shape_result(await accounts_client.get(f"/accounts/v1/subaccounts/{subaccountGUID}/settings"))

    class CreateOrUpdateSubaccountSettingsInput(BaseModel):
        subaccountGUID: str = Field(description="GUID of the subaccount")
        settings: dict = Field(description="Settings object to create or replace")

    async def create_or_update_subaccount_settings(subaccountGUID: str, settings: dict) -> str:
        return _shape_result(await accounts_client.put(
            f"/accounts/v1/subaccounts/{subaccountGUID}/settings",
            body=settings,
        ))

    class DeleteSubaccountSettingsInput(BaseModel):
        subaccountGUID: str = Field(description="GUID of the subaccount")

    async def delete_subaccount_settings(subaccountGUID: str) -> str:
        return _shape_result(await accounts_client.delete(f"/accounts/v1/subaccounts/{subaccountGUID}/settings"))

    # Service Manager bindings v1 (per subaccount)
    class GetServiceManagementBindingInput(BaseModel):
        subaccountGUID: str = Field(description="GUID of the subaccount")

    async def get_service_management_binding(subaccountGUID: str) -> str:
        return _shape_result(await accounts_client.get(
            f"/accounts/v1/subaccounts/{subaccountGUID}/serviceManagementBinding"
        ))

    class CreateServiceManagementBindingInput(BaseModel):
        subaccountGUID: str = Field(description="GUID of the subaccount to create a Service Manager binding for")

    async def create_service_management_binding(subaccountGUID: str) -> str:
        return _shape_result(await accounts_client.post(
            f"/accounts/v1/subaccounts/{subaccountGUID}/serviceManagementBinding", body={}
        ))

    class DeleteServiceManagementBindingInput(BaseModel):
        subaccountGUID: str = Field(description="GUID of the subaccount whose Service Manager binding to delete")

    async def delete_service_management_binding(subaccountGUID: str) -> str:
        return _shape_result(await accounts_client.delete(
            f"/accounts/v1/subaccounts/{subaccountGUID}/serviceManagementBinding"
        ))

    # Service Manager bindings v2 (per subaccount)
    class GetAllServiceManagerBindingsV2Input(BaseModel):
        subaccountGUID: str = Field(description="GUID of the subaccount")

    async def get_all_service_manager_bindings_v2(subaccountGUID: str) -> str:
        return _shape_result(await accounts_client.get(
            f"/accounts/v2/subaccounts/{subaccountGUID}/serviceManagerBindings"
        ))

    class CreateServiceManagerBindingV2Input(BaseModel):
        subaccountGUID: str = Field(description="GUID of the subaccount")
        bindingName: str = Field(description="Unique name for the new Service Manager binding")
        parameters: dict | None = Field(default=None, description="Optional binding parameters")

    async def create_service_manager_binding_v2(
        subaccountGUID: str,
        bindingName: str,
        parameters: dict | None = None,
    ) -> str:
        body: dict = {"name": bindingName}
        if parameters:
            body["parameters"] = parameters
        return _shape_result(await accounts_client.post(
            f"/accounts/v2/subaccounts/{subaccountGUID}/serviceManagerBindings", body=body
        ))

    class GetServiceManagerBindingV2Input(BaseModel):
        subaccountGUID: str = Field(description="GUID of the subaccount")
        bindingName: str = Field(description="Name of the Service Manager binding")

    async def get_service_manager_binding_v2(subaccountGUID: str, bindingName: str) -> str:
        return _shape_result(await accounts_client.get(
            f"/accounts/v2/subaccounts/{subaccountGUID}/serviceManagerBindings/{bindingName}"
        ))

    class DeleteServiceManagerBindingV2Input(BaseModel):
        subaccountGUID: str = Field(description="GUID of the subaccount")
        bindingName: str = Field(description="Name of the Service Manager binding to delete")

    async def delete_service_manager_binding_v2(subaccountGUID: str, bindingName: str) -> str:
        return _shape_result(await accounts_client.delete(
            f"/accounts/v2/subaccounts/{subaccountGUID}/serviceManagerBindings/{bindingName}"
        ))

    # Directory CRUD
    class GetDirectoryInput(BaseModel):
        directoryGUID: str = Field(description="GUID of the directory to retrieve")
        expand: bool = Field(default=False, description="If True, include child accounts")

    async def get_directory(directoryGUID: str, expand: bool = False) -> str:
        return _shape_result(await accounts_client.get(
            f"/accounts/v1/directories/{directoryGUID}",
            params={"expand": str(expand).lower()},
        ))

    class CreateDirectoryInput(BaseModel):
        displayName: str = Field(description="Display name for the new directory")
        description: str | None = Field(default=None, description="Optional description")
        parentGUID: str | None = Field(default=None, description="Parent directory or global account GUID; omit for root level")
        subdomain: str | None = Field(default=None, description="Subdomain for the directory (required when adding entitlement management feature)")

    async def create_directory(
        displayName: str,
        description: str | None = None,
        parentGUID: str | None = None,
        subdomain: str | None = None,
    ) -> str:
        body: dict = {"displayName": displayName}
        if description is not None:
            body["description"] = description
        if parentGUID is not None:
            body["parentGUID"] = parentGUID
        if subdomain is not None:
            body["subdomain"] = subdomain
        return _shape_result(await accounts_client.post("/accounts/v1/directories", body=body))

    class UpdateDirectoryInput(BaseModel):
        directoryGUID: str = Field(description="GUID of the directory to update")
        displayName: str | None = Field(default=None, description="New display name")
        description: str | None = Field(default=None, description="New description")
        customProperties: list | None = Field(default=None, description="Custom property key/value objects")

    async def update_directory(
        directoryGUID: str,
        displayName: str | None = None,
        description: str | None = None,
        customProperties: list | None = None,
    ) -> str:
        body: dict = {}
        if displayName is not None:
            body["displayName"] = displayName
        if description is not None:
            body["description"] = description
        if customProperties is not None:
            body["customProperties"] = customProperties
        return _shape_result(await accounts_client.patch(f"/accounts/v1/directories/{directoryGUID}", body=body))

    class DeleteDirectoryInput(BaseModel):
        directoryGUID: str = Field(description="GUID of the directory to delete. The directory must be empty first.")
        forceDelete: bool = Field(default=False, description="If True, delete even if the directory still has children (use with caution)")

    async def delete_directory(directoryGUID: str, forceDelete: bool = False) -> str:
        params: dict = {}
        if forceDelete:
            params["forceDelete"] = "true"
        return _shape_result(await accounts_client.delete(
            f"/accounts/v1/directories/{directoryGUID}", params=params
        ))

    class UpdateDirectoryFeaturesInput(BaseModel):
        directoryGUID: str = Field(description="GUID of the directory")
        features: list = Field(description="List of features to enable/disable, e.g. ['DEFAULT', 'ENTITLEMENTS', 'AUTHORIZATIONS']")

    async def update_directory_features(directoryGUID: str, features: list) -> str:
        return _shape_result(await accounts_client.patch(
            f"/accounts/v1/directories/{directoryGUID}/changeDirectoryFeatures",
            body={"features": features},
        ))

    # Directory labels
    class GetDirectoryLabelsInput(BaseModel):
        directoryGUID: str = Field(description="GUID of the directory")

    async def get_directory_labels(directoryGUID: str) -> str:
        return _shape_result(await accounts_client.get(f"/accounts/v1/directories/{directoryGUID}/labels"))

    class CreateDirectoryLabelsInput(BaseModel):
        directoryGUID: str = Field(description="GUID of the directory")
        labels: dict = Field(description="Label key-value pairs, e.g. {'env': ['prod'], 'owner': ['team-a']}")

    async def create_directory_labels(directoryGUID: str, labels: dict) -> str:
        return _shape_result(await accounts_client.put(
            f"/accounts/v1/directories/{directoryGUID}/labels",
            body={"labels": labels},
        ))

    class DeleteDirectoryLabelsInput(BaseModel):
        directoryGUID: str = Field(description="GUID of the directory")

    async def delete_directory_labels(directoryGUID: str) -> str:
        return _shape_result(await accounts_client.delete(f"/accounts/v1/directories/{directoryGUID}/labels"))

    # Directory settings
    class GetDirectorySettingsInput(BaseModel):
        directoryGUID: str = Field(description="GUID of the directory")

    async def get_directory_settings(directoryGUID: str) -> str:
        return _shape_result(await accounts_client.get(f"/accounts/v1/directories/{directoryGUID}/settings"))

    class CreateOrUpdateDirectorySettingsInput(BaseModel):
        directoryGUID: str = Field(description="GUID of the directory")
        settings: dict = Field(description="Settings object to create or replace")

    async def create_or_update_directory_settings(directoryGUID: str, settings: dict) -> str:
        return _shape_result(await accounts_client.put(
            f"/accounts/v1/directories/{directoryGUID}/settings",
            body=settings,
        ))

    class DeleteDirectorySettingsInput(BaseModel):
        directoryGUID: str = Field(description="GUID of the directory")

    async def delete_directory_settings(directoryGUID: str) -> str:
        return _shape_result(await accounts_client.delete(f"/accounts/v1/directories/{directoryGUID}/settings"))

    # Job Management (shared across Accounts and Provisioning)
    class GetJobStatusInput(BaseModel):
        jobInstanceIdOrUniqueId: str = Field(
            description="Job instance ID or unique ID returned by async operations (createSubaccount, deleteSubaccount, etc.)"
        )

    async def get_job_status(jobInstanceIdOrUniqueId: str) -> str:
        return _shape_result(await accounts_client.get(
            f"/jobs-management/v1/jobs/{jobInstanceIdOrUniqueId}/status"
        ))

    # -----------------------------------------------------------------------
    # Entitlements Service tools
    # -----------------------------------------------------------------------

    class GetGlobalAccountAssignmentsInput(BaseModel):
        entitledServicesOnly: bool = Field(
            default=False, description="Return only services with quota assigned"
        )
        assignedServiceName: str | None = Field(
            default=None, description="Filter by service technical name"
        )
        detailLevel: Literal["summary", "detail"] = Field(
            default="summary", description=DETAIL_LEVEL_DESC
        )

    async def get_global_account_assignments(
        entitledServicesOnly: bool = False,
        assignedServiceName: str | None = None,
        detailLevel: Literal["summary", "detail"] = "summary",
    ) -> str:
        params: dict = {"entitledServicesOnly": str(entitledServicesOnly).lower()}
        if assignedServiceName:
            params["assignedServiceName"] = assignedServiceName
        result = await entitlements_client.get(
            "/entitlements/v1/globalAccountAssignments", params=params
        )
        return _shape_result(
            result,
            record_keys=["entitledServices", "assignedServices"],
            summary_fields=ENTITLEMENT_SUMMARY_FIELDS,
            detail=(detailLevel == "detail"),
            scoped=bool(assignedServiceName),
        )

    class GetSubaccountAssignmentsInput(BaseModel):
        subaccountGUID: str | None = Field(
            default=None, description="Subaccount GUID to filter assignments"
        )
        entitledServicesOnly: bool = Field(
            default=False, description="Return only entitled services"
        )
        detailLevel: Literal["summary", "detail"] = Field(
            default="summary", description=DETAIL_LEVEL_DESC
        )

    async def get_subaccount_assignments(
        subaccountGUID: str | None = None,
        entitledServicesOnly: bool = False,
        detailLevel: Literal["summary", "detail"] = "summary",
    ) -> str:
        params: dict = {"entitledServicesOnly": str(entitledServicesOnly).lower()}
        if subaccountGUID:
            params["subaccountGUID"] = subaccountGUID
        result = await entitlements_client.get(
            "/entitlements/v1/assignments", params=params
        )
        return _shape_result(
            result,
            record_keys=["entitledServices", "assignedServices"],
            summary_fields=ENTITLEMENT_SUMMARY_FIELDS,
            detail=(detailLevel == "detail"),
            scoped=bool(subaccountGUID),
        )

    class GetAllowedDataCentersInput(BaseModel):
        pass

    async def get_allowed_data_centers() -> str:
        return _shape_result(await entitlements_client.get("/entitlements/v1/globalAccountAllowedDataCenters"))

    class UpdateDirectoryEntitlementsInput(BaseModel):
        directoryGUID: str = Field(description="GUID of the directory")
        entitlements: list = Field(
            description="List of entitlement assignment objects. Each entry: {'serviceName': str, 'servicePlanName': str, 'quota': int, 'enable': bool}"
        )

    async def update_directory_entitlements(directoryGUID: str, entitlements: list) -> str:
        return _shape_result(await entitlements_client.patch(
            f"/entitlements/v1/directories/{directoryGUID}/assignments",
            body={"entitlements": entitlements},
        ))

    class CreateOrUpdateDirectoryEntitlementsInput(BaseModel):
        directoryGUID: str = Field(description="GUID of the directory to assign entitlements to")
        entitlements: list = Field(
            description="List of entitlement objects to assign or update. Each entry: {'serviceName': str, 'servicePlanName': str, 'quota': int}"
        )

    async def create_or_update_directory_entitlements(directoryGUID: str, entitlements: list) -> str:
        return _shape_result(await entitlements_client.put(
            f"/entitlements/v1/directories/{directoryGUID}/assignments",
            body={"entitlements": entitlements},
        ))

    class SetSubaccountServicePlansInput(BaseModel):
        entitlements: list = Field(
            description=(
                "List of service plan assignment objects to set for subaccounts. "
                "Each entry: {'serviceName': str, 'servicePlanName': str, 'subaccountServicePlans': [{'subaccountGUID': str, 'quota': int}]}"
            )
        )

    async def set_subaccount_service_plans(entitlements: list) -> str:
        return _shape_result(await entitlements_client.put(
            "/entitlements/v1/subaccountServicePlans",
            body={"entitlements": entitlements},
        ))

    # -----------------------------------------------------------------------
    # Resource Consumption tools
    # -----------------------------------------------------------------------

    class MonthlySubaccountCmCostsInput(BaseModel):
        fromDate: str = Field(
            description="Start month, required. Format YYYYMM (e.g. 202607). "
            "'YYYY-MM' is also accepted and normalized."
        )
        toDate: str = Field(
            description="End month, required. Format YYYYMM (e.g. 202607). "
            "'YYYY-MM' is also accepted and normalized. For a single month set "
            "fromDate == toDate."
        )
        subaccountName: str | None = Field(
            default=None,
            description="Optional case-insensitive subaccount name to narrow results "
            "to one subaccount (matched client-side on subaccountName).",
        )

    async def monthly_subaccount_cm_costs(
        fromDate: str,
        toDate: str,
        subaccountName: str | None = None,
    ) -> str:
        # /reports/v1/monthlySubaccountsCost expects fromDate/toDate as YYYYMM
        # integers; the response wraps rows under "content". Each row carries a
        # numeric "cost" + "currency" and the period as "reportYearMonth".
        def _yyyymm(v: str) -> int:
            return int(str(v).replace("-", "").strip())

        params: dict = {"fromDate": _yyyymm(fromDate), "toDate": _yyyymm(toDate)}
        result = await consumption_client.get(
            "/reports/v1/monthlySubaccountsCost", params=params
        )
        if (
            subaccountName
            and isinstance(result, dict)
            and isinstance(result.get("content"), list)
        ):
            needle = subaccountName.strip().lower()
            matched = [
                row
                for row in result["content"]
                if needle in str(row.get("subaccountName", "")).lower()
            ]
            result = {**result, "content": matched}
        return _shape_result(result)

    class MonthlyUsageInput(BaseModel):
        fromDate: str = Field(
            description="Start month, required. Format YYYYMM (e.g. 202401); "
            "'YYYY-MM' is also accepted and normalized."
        )
        toDate: str = Field(
            description="End month, required. Format YYYYMM (e.g. 202412); "
            "'YYYY-MM' is also accepted and normalized."
        )

    async def monthly_usage(fromDate: str, toDate: str) -> str:
        # The API requires both as YYYYMM integers. Accept 'YYYY-MM' too.
        def _yyyymm(v: str) -> int:
            digits = str(v).replace("-", "").strip()
            return int(digits)

        params: dict = {"fromDate": _yyyymm(fromDate), "toDate": _yyyymm(toDate)}
        result = await consumption_client.get("/reports/v1/monthlyUsage", params=params)
        return _shape_result(result)

    class CloudCreditsDetailsInput(BaseModel):
        viewPhases: Literal["CURRENT", "ALL"] = Field(
            default="CURRENT",
            description="Which cloud-credit phases to show: 'CURRENT' (default, the "
            "active phase) or 'ALL' (every phase). Single value only.",
        )

    async def cloud_credits_details(
        viewPhases: Literal["CURRENT", "ALL"] = "CURRENT",
    ) -> str:
        result = await consumption_client.get(
            "/reports/v1/cloudCreditsDetails", params={"viewPhases": viewPhases}
        )
        return _shape_result(result)

    class MonthlyDirectoryUsageInput(BaseModel):
        fromDate: str = Field(
            description="Start month, required. Format YYYYMM (e.g. 202607)."
        )
        toDate: str = Field(
            description="End month, required. Format YYYYMM (e.g. 202607)."
        )
        directoryGUID: str = Field(description="GUID of the directory to report on")

    async def monthly_directory_usage(fromDate: str, toDate: str, directoryGUID: str) -> str:
        def _yyyymm(v: str) -> int:
            return int(str(v).replace("-", "").strip())
        params: dict = {
            "fromDate": _yyyymm(fromDate),
            "toDate": _yyyymm(toDate),
            "directoryGUID": directoryGUID,
        }
        return _shape_result(await consumption_client.get("/reports/v1/monthlyDirectoryUsage", params=params))

    class DailySubaccountUsageInput(BaseModel):
        subaccountGUID: str = Field(description="GUID of the subaccount")
        fromDate: str = Field(description="Start date, format YYYYMMDD (e.g. 20260701)")
        toDate: str = Field(description="End date, format YYYYMMDD (e.g. 20260731)")

    async def daily_subaccount_usage(subaccountGUID: str, fromDate: str, toDate: str) -> str:
        def _yyyymmdd(v: str) -> int:
            return int(str(v).replace("-", "").strip())
        params: dict = {
            "subaccountGUID": subaccountGUID,
            "fromDate": _yyyymmdd(fromDate),
            "toDate": _yyyymmdd(toDate),
        }
        return _shape_result(await consumption_client.get("/reports/v1/subaccountUsage", params=params))

    # -----------------------------------------------------------------------
    # Metrics API tools
    # -----------------------------------------------------------------------

    class GetAppMetricsInput(BaseModel):
        subaccountName: str = Field(
            description="Subaccount technical name (subdomain), e.g. 'coena'. "
            "Use getSubaccounts(name=...) to resolve a display name to its subdomain field first."
        )
        appName: str = Field(
            description="Application name as registered in the metrics API, e.g. 'my-cf-app'."
        )

    async def get_app_metrics(subaccountName: str, appName: str) -> str:
        result = await metrics_client.get(
            f"/accounts/{subaccountName}/apps/{appName}/metrics"
        )
        return _shape_result(result)

    class GetAppStateInput(BaseModel):
        subaccountName: str = Field(
            description="Subaccount technical name (subdomain), e.g. 'coena'. "
            "Use getSubaccounts(name=...) to resolve a display name to its subdomain field first."
        )
        appName: str = Field(
            description="Application name as registered in the metrics API, e.g. 'my-cf-app'."
        )

    async def get_app_state(subaccountName: str, appName: str) -> str:
        result = await metrics_client.get(
            f"/accounts/{subaccountName}/apps/{appName}/state"
        )
        return _shape_result(result)

    class GetAppProcessMetricsInput(BaseModel):
        subaccountName: str = Field(description="Subaccount subdomain (technical name). Resolve via getSubaccounts.")
        appName: str = Field(description="Application name")
        processId: str = Field(description="Process ID within the application")

    async def get_app_process_metrics(subaccountName: str, appName: str, processId: str) -> str:
        return _shape_result(await metrics_client.get(
            f"/accounts/{subaccountName}/apps/{appName}/processes/{processId}/metrics"
        ))

    class GetAppProcessStateInput(BaseModel):
        subaccountName: str = Field(description="Subaccount subdomain (technical name). Resolve via getSubaccounts.")
        appName: str = Field(description="Application name")
        processId: str = Field(description="Process ID within the application")

    async def get_app_process_state(subaccountName: str, appName: str, processId: str) -> str:
        return _shape_result(await metrics_client.get(
            f"/accounts/{subaccountName}/apps/{appName}/processes/{processId}/state"
        ))

    class GetDbSystemMetricsInput(BaseModel):
        subaccountName: str = Field(description="Subaccount subdomain (technical name). Resolve via getSubaccounts.")
        databaseSystem: str = Field(description="Database system name")

    async def get_db_system_metrics(subaccountName: str, databaseSystem: str) -> str:
        return _shape_result(await metrics_client.get(
            f"/accounts/{subaccountName}/dbsystem/{databaseSystem}/metrics"
        ))

    class GetDbSystemStateInput(BaseModel):
        subaccountName: str = Field(description="Subaccount subdomain (technical name). Resolve via getSubaccounts.")
        databaseSystem: str = Field(description="Database system name")

    async def get_db_system_state(subaccountName: str, databaseSystem: str) -> str:
        return _shape_result(await metrics_client.get(
            f"/accounts/{subaccountName}/dbsystem/{databaseSystem}/state"
        ))

    class GetHtml5AppMetricsInput(BaseModel):
        subaccountName: str = Field(description="Subaccount subdomain (technical name). Resolve via getSubaccounts.")
        appName: str = Field(description="HTML5 application name")

    async def get_html5_app_metrics(subaccountName: str, appName: str) -> str:
        return _shape_result(await metrics_client.get(
            f"/accounts/{subaccountName}/html5/apps/{appName}/metrics"
        ))

    class GetHtml5AppStateInput(BaseModel):
        subaccountName: str = Field(description="Subaccount subdomain (technical name). Resolve via getSubaccounts.")
        appName: str = Field(description="HTML5 application name")

    async def get_html5_app_state(subaccountName: str, appName: str) -> str:
        return _shape_result(await metrics_client.get(
            f"/accounts/{subaccountName}/html5/apps/{appName}/state"
        ))

    class GetXsInstanceMetricsInput(BaseModel):
        subaccountName: str = Field(description="Subaccount subdomain (technical name). Resolve via getSubaccounts.")
        instanceName: str = Field(description="XS instance name")

    async def get_xs_instance_metrics(subaccountName: str, instanceName: str) -> str:
        return _shape_result(await metrics_client.get(
            f"/accounts/{subaccountName}/instances/{instanceName}/metrics"
        ))

    class GetXsInstanceStateInput(BaseModel):
        subaccountName: str = Field(description="Subaccount subdomain (technical name). Resolve via getSubaccounts.")
        instanceName: str = Field(description="XS instance name")

    async def get_xs_instance_state(subaccountName: str, instanceName: str) -> str:
        return _shape_result(await metrics_client.get(
            f"/accounts/{subaccountName}/instances/{instanceName}/state"
        ))

    class GetXsInstanceAppMetricsInput(BaseModel):
        subaccountName: str = Field(description="Subaccount subdomain (technical name). Resolve via getSubaccounts.")
        instanceName: str = Field(description="XS instance name")
        appName: str = Field(description="XS application name within the instance")

    async def get_xs_instance_app_metrics(subaccountName: str, instanceName: str, appName: str) -> str:
        return _shape_result(await metrics_client.get(
            f"/accounts/{subaccountName}/instances/{instanceName}/apps/{appName}/metrics"
        ))

    class GetXsInstanceAppStateInput(BaseModel):
        subaccountName: str = Field(description="Subaccount subdomain (technical name). Resolve via getSubaccounts.")
        instanceName: str = Field(description="XS instance name")
        appName: str = Field(description="XS application name within the instance")

    async def get_xs_instance_app_state(subaccountName: str, instanceName: str, appName: str) -> str:
        return _shape_result(await metrics_client.get(
            f"/accounts/{subaccountName}/instances/{instanceName}/apps/{appName}/state"
        ))

    # -----------------------------------------------------------------------
    # Usage Records tools
    # -----------------------------------------------------------------------

    class GetUsageRecordsInput(BaseModel):
        pageSize: int = Field(
            default=100, description="Records per page (max 100; API default is 16)"
        )
        pageNumber: int = Field(default=1, description="1-based page number to retrieve")
        filter: str | None = Field(
            default=None,
            description="Optional filter expression, e.g. \"metricId eq 'API_CALLS'\" "
            "or a date range on 'startedAt'.",
        )

    async def get_usage_records(
        pageSize: int = 100,
        pageNumber: int = 1,
        filter: str | None = None,
    ) -> str:
        params: dict = _enforce_page_size({"pageSize": pageSize}, "pageSize")
        params["pageNumber"] = pageNumber
        if filter:
            params["filter"] = filter
        result = await usage_records_client.get("/usage-records", params=params)
        return _shape_result(result)

    # -----------------------------------------------------------------------
    # Provisioning Service tools
    # -----------------------------------------------------------------------

    class GetEnvironmentInstancesInput(BaseModel):
        subaccountGUID: str | None = Field(
            default=None,
            description="Optional subaccount GUID. The provisioning API returns all "
            "environments; results are filtered to this subaccount client-side.",
        )

    async def get_environment_instances(subaccountGUID: str | None = None) -> str:
        # The provisioning API has no subaccountGUID query param (only headers), so
        # fetch all environments and filter by subaccountGUID client-side.
        result = await provisioning_client.get("/provisioning/v1/environments")

        if (
            subaccountGUID
            and isinstance(result, dict)
            and isinstance(result.get("environmentInstances"), list)
        ):
            matched = [
                env
                for env in result["environmentInstances"]
                if env.get("subaccountGUID") == subaccountGUID
            ]
            result = {**result, "environmentInstances": matched}

        return _shape_result(result)

    class GetAvailableEnvironmentsInput(BaseModel):
        pass

    async def get_available_environments() -> str:
        result = await provisioning_client.get("/provisioning/v1/availableEnvironments")
        return _shape_result(result)

    class GetEnvironmentInstanceInput(BaseModel):
        environmentInstanceId: str = Field(description="ID of the environment instance")

    async def get_environment_instance(environmentInstanceId: str) -> str:
        return _shape_result(await provisioning_client.get(
            f"/provisioning/v1/environments/{environmentInstanceId}"
        ))

    class CreateEnvironmentInstanceInput(BaseModel):
        environmentType: str = Field(description="Environment type, e.g. 'cloudfoundry' or 'kyma'")
        name: str = Field(description="Display name for the new environment instance")
        planName: str = Field(description="Service plan name, e.g. 'standard', 'free'")
        subaccountGUID: str = Field(description="GUID of the subaccount to provision the environment in")
        parameters: dict | None = Field(default=None, description="Optional environment-specific provisioning parameters")
        serviceId: str | None = Field(default=None, description="Optional service ID (broker ID); required for some environment types")

    async def create_environment_instance(
        environmentType: str,
        name: str,
        planName: str,
        subaccountGUID: str,
        parameters: dict | None = None,
        serviceId: str | None = None,
    ) -> str:
        body: dict = {
            "environmentType": environmentType,
            "name": name,
            "planName": planName,
            "subaccountGUID": subaccountGUID,
        }
        if parameters is not None:
            body["parameters"] = parameters
        if serviceId is not None:
            body["serviceId"] = serviceId
        return _shape_result(await provisioning_client.post("/provisioning/v1/environments", body=body))

    class UpdateEnvironmentInstanceInput(BaseModel):
        environmentInstanceId: str = Field(description="ID of the environment instance to update")
        parameters: dict | None = Field(default=None, description="Updated parameters for the environment")
        planName: str | None = Field(default=None, description="New plan name if changing plan")

    async def update_environment_instance(
        environmentInstanceId: str,
        parameters: dict | None = None,
        planName: str | None = None,
    ) -> str:
        body: dict = {}
        if parameters is not None:
            body["parameters"] = parameters
        if planName is not None:
            body["planName"] = planName
        return _shape_result(await provisioning_client.patch(
            f"/provisioning/v1/environments/{environmentInstanceId}", body=body
        ))

    class DeleteEnvironmentInstanceInput(BaseModel):
        environmentInstanceId: str = Field(description="ID of the environment instance to delete")

    async def delete_environment_instance(environmentInstanceId: str) -> str:
        return _shape_result(await provisioning_client.delete(
            f"/provisioning/v1/environments/{environmentInstanceId}"
        ))

    class DeleteAllEnvironmentInstancesInput(BaseModel):
        subaccountGUID: str = Field(description="GUID of the subaccount whose all environment instances should be deleted")

    async def delete_all_environment_instances(subaccountGUID: str) -> str:
        return _shape_result(await provisioning_client.delete(
            "/provisioning/v1/environments",
            params={"subaccountGUID": subaccountGUID},
        ))

    # Environment instance bindings
    class GetEnvironmentInstanceBindingsInput(BaseModel):
        environmentInstanceId: str = Field(description="ID of the environment instance")

    async def get_environment_instance_bindings(environmentInstanceId: str) -> str:
        return _shape_result(await provisioning_client.get(
            f"/provisioning/v1/environments/{environmentInstanceId}/bindings"
        ))

    class CreateEnvironmentInstanceBindingInput(BaseModel):
        environmentInstanceId: str = Field(description="ID of the environment instance")
        bindingName: str = Field(description="Name for the new binding")
        parameters: dict | None = Field(default=None, description="Optional binding parameters")

    async def create_environment_instance_binding(
        environmentInstanceId: str,
        bindingName: str,
        parameters: dict | None = None,
    ) -> str:
        body: dict = {"name": bindingName}
        if parameters:
            body["parameters"] = parameters
        return _shape_result(await provisioning_client.put(
            f"/provisioning/v1/environments/{environmentInstanceId}/bindings", body=body
        ))

    class GetEnvironmentInstanceBindingInput(BaseModel):
        environmentInstanceId: str = Field(description="ID of the environment instance")
        bindingId: str = Field(description="ID of the binding")

    async def get_environment_instance_binding(environmentInstanceId: str, bindingId: str) -> str:
        return _shape_result(await provisioning_client.get(
            f"/provisioning/v1/environments/{environmentInstanceId}/bindings/{bindingId}"
        ))

    class DeleteEnvironmentInstanceBindingInput(BaseModel):
        environmentInstanceId: str = Field(description="ID of the environment instance")
        bindingId: str = Field(description="ID of the binding to delete")

    async def delete_environment_instance_binding(environmentInstanceId: str, bindingId: str) -> str:
        return _shape_result(await provisioning_client.delete(
            f"/provisioning/v1/environments/{environmentInstanceId}/bindings/{bindingId}"
        ))

    # Environment instance labels
    class GetEnvironmentInstanceLabelsInput(BaseModel):
        environmentInstanceId: str = Field(description="ID of the environment instance")

    async def get_environment_instance_labels(environmentInstanceId: str) -> str:
        return _shape_result(await provisioning_client.get(
            f"/provisioning/v1/environments/{environmentInstanceId}/labels"
        ))

    class CreateEnvironmentInstanceLabelsInput(BaseModel):
        environmentInstanceId: str = Field(description="ID of the environment instance")
        labels: dict = Field(description="Label key-value pairs to assign, e.g. {'team': ['platform']}")

    async def create_environment_instance_labels(environmentInstanceId: str, labels: dict) -> str:
        return _shape_result(await provisioning_client.put(
            f"/provisioning/v1/environments/{environmentInstanceId}/labels",
            body={"labels": labels},
        ))

    class DeleteEnvironmentInstanceLabelsInput(BaseModel):
        environmentInstanceId: str = Field(description="ID of the environment instance")

    async def delete_environment_instance_labels(environmentInstanceId: str) -> str:
        return _shape_result(await provisioning_client.delete(
            f"/provisioning/v1/environments/{environmentInstanceId}/labels"
        ))

    class GetSubaccountQuotaInput(BaseModel):
        subaccountGUID: str | None = Field(default=None, description="Filter by subaccount GUID")
        planName: str | None = Field(default=None, description="Filter by service plan name")

    async def get_subaccount_quota(
        subaccountGUID: str | None = None,
        planName: str | None = None,
    ) -> str:
        params: dict = {}
        if subaccountGUID:
            params["subaccountGUID"] = subaccountGUID
        if planName:
            params["planName"] = planName
        return _shape_result(await provisioning_client.get(
            "/provisioning/v1/servicePlanAssignments", params=params
        ))

    # -----------------------------------------------------------------------
    # Audit Log tools
    # -----------------------------------------------------------------------

    class GetAuditLogRecordsInput(BaseModel):
        timeFrom: str = Field(
            description="Start datetime ISO-8601, e.g. '2026-07-01T00:00:00'. Required. "
            "Resolve relative terms ('last 7 days', 'this month') from the current date "
            "in the system prompt."
        )
        timeTo: str = Field(
            description="End datetime ISO-8601, e.g. '2026-07-25T23:59:59'. Required."
        )
        category: str | None = Field(
            default=None,
            description=(
                "Optional event category filter. One of: 'audit.security-events', "
                "'audit.configuration', 'audit.data-access', 'audit.data-modification'. "
                "Omit to retrieve all categories."
            ),
        )
        surfaceNotable: bool = Field(
            default=False,
            description=(
                "When True, classify and surface only notable events "
                "(critical/warning/info) with a markdown summary. Use True when the user "
                "asks for 'all' logs or a governance/security overview."
            ),
        )
        pageSize: int = Field(default=100, description="Records per page (max 100).")
        pageNumber: int = Field(default=1, description="1-based page number.")

    async def get_audit_log_records(
        timeFrom: str,
        timeTo: str,
        category: str | None = None,
        surfaceNotable: bool = False,
        pageSize: int = 100,
        pageNumber: int = 1,
    ) -> str:
        params: dict = {
            "time_from": timeFrom,
            "time_to": timeTo,
            "$top": min(pageSize, MAX_PAGE_SIZE),
            "$skip": (pageNumber - 1) * min(pageSize, MAX_PAGE_SIZE),
        }
        if category:
            params["category"] = category
        result = await audit_logs_client.get("/auditlog/v2/auditlogrecords", params=params)
        if isinstance(result, dict) and result.get("error"):
            return _shape_result(result)
        # The Audit Log Retrieval API returns a bare array, not {"value": [...]}
        if isinstance(result, list):
            records = result
        else:
            records = result.get("value", []) if isinstance(result, dict) else []
        if not surfaceNotable:
            return _format_audit_log_markdown(records)
        return _surface_notable_audit_events(records)

    # -----------------------------------------------------------------------
    # XSUAA / Authorization & Trust Management tools
    # All 4 APIs share the same base URL (BTP_AUTHORIZATION destination).
    # Authorization & Trust Management + SCIM — read tools always registered;
    # write tools gated by ALLOW_WRITES.

    from urllib.parse import quote as _q

    # ================================================================
    # Authorization API — Applications
    # ================================================================

    class GetAppsInput(BaseModel):
        onlyForOrgId: str | None = Field(default=None, description="Filter to a specific CF org GUID")
        onlyWithClientId: bool = Field(default=True, description="If False, include apps without an OAuth client")

    async def get_apps(onlyForOrgId: str | None = None, onlyWithClientId: bool = True) -> str:
        params: dict = {"onlyWithClientId": str(onlyWithClientId).lower()}
        if onlyForOrgId:
            params["onlyForOrgId"] = onlyForOrgId
        return _shape_result(await authorization_client.get("/sap/rest/authorization/v2/apps", params=params))

    class GetAppInput(BaseModel):
        appId: str = Field(description="Application ID (e.g. myapp!t1234)")

    async def get_app(appId: str) -> str:
        return _shape_result(await authorization_client.get(f"/sap/rest/authorization/v2/apps/{_q(appId, safe='')}"))

    class GetAppScopesInput(BaseModel):
        appId: str = Field(description="Application ID")
        scopeName: str | None = Field(default=None, description="Optional: return a single scope by name")

    async def get_app_scopes(appId: str, scopeName: str | None = None) -> str:
        path = f"/sap/rest/authorization/v2/apps/{_q(appId, safe='')}/scopes"
        if scopeName:
            path += f"/{_q(scopeName, safe='')}"
        return _shape_result(await authorization_client.get(path))

    class GetAppAuthoritiesInput(BaseModel):
        appId: str = Field(description="Application ID receiving granted authorities")
        grantedByAppId: str | None = Field(default=None, description="Application ID of the granting app")

    async def get_app_authorities(appId: str, grantedByAppId: str | None = None) -> str:
        params: dict = {}
        if grantedByAppId:
            params["grantedByAppId"] = grantedByAppId
        return _shape_result(await authorization_client.get(
            f"/sap/rest/authorization/v2/apps/{_q(appId, safe='')}/authorities", params=params
        ))

    class GetOwnAppInput(BaseModel):
        includeUsage: bool = Field(default=False, description="If True, also return usage information")

    async def get_own_app(includeUsage: bool = False) -> str:
        path = "/sap/rest/authorization/v2/ownapp"
        if includeUsage:
            path += "/usage"
        return _shape_result(await authorization_client.get(path))

    # ================================================================
    # Authorization API — Role Collections
    # ================================================================

    class GetRoleCollectionsInput(BaseModel):
        page: int | None = Field(
            default=None,
            description="1-based page number for paginated results. Omit to get the first page. "
            "Use this tool to look up a role collection by name or to list all collections. "
            "For 'who has role X' (members/users), use getSCIMGroups instead — it includes member lists."
        )

    async def get_role_collections(page: int | None = None) -> str:
        if page is not None:
            return _shape_result(await authorization_client.get(
                f"/sap/rest/authorization/v2/rolecollections/pages/{page}"
            ))
        return _shape_result(await authorization_client.get("/sap/rest/authorization/v2/rolecollections"))

    class GetRoleCollectionInput(BaseModel):
        name: str = Field(
            description="Exact name of the role collection (case-sensitive). "
            "Use getRoleCollections first to list available names if unknown."
        )

    async def get_role_collection(name: str) -> str:
        return _shape_result(await authorization_client.get(
            f"/sap/rest/authorization/v2/rolecollections/{_q(name, safe='')}"
        ))

    class CreateRoleCollectionInput(BaseModel):
        name: str = Field(description="Unique name for the new role collection")
        description: str | None = Field(default=None, description="Optional description")

    async def create_role_collection(name: str, description: str | None = None) -> str:
        body: dict = {"name": name}
        if description:
            body["description"] = description
        return _shape_result(await authorization_client.post(
            "/sap/rest/authorization/v2/rolecollections", body=body
        ))

    class UpdateRoleCollectionInput(BaseModel):
        name: str = Field(description="Name of the role collection to update")
        newDescription: str | None = Field(default=None, description="New description")
        newName: str | None = Field(default=None, description="New name for the role collection")

    async def update_role_collection(name: str, newDescription: str | None = None, newName: str | None = None) -> str:
        body: dict = {}
        if newDescription is not None:
            body["description"] = newDescription
        if newName is not None:
            body["name"] = newName
        return _shape_result(await authorization_client.put(
            f"/sap/rest/authorization/v2/rolecollections/{_q(name, safe='')}", body=body
        ))

    class DeleteRoleCollectionInput(BaseModel):
        name: str = Field(description="Exact name of the role collection to delete")

    async def delete_role_collection(name: str) -> str:
        return _shape_result(await authorization_client.delete(
            f"/sap/rest/authorization/v2/rolecollections/{_q(name, safe='')}"
        ))

    class GetRoleCollectionRolesInput(BaseModel):
        roleCollectionName: str = Field(description="Name of the role collection")

    async def get_role_collection_roles(roleCollectionName: str) -> str:
        return _shape_result(await authorization_client.get(
            f"/sap/rest/authorization/v2/rolecollections/{_q(roleCollectionName, safe='')}/roles"
        ))

    class AssignRoleToCollectionInput(BaseModel):
        roleCollectionName: str = Field(description="Name of the target role collection")
        roleTemplateName: str = Field(description="Role template name, e.g. 'Viewer'")
        roleTemplateAppId: str = Field(description="App ID of the role template, e.g. 'myapp!t1234'")
        roleName: str | None = Field(default=None, description="Role name (defaults to roleTemplateName if omitted)")

    async def assign_role_to_collection(
        roleCollectionName: str, roleTemplateName: str, roleTemplateAppId: str, roleName: str | None = None
    ) -> str:
        body = [{"roleTemplateName": roleTemplateName, "roleTemplateAppId": roleTemplateAppId,
                 "roleName": roleName or roleTemplateName}]
        return _shape_result(await authorization_client.put(
            f"/sap/rest/authorization/v2/rolecollections/{_q(roleCollectionName, safe='')}/roles",
            body=body,
        ))

    class UnassignRoleFromCollectionInput(BaseModel):
        roleCollectionName: str = Field(description="Name of the role collection")
        roleTemplateAppId: str = Field(description="App ID of the role template")
        roleName: str = Field(description="Role name")
        roleTemplateName: str = Field(description="Role template name")

    async def unassign_role_from_collection(
        roleCollectionName: str, roleTemplateAppId: str, roleName: str, roleTemplateName: str
    ) -> str:
        return _shape_result(await authorization_client.delete(
            f"/sap/rest/authorization/v2/rolecollections/{_q(roleCollectionName, safe='')}"
            f"/roles/{_q(roleTemplateAppId, safe='')}/{_q(roleName, safe='')}/{_q(roleTemplateName, safe='')}"
        ))

    class GetRoleCollectionsByRoleInput(BaseModel):
        appId: str = Field(description="Application ID")
        roleTemplateName: str = Field(description="Role template name")
        roleName: str = Field(description="Role name")

    async def get_role_collections_by_role(appId: str, roleTemplateName: str, roleName: str) -> str:
        return _shape_result(await authorization_client.get(
            f"/sap/rest/authorization/v2/rolecollections/roles"
            f"/{_q(appId, safe='')}/{_q(roleTemplateName, safe='')}/{_q(roleName, safe='')}"
        ))

    # ================================================================
    # Authorization API — Roles
    # ================================================================

    class GetRolesInput(BaseModel):
        appId: str | None = Field(
            default=None,
            description="Filter roles by application ID, e.g. 'myapp!t1234'. "
            "Use getXsuaaApps to list app IDs. Omit to get all roles across all apps."
        )

    async def get_roles(appId: str | None = None) -> str:
        if appId:
            return _shape_result(await authorization_client.get(
                f"/sap/rest/authorization/v2/apps/{_q(appId, safe='')}/roles"
            ))
        return _shape_result(await authorization_client.get("/sap/rest/authorization/v2/roles"))

    class CreateRoleInput(BaseModel):
        roleTemplateName: str = Field(
            description="Role template name to base the role on, e.g. 'Viewer'. "
            "Use getRoleTemplates(appId=...) to list available templates for an app."
        )
        roleTemplateAppId: str = Field(
            description="App ID that owns the role template, e.g. 'myapp!t1234'. "
            "Use getXsuaaApps to list app IDs."
        )
        name: str = Field(description="Name for the new role")
        description: str | None = Field(default=None, description="Optional description")

    async def create_role(roleTemplateName: str, roleTemplateAppId: str, name: str, description: str | None = None) -> str:
        body: dict = {
            "roleTemplateName": roleTemplateName,
            "roleTemplateAppId": roleTemplateAppId,
            "name": name,
        }
        if description:
            body["description"] = description
        return _shape_result(await authorization_client.post("/sap/rest/authorization/v2/apps/roles", body=body))

    class GetRoleInput(BaseModel):
        appId: str = Field(description="Application ID, e.g. 'myapp!t1234'. Use getXsuaaApps to list.")
        templateName: str = Field(description="Role template name. Use getRoleTemplates(appId=...) to list.")
        roleName: str = Field(description="Role name. Use getXsuaaRoles(appId=...) to list.")

    async def get_role(appId: str, templateName: str, roleName: str) -> str:
        return _shape_result(await authorization_client.get(
            f"/sap/rest/authorization/v2/apps/{_q(appId, safe='')}"
            f"/roletemplates/{_q(templateName, safe='')}/roles/{_q(roleName, safe='')}"
        ))

    class UpdateRoleInput(BaseModel):
        appId: str = Field(description="Application ID, e.g. 'myapp!t1234'. Use getXsuaaApps to list.")
        templateName: str = Field(description="Role template name. Use getRoleTemplates(appId=...) to list.")
        roleName: str = Field(description="Role name to update. Use getXsuaaRoles(appId=...) to list.")
        description: str | None = Field(default=None, description="New description")

    async def update_role(appId: str, templateName: str, roleName: str, description: str | None = None) -> str:
        body: dict = {"name": roleName}
        if description is not None:
            body["description"] = description
        return _shape_result(await authorization_client.put(
            f"/sap/rest/authorization/v2/apps/{_q(appId, safe='')}"
            f"/roletemplates/{_q(templateName, safe='')}/roles/{_q(roleName, safe='')}",
            body=body,
        ))

    class DeleteRoleInput(BaseModel):
        appId: str = Field(description="Application ID, e.g. 'myapp!t1234'. Use getXsuaaApps to list.")
        templateName: str = Field(description="Role template name. Use getRoleTemplates(appId=...) to list.")
        roleName: str = Field(description="Role name to delete. Use getXsuaaRoles(appId=...) to list.")

    async def delete_role(appId: str, templateName: str, roleName: str) -> str:
        return _shape_result(await authorization_client.delete(
            f"/sap/rest/authorization/v2/apps/{_q(appId, safe='')}"
            f"/roletemplates/{_q(templateName, safe='')}/roles/{_q(roleName, safe='')}"
        ))

    # ================================================================
    # Authorization API — Role Templates
    # ================================================================

    class GetRoleTemplatesInput(BaseModel):
        appId: str | None = Field(
            default=None,
            description="Filter by application ID, e.g. 'myapp!t1234'. Use getXsuaaApps to list. "
            "Omit to list templates across all apps."
        )
        templateName: str | None = Field(
            default=None,
            description="Get a specific template by name. Requires appId to be set."
        )
        showRoles: bool = Field(default=False, description="Include associated roles in the response")

    async def get_role_templates(
        appId: str | None = None, templateName: str | None = None, showRoles: bool = False
    ) -> str:
        if appId and templateName:
            return _shape_result(await authorization_client.get(
                f"/sap/rest/authorization/v2/apps/{_q(appId, safe='')}/roletemplates/{_q(templateName, safe='')}"
            ))
        if appId:
            return _shape_result(await authorization_client.get(
                f"/sap/rest/authorization/v2/apps/{_q(appId, safe='')}/roletemplates"
            ))
        return _shape_result(await authorization_client.get(
            "/sap/rest/authorization/v2/apps/roletemplates",
            params={"showRoles": str(showRoles).lower()},
        ))

    # ================================================================
    # Authorization API — Attribute Mapping (IdP attribute → role collection)
    # ================================================================

    class GetAttributeMappingsInput(BaseModel):
        origin: str = Field(description="Identity provider origin key, e.g. 'sap.default'")
        attributeName: str | None = Field(default=None, description="Filter by attribute name")
        attributeValue: str | None = Field(default=None, description="Filter by attribute value (requires attributeName)")
        roleCollectionName: str | None = Field(default=None, description="Filter by role collection name")

    async def get_attribute_mappings(
        origin: str,
        attributeName: str | None = None,
        attributeValue: str | None = None,
        roleCollectionName: str | None = None,
    ) -> str:
        if attributeName and attributeValue and roleCollectionName:
            return _shape_result(await authorization_client.get(
                f"/sap/rest/authorization/v2/identity-providers/{_q(origin, safe='')}"
                f"/attributes/{_q(attributeName, safe='')}"
                f"/rolecollections/{_q(roleCollectionName, safe='')}"
            ))
        if attributeName and attributeValue:
            return _shape_result(await authorization_client.get(
                f"/sap/rest/authorization/v2/identity-providers/{_q(origin, safe='')}"
                f"/attributes/{_q(attributeName, safe='')}/{_q(attributeValue, safe='')}"
            ))
        if roleCollectionName:
            return _shape_result(await authorization_client.get(
                f"/sap/rest/authorization/v2/identity-providers/{_q(origin, safe='')}"
                f"/rolecollections/{_q(roleCollectionName, safe='')}"
            ))
        return _shape_result(await authorization_client.get(
            f"/sap/rest/authorization/v2/identity-providers/{_q(origin, safe='')}/attributes/rolecollections"
        ))

    class CreateAttributeMappingInput(BaseModel):
        origin: str = Field(description="Identity provider origin key")
        attributeName: str = Field(description="IdP attribute name to map, e.g. 'department'")
        attributeValue: str = Field(description="Attribute value that triggers the mapping")
        operator: str = Field(default="equals", description="Operator: 'equals' or 'contains'")
        roleCollectionName: str = Field(description="Role collection to assign when attribute matches")

    async def create_attribute_mapping(
        origin: str, attributeName: str, attributeValue: str, operator: str, roleCollectionName: str
    ) -> str:
        body = {
            "attributeName": attributeName,
            "attributeValue": attributeValue,
            "operator": operator,
            "roleCollectionName": roleCollectionName,
        }
        return _shape_result(await authorization_client.post(
            f"/sap/rest/authorization/v2/identity-providers/{_q(origin, safe='')}/attributes",
            body=body,
        ))

    class DeleteAttributeMappingInput(BaseModel):
        origin: str = Field(description="Identity provider origin key")
        attributeName: str = Field(description="Attribute name of the mapping to delete")
        operator: str = Field(description="Operator used in the mapping: 'equals' or 'contains'")
        attributeValue: str = Field(description="Attribute value of the mapping to delete")
        roleCollectionName: str = Field(description="Role collection name of the mapping to delete")

    async def delete_attribute_mapping(
        origin: str, attributeName: str, operator: str, attributeValue: str, roleCollectionName: str
    ) -> str:
        return _shape_result(await authorization_client.delete(
            f"/sap/rest/authorization/v2/identity-providers/{_q(origin, safe='')}"
            f"/attributes/{_q(attributeName, safe='')}/{_q(operator, safe='')}"
            f"/{_q(attributeValue, safe='')}/rolecollections/{_q(roleCollectionName, safe='')}"
        ))

    # ================================================================
    # SCIM API (PlatformAPI) — Groups = role collections
    # ================================================================

    class GetScimGroupsInput(BaseModel):
        filter: str | None = Field(
            default=None,
            description=(
                'SCIM filter to narrow results. Use to find a role collection by name: '
                'displayName eq "Subaccount Administrator" '
                'or partial match: displayName co "Admin". '
                'For "who has role X" queries: filter by displayName, then read members[] '
                'from the returned group(s).'
            ),
        )
        count: int = Field(default=100, description="Max results per page (max 500)")
        startIndex: int = Field(default=1, description="1-based index of first result")
        sortBy: str | None = Field(default=None, description="Sort attribute; only 'displayName' supported")
        sortOrder: str | None = Field(default=None, description="'ascending' or 'descending'")

    async def get_scim_groups(
        filter: str | None = None, count: int = 100, startIndex: int = 1,
        sortBy: str | None = None, sortOrder: str | None = None
    ) -> str:
        params: dict = {"count": min(count, 500), "startIndex": startIndex}
        if filter:
            params["filter"] = filter
        if sortBy:
            params["sortBy"] = sortBy
        if sortOrder:
            params["sortOrder"] = sortOrder
        return _shape_result(await scim_client.get("/Groups", params=params))

    class GetScimGroupInput(BaseModel):
        groupId: str = Field(description="SCIM group ID (= role collection ID)")

    async def get_scim_group(groupId: str) -> str:
        return _shape_result(await scim_client.get(f"/Groups/{_q(groupId, safe='')}"))

    class CreateScimGroupInput(BaseModel):
        displayName: str = Field(description="Display name / name of the role collection")
        description: str | None = Field(default=None, description="Optional description")

    async def create_scim_group(displayName: str, description: str | None = None) -> str:
        body: dict = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
            "displayName": displayName,
        }
        if description:
            body["externalId"] = description
        return _shape_result(await scim_client.post("/Groups", body=body))

    class UpdateScimGroupInput(BaseModel):
        groupId: str = Field(
            description="SCIM group ID to update. Use getSCIMGroups(filter='displayName eq \"name\"') to get the ID."
        )
        members: list | None = Field(
            default=None,
            description='FULL replacement member list — any user not in this list is REMOVED. '
            'Each entry: {"value": "<scimUserId>", "type": "USER"}. '
            'To add ONE user without removing others: first call getSCIMGroup to get current members, '
            'then pass the full list including the new user. '
            'To just add/remove individual members without fetching first, use patchSCIMGroup instead.',
        )
        description: str | None = Field(default=None, description="New description")

    async def update_scim_group(
        groupId: str, members: list | None = None, description: str | None = None
    ) -> str:
        body: dict = {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"]}
        if members is not None:
            body["members"] = members
        if description is not None:
            body["description"] = description
        return _shape_result(await scim_client.put(f"/Groups/{_q(groupId, safe='')}", body=body))

    class PatchScimGroupInput(BaseModel):
        groupId: str = Field(description="SCIM group ID to patch")
        operations: list = Field(
            description='SCIM patch operations list, e.g. [{"op":"add","path":"members","value":[{"value":"<userId>","type":"USER"}]}]'
        )

    async def patch_scim_group(groupId: str, operations: list) -> str:
        body = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": operations,
        }
        return _shape_result(await scim_client.patch(f"/Groups/{_q(groupId, safe='')}", body=body))

    # ================================================================
    # SCIM API — Users = shadow users
    # ================================================================

    class GetScimUsersInput(BaseModel):
        filter: str | None = Field(default=None, description="SCIM filter, e.g. 'userName eq \"alice@example.com\"'")
        count: int = Field(default=100, description="Max results per page (max 500)")
        startIndex: int = Field(default=1, description="1-based start index")

    async def get_scim_users(
        filter: str | None = None, count: int = 100, startIndex: int = 1
    ) -> str:
        params: dict = {"count": min(count, 500), "startIndex": startIndex}
        if filter:
            params["filter"] = filter
        return _shape_result(await scim_client.get("/Users", params=params))

    class GetScimUserInput(BaseModel):
        userId: str = Field(description="SCIM user ID (UUID)")

    async def get_scim_user(userId: str) -> str:
        return _shape_result(await scim_client.get(f"/Users/{_q(userId, safe='')}"))

    class CreateShadowUserInput(BaseModel):
        userName: str = Field(description="Login name / email for the new shadow user")
        origin: str = Field(default="sap.default", description="Identity provider origin key")
        familyName: str | None = Field(default=None, description="Optional last name")
        givenName: str | None = Field(default=None, description="Optional first name")

    async def create_shadow_user(
        userName: str, origin: str = "sap.default",
        familyName: str | None = None, givenName: str | None = None,
    ) -> str:
        body: dict = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": userName,
            "emails": [{"value": userName, "primary": True}],
            "origin": origin,
        }
        name: dict = {}
        if familyName:
            name["familyName"] = familyName
        if givenName:
            name["givenName"] = givenName
        if name:
            body["name"] = name
        return _shape_result(await scim_client.post("/Users", body=body))

    class UpdateShadowUserInput(BaseModel):
        userId: str = Field(
            description="SCIM user ID (UUID) of the user to update. "
            "Use getSCIMUsers(filter='userName eq \"email\"') to resolve a username to its ID."
        )
        userName: str = Field(description="Username / email (required even for partial updates — this is a full PUT replace)")
        origin: str = Field(default="sap.default", description="Identity provider origin key, e.g. 'sap.default'")
        active: bool | None = Field(default=None, description="Set to False to deactivate the user, True to reactivate")
        familyName: str | None = Field(default=None, description="User's last / family name")
        givenName: str | None = Field(default=None, description="User's first / given name")

    async def update_shadow_user(
        userId: str, userName: str, origin: str = "sap.default",
        active: bool | None = None, familyName: str | None = None, givenName: str | None = None,
    ) -> str:
        body: dict = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": userName,
            "origin": origin,
        }
        if active is not None:
            body["active"] = active
        name: dict = {}
        if familyName:
            name["familyName"] = familyName
        if givenName:
            name["givenName"] = givenName
        if name:
            body["name"] = name
        return _shape_result(await scim_client.put(f"/Users/{_q(userId, safe='')}", body=body))

    class PatchShadowUserInput(BaseModel):
        userId: str = Field(description="SCIM user ID (UUID)")
        operations: list = Field(
            description='SCIM patch operations, e.g. [{"op":"replace","path":"active","value":false}]'
        )

    async def patch_shadow_user(userId: str, operations: list) -> str:
        body = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": operations,
        }
        return _shape_result(await scim_client.patch(f"/Users/{_q(userId, safe='')}", body=body))

    class DeleteShadowUserInput(BaseModel):
        userId: str = Field(
            description="SCIM user ID (UUID). Use getSCIMUsers to resolve a username to its ID first."
        )

    async def delete_shadow_user(userId: str) -> str:
        return _shape_result(await scim_client.delete(f"/Users/{_q(userId, safe='')}"))

    # ================================================================
    # Identity Provider Management API (TrustConfigurationAPI)
    # ================================================================

    class GetIdentityProvidersInput(BaseModel):
        activeOnly: bool = Field(default=False, description="If True, return only active identity providers")

    async def get_identity_providers(activeOnly: bool = False) -> str:
        return _shape_result(await authorization_client.get(
            "/sap/rest/identity-providers", params={"activeOnly": str(activeOnly).lower()}
        ))

    class GetIdentityProviderInput(BaseModel):
        idpId: str = Field(description="Identity provider ID (origin key)")

    async def get_identity_provider(idpId: str) -> str:
        return _shape_result(await authorization_client.get(
            f"/sap/rest/identity-providers/{_q(idpId, safe='')}"
        ))

    class CreateIdentityProviderInput(BaseModel):
        type: str = Field(description="Protocol type: 'oidc1.0' or 'saml2.0'")
        name: str = Field(description="Display name for the identity provider")
        originKey: str | None = Field(default=None, description="Origin key (unique identifier)")
        active: bool = Field(default=True, description="Whether the IdP is active")
        addShadowUserOnLogin: bool = Field(default=True, description="Auto-create shadow users on login")

    async def create_identity_provider(
        type: str, name: str, originKey: str | None = None,
        active: bool = True, addShadowUserOnLogin: bool = True,
    ) -> str:
        body: dict = {"type": type, "name": name, "active": active,
                      "addShadowUserOnLogin": addShadowUserOnLogin}
        if originKey:
            body["originKey"] = originKey
        return _shape_result(await authorization_client.post("/sap/rest/identity-providers", body=body))

    class UpdateIdentityProviderInput(BaseModel):
        idpId: str = Field(description="Identity provider ID to update")
        active: bool | None = Field(default=None, description="Enable or disable the IdP")
        addShadowUserOnLogin: bool | None = Field(default=None, description="Toggle shadow user creation")
        linkText: str | None = Field(default=None, description="Login button label text")

    async def update_identity_provider(
        idpId: str, active: bool | None = None,
        addShadowUserOnLogin: bool | None = None, linkText: str | None = None,
    ) -> str:
        body: dict = {}
        if active is not None:
            body["active"] = active
        if addShadowUserOnLogin is not None:
            body["addShadowUserOnLogin"] = addShadowUserOnLogin
        if linkText is not None:
            body["linkText"] = linkText
        return _shape_result(await authorization_client.put(
            f"/sap/rest/identity-providers/{_q(idpId, safe='')}", body=body
        ))

    class DeleteIdentityProviderInput(BaseModel):
        idpId: str = Field(description="Identity provider ID to delete")

    async def delete_identity_provider(idpId: str) -> str:
        return _shape_result(await authorization_client.delete(
            f"/sap/rest/identity-providers/{_q(idpId, safe='')}"
        ))

    class GetIasTenantsInput(BaseModel):
        pass

    async def get_ias_tenants() -> str:
        return _shape_result(await authorization_client.get("/sap/rest/identity-providers/ias"))

    # ================================================================
    # Security Settings API (SecuritySettingsAPI)
    # ================================================================

    class GetSecuritySettingsInput(BaseModel):
        pass

    async def get_security_settings() -> str:
        return _shape_result(await authorization_client.get(
            "/sap/rest/authorization/v2/securitySettings"
        ))

    class GetTrustedDomainsInput(BaseModel):
        subdomain: str = Field(description="Subaccount subdomain (e.g. 'coena')")

    async def get_trusted_domains(subdomain: str) -> str:
        return _shape_result(await authorization_client.get(
            "/sap/rest/authorization/v2/securitySettings/public",
            params={"subdomain": subdomain},
        ))

    class UpdateSecuritySettingsInput(BaseModel):
        iframeDomains: str | None = Field(default=None, description="Space-separated trusted iframe domains, e.g. 'https://*.example.com'")
        defaultIdp: str | None = Field(default=None, description="Origin key of the default IdP for password grant flow")
        tokenValidityInMinutes: int | None = Field(default=None, description="Access token validity in minutes")

    async def update_security_settings(
        iframeDomains: str | None = None,
        defaultIdp: str | None = None,
        tokenValidityInMinutes: int | None = None,
    ) -> str:
        body: dict = {}
        if iframeDomains is not None:
            body["iframeDomains"] = iframeDomains
        if defaultIdp is not None:
            body["defaultIdp"] = defaultIdp
        if tokenValidityInMinutes is not None:
            body["tokenPolicySettings"] = {"accessTokenValidity": tokenValidityInMinutes}
        return _shape_result(await authorization_client.patch(
            "/sap/rest/authorization/v2/securitySettings", body=body
        ))

    class TriggerKeyRotationInput(BaseModel):
        pass

    async def trigger_key_rotation() -> str:
        return _shape_result(await authorization_client.post(
            "/sap/rest/authorization/v2/securitySettings/keyRotation", body={}
        ))

    # ================================================================
    # Auth/SCIM tool lists — read tools always on; write tools gated
    # ================================================================
    auth_read_tools = [
        # Authorization API — Applications (all GET)
        StructuredTool.from_function(coroutine=get_apps, name="getXsuaaApps",
            description="List XSUAA service instances (apps) registered in the subaccount",
            args_schema=GetAppsInput),
        StructuredTool.from_function(coroutine=get_app, name="getXsuaaApp",
            description="Get details of a specific XSUAA application by app ID",
            args_schema=GetAppInput),
        StructuredTool.from_function(coroutine=get_app_scopes, name="getXsuaaAppScopes",
            description="Get OAuth scopes of a XSUAA application, optionally a specific scope",
            args_schema=GetAppScopesInput),
        StructuredTool.from_function(coroutine=get_app_authorities, name="getXsuaaAppAuthorities",
            description="Get authorities granted to a XSUAA application from another app",
            args_schema=GetAppAuthoritiesInput),
        StructuredTool.from_function(coroutine=get_own_app, name="getOwnXsuaaApp",
            description="Get details of the service instance used to call this API; optionally include usage",
            args_schema=GetOwnAppInput),
        # Role Collections — read
        StructuredTool.from_function(coroutine=get_role_collections, name="getRoleCollections",
            description="List all role collections in the subaccount (paginated)",
            args_schema=GetRoleCollectionsInput),
        StructuredTool.from_function(coroutine=get_role_collection, name="getRoleCollection",
            description="Get details of a specific role collection by name",
            args_schema=GetRoleCollectionInput),
        StructuredTool.from_function(coroutine=get_role_collection_roles, name="getRoleCollectionRoles",
            description="List roles assigned to a role collection",
            args_schema=GetRoleCollectionRolesInput),
        StructuredTool.from_function(coroutine=get_role_collections_by_role, name="getRoleCollectionsByRole",
            description="Find all role collections that contain a specific role",
            args_schema=GetRoleCollectionsByRoleInput),
        # Roles — read
        StructuredTool.from_function(coroutine=get_roles, name="getXsuaaRoles",
            description="List roles, optionally filtered by application ID",
            args_schema=GetRolesInput),
        StructuredTool.from_function(coroutine=get_role, name="getXsuaaRole",
            description="Get a specific role by app ID, template name, and role name",
            args_schema=GetRoleInput),
        # Role Templates
        StructuredTool.from_function(coroutine=get_role_templates, name="getRoleTemplates",
            description="List role templates, optionally filtered by app and template name",
            args_schema=GetRoleTemplatesInput),
        # Attribute Mapping — read
        StructuredTool.from_function(coroutine=get_attribute_mappings, name="getAttributeMappings",
            description="Get IdP attribute-to-role-collection mappings for an identity provider",
            args_schema=GetAttributeMappingsInput),
        # SCIM Groups — read
        StructuredTool.from_function(coroutine=get_scim_groups, name="getSCIMGroups",
            description=(
                "Find role collections and their assigned users via the SCIM Groups interface. "
                "PREFERRED tool for 'who has role X' or 'list users with Y access' queries — "
                "each returned group includes a members[] array with the assigned users. "
                "Use the filter param to search by name, e.g. filter='displayName eq \"Subaccount Administrator\"' "
                "or filter='displayName co \"Admin\"'. Call without a filter to list all role collections with members."
            ),
            args_schema=GetScimGroupsInput),
        StructuredTool.from_function(coroutine=get_scim_group, name="getSCIMGroup",
            description=(
                "Get a specific role collection by its SCIM group ID, including the full members[] list. "
                "Use when you already have the group ID from getSCIMGroups or getRoleCollections."
            ),
            args_schema=GetScimGroupInput),
        # SCIM Users — read
        StructuredTool.from_function(coroutine=get_scim_users, name="getSCIMUsers",
            description=(
                "List shadow users in the subaccount's XSUAA tenant, optionally filtered. "
                "Use to resolve a username/email to a SCIM user ID, or to enumerate all users. "
                "Filter examples: 'userName eq \"alice@example.com\"', 'emails.value co \"@company.com\"'. "
                "For 'who has role X', prefer getSCIMGroups with a displayName filter — it returns users and role collection in one call."
            ),
            args_schema=GetScimUsersInput),
        StructuredTool.from_function(coroutine=get_scim_user, name="getSCIMUser",
            description="Get a specific shadow user by SCIM user ID",
            args_schema=GetScimUserInput),
        # Identity Provider Management — read
        StructuredTool.from_function(coroutine=get_identity_providers, name="getIdentityProviders",
            description="List identity providers configured for the subaccount",
            args_schema=GetIdentityProvidersInput),
        StructuredTool.from_function(coroutine=get_identity_provider, name="getIdentityProvider",
            description="Get configuration details of a specific identity provider",
            args_schema=GetIdentityProviderInput),
        StructuredTool.from_function(coroutine=get_ias_tenants, name="getIasTenants",
            description="List available SAP Cloud Identity Services tenants for OIDC trust configuration",
            args_schema=GetIasTenantsInput),
        # Security Settings — read
        StructuredTool.from_function(coroutine=get_security_settings, name="getSecuritySettings",
            description="Get security settings of the XSUAA tenant (token policy, CORS, signing keys)",
            args_schema=GetSecuritySettingsInput),
        StructuredTool.from_function(coroutine=get_trusted_domains, name="getTrustedDomains",
            description="Get trusted domains for a subaccount (public, no auth required)",
            args_schema=GetTrustedDomainsInput),
    ]

    auth_write_tools = [
        # Role Collections — write
        StructuredTool.from_function(coroutine=create_role_collection, name="createRoleCollection",
            description="Create a new role collection",
            args_schema=CreateRoleCollectionInput),
        StructuredTool.from_function(coroutine=update_role_collection, name="updateRoleCollection",
            description="Update the name or description of a role collection",
            args_schema=UpdateRoleCollectionInput),
        StructuredTool.from_function(coroutine=delete_role_collection, name="deleteRoleCollection",
            description="Delete a role collection by name",
            args_schema=DeleteRoleCollectionInput),
        StructuredTool.from_function(coroutine=assign_role_to_collection, name="assignRoleToRoleCollection",
            description="Add a role to a role collection",
            args_schema=AssignRoleToCollectionInput),
        StructuredTool.from_function(coroutine=unassign_role_from_collection, name="unassignRoleFromRoleCollection",
            description="Remove a role from a role collection",
            args_schema=UnassignRoleFromCollectionInput),
        # Roles — write
        StructuredTool.from_function(coroutine=create_role, name="createXsuaaRole",
            description="Create a new role for an application based on a role template",
            args_schema=CreateRoleInput),
        StructuredTool.from_function(coroutine=update_role, name="updateXsuaaRole",
            description="Update the description of a role",
            args_schema=UpdateRoleInput),
        StructuredTool.from_function(coroutine=delete_role, name="deleteXsuaaRole",
            description="Delete a role",
            args_schema=DeleteRoleInput),
        # Attribute Mapping — write
        StructuredTool.from_function(coroutine=create_attribute_mapping, name="createAttributeMapping",
            description="Create an IdP attribute mapping that assigns a role collection based on an attribute value",
            args_schema=CreateAttributeMappingInput),
        StructuredTool.from_function(coroutine=delete_attribute_mapping, name="deleteAttributeMapping",
            description="Delete an IdP attribute-to-role-collection mapping",
            args_schema=DeleteAttributeMappingInput),
        # SCIM Groups — write
        StructuredTool.from_function(coroutine=create_scim_group, name="createSCIMGroup",
            description="Create a role collection via SCIM Groups interface",
            args_schema=CreateScimGroupInput),
        StructuredTool.from_function(coroutine=update_scim_group, name="updateSCIMGroup",
            description=(
                "FULL REPLACE of a role collection's member list via SCIM PUT. "
                "WARNING: any member not in the new list is removed. "
                "To add a single user safely: call getSCIMGroup first to get the current members, "
                "then pass the complete updated list. To add/remove individual members without a "
                "read-first, use patchSCIMGroup instead."
            ),
            args_schema=UpdateScimGroupInput),
        StructuredTool.from_function(coroutine=patch_scim_group, name="patchSCIMGroup",
            description="Add or remove individual members of a role collection via SCIM PATCH operations",
            args_schema=PatchScimGroupInput),
        # SCIM Users — write
        StructuredTool.from_function(coroutine=create_shadow_user, name="createShadowUser",
            description="Create a shadow user in the subaccount's XSUAA tenant",
            args_schema=CreateShadowUserInput),
        StructuredTool.from_function(coroutine=update_shadow_user, name="updateShadowUser",
            description="Replace a shadow user's attributes (full update)",
            args_schema=UpdateShadowUserInput),
        StructuredTool.from_function(coroutine=patch_shadow_user, name="patchShadowUser",
            description="Partially update a shadow user via SCIM PATCH operations",
            args_schema=PatchShadowUserInput),
        StructuredTool.from_function(coroutine=delete_shadow_user, name="deleteShadowUser",
            description="Delete a shadow user by SCIM user ID",
            args_schema=DeleteShadowUserInput),
        # Identity Provider Management — write
        StructuredTool.from_function(coroutine=create_identity_provider, name="createIdentityProvider",
            description="Create a new identity provider (OIDC or SAML)",
            args_schema=CreateIdentityProviderInput),
        StructuredTool.from_function(coroutine=update_identity_provider, name="updateIdentityProvider",
            description="Update an identity provider's active state, shadow-user setting, or link text",
            args_schema=UpdateIdentityProviderInput),
        StructuredTool.from_function(coroutine=delete_identity_provider, name="deleteIdentityProvider",
            description="Delete an identity provider configuration",
            args_schema=DeleteIdentityProviderInput),
        # Security Settings — write
        StructuredTool.from_function(coroutine=update_security_settings, name="updateSecuritySettings",
            description="Update security settings: iframe domains, default IdP, or token validity",
            args_schema=UpdateSecuritySettingsInput),
        StructuredTool.from_function(coroutine=trigger_key_rotation, name="triggerKeyRotation",
            description="Schedule a token signing key rotation (fires within 24-48 hours)",
            args_schema=TriggerKeyRotationInput),
    ] if ALLOW_WRITES else []

    # -----------------------------------------------------------------------
    # Assemble tool list
    # -----------------------------------------------------------------------

    # Accounts Service — write tools (gated by ALLOW_WRITES)
    accounts_write_tools = [
        StructuredTool.from_function(coroutine=update_global_account, name="updateGlobalAccount",
            description="Update the global account display name, description, or custom properties",
            args_schema=UpdateGlobalAccountInput),
        StructuredTool.from_function(coroutine=create_subaccount, name="createSubaccount",
            description="Create a new subaccount in the global account or a directory",
            args_schema=CreateSubaccountInput),
        StructuredTool.from_function(coroutine=update_subaccount, name="updateSubaccount",
            description="Update a subaccount's display name, description, beta flag, or custom properties",
            args_schema=UpdateSubaccountInput),
        StructuredTool.from_function(coroutine=delete_subaccount, name="deleteSubaccount",
            description="Delete a subaccount. The subaccount must have no active services or environment instances.",
            args_schema=DeleteSubaccountInput),
        StructuredTool.from_function(coroutine=move_subaccount, name="moveSubaccount",
            description="Move a single subaccount into a different directory or to the global account root",
            args_schema=MoveSubaccountInput),
        StructuredTool.from_function(coroutine=move_subaccounts, name="moveSubaccounts",
            description="Batch move multiple subaccounts into a target directory or global account root",
            args_schema=MoveSubaccountsInput),
        StructuredTool.from_function(coroutine=create_subaccount_labels, name="createSubaccountLabels",
            description="Assign labels (key-value tags) to a subaccount",
            args_schema=CreateSubaccountLabelsInput),
        StructuredTool.from_function(coroutine=delete_subaccount_labels, name="deleteSubaccountLabels",
            description="Remove all labels from a subaccount",
            args_schema=DeleteSubaccountLabelsInput),
        StructuredTool.from_function(coroutine=create_or_update_subaccount_settings, name="createOrUpdateSubaccountSettings",
            description="Create or replace subaccount settings",
            args_schema=CreateOrUpdateSubaccountSettingsInput),
        StructuredTool.from_function(coroutine=delete_subaccount_settings, name="deleteSubaccountSettings",
            description="Delete all settings for a subaccount",
            args_schema=DeleteSubaccountSettingsInput),
        StructuredTool.from_function(coroutine=create_service_management_binding, name="createServiceManagementBinding",
            description="Create a Service Manager binding for a subaccount (v1)",
            args_schema=CreateServiceManagementBindingInput),
        StructuredTool.from_function(coroutine=delete_service_management_binding, name="deleteServiceManagementBinding",
            description="Delete the Service Manager binding for a subaccount (v1)",
            args_schema=DeleteServiceManagementBindingInput),
        StructuredTool.from_function(coroutine=create_service_manager_binding_v2, name="createServiceManagerBindingV2",
            description="Create a named Service Manager binding for a subaccount (v2)",
            args_schema=CreateServiceManagerBindingV2Input),
        StructuredTool.from_function(coroutine=delete_service_manager_binding_v2, name="deleteServiceManagerBindingV2",
            description="Delete a named Service Manager binding for a subaccount (v2)",
            args_schema=DeleteServiceManagerBindingV2Input),
        StructuredTool.from_function(coroutine=create_directory, name="createDirectory",
            description="Create a new directory in the account hierarchy",
            args_schema=CreateDirectoryInput),
        StructuredTool.from_function(coroutine=update_directory, name="updateDirectory",
            description="Update a directory's display name, description, or custom properties",
            args_schema=UpdateDirectoryInput),
        StructuredTool.from_function(coroutine=delete_directory, name="deleteDirectory",
            description="Delete a directory. Must be empty unless forceDelete=True.",
            args_schema=DeleteDirectoryInput),
        StructuredTool.from_function(coroutine=update_directory_features, name="updateDirectoryFeatures",
            description="Add or remove features (entitlement management, authorizations) for a directory",
            args_schema=UpdateDirectoryFeaturesInput),
        StructuredTool.from_function(coroutine=create_directory_labels, name="createDirectoryLabels",
            description="Assign labels to a directory",
            args_schema=CreateDirectoryLabelsInput),
        StructuredTool.from_function(coroutine=delete_directory_labels, name="deleteDirectoryLabels",
            description="Remove all labels from a directory",
            args_schema=DeleteDirectoryLabelsInput),
        StructuredTool.from_function(coroutine=create_or_update_directory_settings, name="createOrUpdateDirectorySettings",
            description="Create or replace directory settings",
            args_schema=CreateOrUpdateDirectorySettingsInput),
        StructuredTool.from_function(coroutine=delete_directory_settings, name="deleteDirectorySettings",
            description="Delete all settings for a directory",
            args_schema=DeleteDirectorySettingsInput),
    ] if ALLOW_WRITES else []

    # Entitlements Service — write tools (gated by ALLOW_WRITES)
    entitlements_write_tools = [
        StructuredTool.from_function(coroutine=update_directory_entitlements, name="updateDirectoryEntitlements",
            description="Update (PATCH) entitlement assignments for a directory",
            args_schema=UpdateDirectoryEntitlementsInput),
        StructuredTool.from_function(coroutine=create_or_update_directory_entitlements, name="createOrUpdateDirectoryEntitlements",
            description="Assign or replace (PUT) entitlements for a directory",
            args_schema=CreateOrUpdateDirectoryEntitlementsInput),
        StructuredTool.from_function(coroutine=set_subaccount_service_plans, name="setSubaccountServicePlans",
            description="Assign or update entitlements (service plan quotas) for subaccounts",
            args_schema=SetSubaccountServicePlansInput),
    ] if ALLOW_WRITES else []

    # Provisioning Service — write tools (gated by ALLOW_WRITES)
    provisioning_write_tools = [
        StructuredTool.from_function(coroutine=create_environment_instance, name="createEnvironmentInstance",
            description="Provision a new environment instance (Cloud Foundry, Kyma) in a subaccount",
            args_schema=CreateEnvironmentInstanceInput),
        StructuredTool.from_function(coroutine=update_environment_instance, name="updateEnvironmentInstance",
            description="Update an environment instance's parameters or plan",
            args_schema=UpdateEnvironmentInstanceInput),
        StructuredTool.from_function(coroutine=delete_environment_instance, name="deleteEnvironmentInstance",
            description="Delete a specific environment instance",
            args_schema=DeleteEnvironmentInstanceInput),
        StructuredTool.from_function(coroutine=delete_all_environment_instances, name="deleteAllEnvironmentInstances",
            description="Delete all environment instances in a subaccount",
            args_schema=DeleteAllEnvironmentInstancesInput),
        StructuredTool.from_function(coroutine=create_environment_instance_binding, name="createEnvironmentInstanceBinding",
            description="Create a binding for an environment instance",
            args_schema=CreateEnvironmentInstanceBindingInput),
        StructuredTool.from_function(coroutine=delete_environment_instance_binding, name="deleteEnvironmentInstanceBinding",
            description="Delete a specific binding from an environment instance",
            args_schema=DeleteEnvironmentInstanceBindingInput),
        StructuredTool.from_function(coroutine=create_environment_instance_labels, name="createEnvironmentInstanceLabels",
            description="Assign labels to an environment instance",
            args_schema=CreateEnvironmentInstanceLabelsInput),
        StructuredTool.from_function(coroutine=delete_environment_instance_labels, name="deleteEnvironmentInstanceLabels",
            description="Remove all labels from an environment instance",
            args_schema=DeleteEnvironmentInstanceLabelsInput),
    ] if ALLOW_WRITES else []

    return [
        # Accounts Service — read
        StructuredTool.from_function(
            coroutine=get_global_account,
            name="getGlobalAccount",
            description="Get global account details including child directories and subaccounts",
            args_schema=GetGlobalAccountInput,
        ),
        StructuredTool.from_function(
            coroutine=get_subaccounts,
            name="getSubaccounts",
            description="List all subaccounts in the global account, optionally filtered by directory or label",
            args_schema=GetSubaccountsInput,
        ),
        StructuredTool.from_function(
            coroutine=get_subaccount,
            name="getSubaccount",
            description="Get details of a single subaccount by GUID",
            args_schema=GetSubaccountInput,
        ),
        StructuredTool.from_function(
            coroutine=get_directories,
            name="getDirectories",
            description="Get directories and account topology from the global account",
            args_schema=GetDirectoriesInput,
        ),
        StructuredTool.from_function(
            coroutine=get_directory,
            name="getDirectory",
            description="Get details of a specific directory by GUID",
            args_schema=GetDirectoryInput,
        ),
        StructuredTool.from_function(
            coroutine=get_subaccount_labels,
            name="getSubaccountLabels",
            description="Get all labels assigned to a subaccount",
            args_schema=GetSubaccountLabelsInput,
        ),
        StructuredTool.from_function(
            coroutine=get_subaccount_settings,
            name="getSubaccountSettings",
            description="Get settings for a subaccount",
            args_schema=GetSubaccountSettingsInput,
        ),
        StructuredTool.from_function(
            coroutine=get_service_management_binding,
            name="getServiceManagementBinding",
            description="Get the Service Manager binding for a subaccount (v1)",
            args_schema=GetServiceManagementBindingInput,
        ),
        StructuredTool.from_function(
            coroutine=get_all_service_manager_bindings_v2,
            name="getAllServiceManagerBindingsV2",
            description="Get all Service Manager bindings for a subaccount (v2)",
            args_schema=GetAllServiceManagerBindingsV2Input,
        ),
        StructuredTool.from_function(
            coroutine=get_service_manager_binding_v2,
            name="getServiceManagerBindingV2",
            description="Get a specific named Service Manager binding for a subaccount (v2)",
            args_schema=GetServiceManagerBindingV2Input,
        ),
        StructuredTool.from_function(
            coroutine=get_directory_labels,
            name="getDirectoryLabels",
            description="Get all labels assigned to a directory",
            args_schema=GetDirectoryLabelsInput,
        ),
        StructuredTool.from_function(
            coroutine=get_directory_settings,
            name="getDirectorySettings",
            description="Get settings for a directory",
            args_schema=GetDirectorySettingsInput,
        ),
        StructuredTool.from_function(
            coroutine=get_job_status,
            name="getJobStatus",
            description="Poll the status of an async BTP job (e.g. subaccount create/delete). Returns state: COMPLETED, RUNNING, FAILED, etc.",
            args_schema=GetJobStatusInput,
        ),
        # Entitlements Service — read
        StructuredTool.from_function(
            coroutine=get_global_account_assignments,
            name="getGlobalAccountAssignments",
            description="Get all entitlement assignments for the global account including quota and usage",
            args_schema=GetGlobalAccountAssignmentsInput,
        ),
        StructuredTool.from_function(
            coroutine=get_subaccount_assignments,
            name="getSubaccountAssignments",
            description="Get entitlement assignments for a specific subaccount",
            args_schema=GetSubaccountAssignmentsInput,
        ),
        StructuredTool.from_function(
            coroutine=get_allowed_data_centers,
            name="getAllowedDataCenters",
            description="List available data center regions for provisioning subaccounts",
            args_schema=GetAllowedDataCentersInput,
        ),
        # Resource Consumption — read
        StructuredTool.from_function(
            coroutine=monthly_subaccount_cm_costs,
            name="monthlySubaccountCmCosts",
            description="Get monthly cost per subaccount (in the global account's currency) "
            "for a YYYYMM period range. Use for 'which subaccounts cost the most' / "
            "cost-by-subaccount questions.",
            args_schema=MonthlySubaccountCmCostsInput,
        ),
        StructuredTool.from_function(
            coroutine=monthly_usage,
            name="monthlyUsage",
            description="Get monthly usage report across all services in the global account",
            args_schema=MonthlyUsageInput,
        ),
        StructuredTool.from_function(
            coroutine=cloud_credits_details,
            name="cloudCreditsDetails",
            description="Get cloud credits balance and consumption details",
            args_schema=CloudCreditsDetailsInput,
        ),
        StructuredTool.from_function(
            coroutine=monthly_directory_usage,
            name="monthlyDirectoryUsage",
            description="Get monthly usage data for a specific directory",
            args_schema=MonthlyDirectoryUsageInput,
        ),
        StructuredTool.from_function(
            coroutine=daily_subaccount_usage,
            name="dailySubaccountUsage",
            description="Get daily usage data for a specific subaccount",
            args_schema=DailySubaccountUsageInput,
        ),
        # Metrics API — read
        StructuredTool.from_function(
            coroutine=get_app_metrics,
            name="GET_accounts-subaccountName-apps-appName-metrics",
            description="Get runtime metrics for a CF application in a subaccount",
            args_schema=GetAppMetricsInput,
        ),
        StructuredTool.from_function(
            coroutine=get_app_state,
            name="GET_accounts-subaccountName-apps-appName-state",
            description="Get the running state of a CF application in a subaccount",
            args_schema=GetAppStateInput,
        ),
        StructuredTool.from_function(
            coroutine=get_app_process_metrics,
            name="GET_accounts-subaccountName-apps-appName-processes-processId-metrics",
            description="Get metrics for a specific process of a CF application",
            args_schema=GetAppProcessMetricsInput,
        ),
        StructuredTool.from_function(
            coroutine=get_app_process_state,
            name="GET_accounts-subaccountName-apps-appName-processes-processId-state",
            description="Get the state of a specific process of a CF application",
            args_schema=GetAppProcessStateInput,
        ),
        StructuredTool.from_function(
            coroutine=get_db_system_metrics,
            name="GET_accounts-subaccountName-dbsystem-databaseSystem-metrics",
            description="Get metrics for a database system in a subaccount",
            args_schema=GetDbSystemMetricsInput,
        ),
        StructuredTool.from_function(
            coroutine=get_db_system_state,
            name="GET_accounts-subaccountName-dbsystem-databaseSystem-state",
            description="Get the state of a database system in a subaccount",
            args_schema=GetDbSystemStateInput,
        ),
        StructuredTool.from_function(
            coroutine=get_html5_app_metrics,
            name="GET_accounts-subaccountName-html5-apps-appName-metrics",
            description="Get metrics for an HTML5 application in a subaccount",
            args_schema=GetHtml5AppMetricsInput,
        ),
        StructuredTool.from_function(
            coroutine=get_html5_app_state,
            name="GET_accounts-subaccountName-html5-apps-appName-state",
            description="Get the state of an HTML5 application in a subaccount",
            args_schema=GetHtml5AppStateInput,
        ),
        StructuredTool.from_function(
            coroutine=get_xs_instance_metrics,
            name="GET_accounts-subaccountName-instances-instanceName-metrics",
            description="Get metrics for an XS instance in a subaccount",
            args_schema=GetXsInstanceMetricsInput,
        ),
        StructuredTool.from_function(
            coroutine=get_xs_instance_state,
            name="GET_accounts-subaccountName-instances-instanceName-state",
            description="Get the state of an XS instance in a subaccount",
            args_schema=GetXsInstanceStateInput,
        ),
        StructuredTool.from_function(
            coroutine=get_xs_instance_app_metrics,
            name="GET_accounts-subaccountName-instances-instanceName-apps-appName-metrics",
            description="Get metrics for an XS application within an XS instance",
            args_schema=GetXsInstanceAppMetricsInput,
        ),
        StructuredTool.from_function(
            coroutine=get_xs_instance_app_state,
            name="GET_accounts-subaccountName-instances-instanceName-apps-appName-state",
            description="Get the state of an XS application within an XS instance",
            args_schema=GetXsInstanceAppStateInput,
        ),
        # Usage Records — read
        StructuredTool.from_function(
            coroutine=get_usage_records,
            name="get_usage-records",
            description="Get subscription billing usage records",
            args_schema=GetUsageRecordsInput,
        ),
        # Provisioning Service — read
        StructuredTool.from_function(
            coroutine=get_environment_instances,
            name="getEnvironmentInstances",
            description="Get all environment instances (Kyma, Cloud Foundry) provisioned in the global account",
            args_schema=GetEnvironmentInstancesInput,
        ),
        StructuredTool.from_function(
            coroutine=get_environment_instance,
            name="getEnvironmentInstance",
            description="Get details of a specific environment instance by ID",
            args_schema=GetEnvironmentInstanceInput,
        ),
        StructuredTool.from_function(
            coroutine=get_available_environments,
            name="getAvailableEnvironments",
            description="Get available environment types that can be provisioned",
            args_schema=GetAvailableEnvironmentsInput,
        ),
        StructuredTool.from_function(
            coroutine=get_environment_instance_bindings,
            name="getEnvironmentInstanceBindings",
            description="Get all bindings for an environment instance",
            args_schema=GetEnvironmentInstanceBindingsInput,
        ),
        StructuredTool.from_function(
            coroutine=get_environment_instance_binding,
            name="getEnvironmentInstanceBinding",
            description="Get a specific binding for an environment instance",
            args_schema=GetEnvironmentInstanceBindingInput,
        ),
        StructuredTool.from_function(
            coroutine=get_environment_instance_labels,
            name="getEnvironmentInstanceLabels",
            description="Get labels assigned to an environment instance",
            args_schema=GetEnvironmentInstanceLabelsInput,
        ),
        StructuredTool.from_function(
            coroutine=get_subaccount_quota,
            name="getSubaccountQuota",
            description="Get service plan quota assignments for subaccounts from the Provisioning Service",
            args_schema=GetSubaccountQuotaInput,
        ),
        # Audit Log
        StructuredTool.from_function(
            coroutine=get_audit_log_records,
            name="getAuditLogRecords",
            description=(
                "Retrieve audit log records for the BTP subaccount. Use for governance "
                "queries: security events, configuration changes, role assignments, data access. "
                "Set surfaceNotable=True when the user asks for 'all' logs or a security/"
                "governance overview — classifies records into critical/warning/info severity. "
                "Set category for targeted queries. Returns formatted markdown."
            ),
            args_schema=GetAuditLogRecordsInput,
        ),
    ] + accounts_write_tools + entitlements_write_tools + provisioning_write_tools + auth_read_tools + auth_write_tools


class BTPGuardianAgent:
    SUPPORTED_CONTENT_TYPES = ["text", "text/plain"]

    def __init__(
        self,
        accounts_client: Client | None = None,
        entitlements_client: Client | None = None,
        consumption_client: Client | None = None,
        metrics_client: Client | None = None,
        usage_records_client: Client | None = None,
        provisioning_client: Client | None = None,
        audit_logs_client: Client | None = None,
        authorization_client: Client | None = None,
        scim_client: Client | None = None,
    ):
        self._accounts_client = accounts_client or Client(destination_name=DEST_ACCOUNTS)
        self._entitlements_client = entitlements_client or Client(destination_name=DEST_ENTITLEMENTS)
        self._consumption_client = consumption_client or Client(destination_name=DEST_RESOURCE_CONSUMPTION)
        self._metrics_client = metrics_client or Client(destination_name=DEST_METRICS)
        self._usage_records_client = usage_records_client or Client(destination_name=DEST_USAGE_RECORDS)
        self._provisioning_client = provisioning_client or Client(destination_name=DEST_PROVISIONING)
        self._audit_logs_client = audit_logs_client or Client(destination_name=DEST_AUDIT_LOGS)
        # Write clients — only instantiated when BTP_ALLOW_WRITES=1
        self._authorization_client = authorization_client or Client(destination_name=DEST_AUTHORIZATION)
        self._scim_client = scim_client or Client(destination_name=DEST_SCIM)

        self._llm: BaseChatModel | None = None
        self._tools: list | None = None
        self._graph = None

    # ------------------------------------------------------------------
    # Client properties (for testing / injection)
    # ------------------------------------------------------------------

    @property
    def accounts_client(self) -> Client:
        return self._accounts_client

    @property
    def entitlements_client(self) -> Client:
        return self._entitlements_client

    @property
    def consumption_client(self) -> Client:
        return self._consumption_client

    @property
    def metrics_client(self) -> Client:
        return self._metrics_client

    @property
    def usage_records_client(self) -> Client:
        return self._usage_records_client

    @property
    def provisioning_client(self) -> Client:
        return self._provisioning_client

    # ------------------------------------------------------------------
    # Lazy LLM initialisation
    # ------------------------------------------------------------------

    async def _get_llm(self) -> BaseChatModel:
        if self._llm is None:
            from aicore import init_llm_from_destination
            self._llm = await init_llm_from_destination(
                get_model_name(),
                temperature=get_temperature(),
                max_tokens=get_max_tokens(),
            )
        return self._llm

    # ------------------------------------------------------------------
    # Tool loading
    # ------------------------------------------------------------------

    async def _get_tools(self) -> list:
        if self._tools is None:
            # Local/test mode: serve mocked MCP tools from mcp-mock.json instead
            # of hitting real BTP APIs (which need a destination service binding).
            if os.environ.get("IBD_TESTING") == "1":
                from mcp_tools import get_mcp_tools
                self._tools = await get_mcp_tools()
                logger.info(
                    "Mock MCP tools loaded (IBD_TESTING=1): %d tool(s) — %s",
                    len(self._tools),
                    [t.name for t in self._tools],
                )
                return self._tools
            self._tools = _build_domain_tools(
                accounts_client=self._accounts_client,
                entitlements_client=self._entitlements_client,
                consumption_client=self._consumption_client,
                metrics_client=self._metrics_client,
                usage_records_client=self._usage_records_client,
                provisioning_client=self._provisioning_client,
                audit_logs_client=self._audit_logs_client,
                authorization_client=self._authorization_client,
                scim_client=self._scim_client,
            )
            logger.info(
                "Domain tools built: %d tool(s) — %s",
                len(self._tools),
                [t.name for t in self._tools],
            )
        return self._tools

    # ------------------------------------------------------------------
    # LangGraph construction
    # ------------------------------------------------------------------

    def _build_graph(self, tools, llm):
        llm_with_tools = llm.bind_tools(tools)
        tool_node = ToolNode(tools)

        def should_continue(state: MessagesState) -> Literal["tools", "__end__"]:
            last = state["messages"][-1]
            if hasattr(last, "tool_calls") and last.tool_calls:
                return "tools"
            return "__end__"

        async def call_model(state: MessagesState):
            trimmed = _trim_messages(state["messages"], MAX_HISTORY_MESSAGES)
            response = await llm_with_tools.ainvoke(trimmed)
            return {"messages": [response]}

        builder = StateGraph(MessagesState)
        builder.add_node("model", call_model)
        builder.add_node("tools", tool_node)
        builder.add_edge(START, "model")
        builder.add_conditional_edges(
            "model", should_continue, {"tools": "tools", "__end__": END}
        )
        builder.add_edge("tools", "model")
        # In-process checkpointer keyed by thread_id (=A2A context_id) so the
        # agent remembers prior turns within a conversation. Lost on restart —
        # fine for a single-instance demo.
        return builder.compile(checkpointer=MemorySaver())

    async def _get_graph(self):
        if self._graph is None:
            llm = await self._get_llm()
            tools = await self._get_tools()
            self._graph = self._build_graph(tools, llm)
        return self._graph

    # ------------------------------------------------------------------
    # Business logic helper
    # ------------------------------------------------------------------

    @tracer.start_as_current_span("btp-guardian.run-agent")
    async def _run_agent(self, query: str, context_id: str) -> str:
        """Execute the agent reasoning loop and return the final response string."""
        graph = await self._get_graph()
        # Thread the conversation by A2A context_id so the checkpointer replays
        # prior turns. Only prepend the system prompt on the first turn of a
        # thread; later turns already have it in the replayed history.
        config = {"configurable": {"thread_id": context_id or "default"}}
        existing = await graph.aget_state(config)
        first_turn = not (existing and existing.values.get("messages"))

        turn_messages = []
        if first_turn:
            today = datetime.now(timezone.utc)
            system_prompt = (
                f"{get_system_prompt()}\n\n"
                f"## Current date\n"
                f"Today is {today:%Y-%m-%d} (UTC). Current month as YYYYMM: {today:%Y%m}."
            )
            turn_messages.append(SystemMessage(content=system_prompt))
        turn_messages.append(HumanMessage(content=query))

        result = await graph.ainvoke({"messages": turn_messages}, config=config)
        response = result["messages"][-1].content
        logger.info("btp-guardian.run-agent completed for context_id=%s", context_id)
        return response

    # ------------------------------------------------------------------
    # Milestone instrumentation helpers
    # ------------------------------------------------------------------

    @tracer.start_as_current_span("M1-account-topology")
    async def milestone_account_topology(self, subaccount_count: int, success: bool):
        if success:
            logger.info(
                "M1.achieved: account topology resolved — %d subaccounts mapped",
                subaccount_count,
            )
        else:
            logger.warning(
                "M1.missed: account topology could not be resolved "
                "— Accounts Service unreachable or empty"
            )

    @tracer.start_as_current_span("M2-consumption-data")
    async def milestone_consumption_data(self, service_count: int, period: str, success: bool):
        if success:
            logger.info(
                "M2.achieved: consumption data retrieved — %d services, period=%s",
                service_count,
                period,
            )
        else:
            logger.warning(
                "M2.missed: consumption data unavailable — resource-consumption API returned no data"
            )

    @tracer.start_as_current_span("M3-entitlement-utilization")
    async def milestone_entitlement_utilization(
        self, entitlement_count: int, success: bool, reason: str = ""
    ):
        if success:
            logger.info(
                "M3.achieved: entitlement utilization computed — %d entitlements analyzed",
                entitlement_count,
            )
        else:
            logger.warning(
                "M3.missed: entitlement analysis incomplete — %s", reason
            )

    @tracer.start_as_current_span("M4-governance-posture")
    async def milestone_governance_posture(self, flagged_count: int, success: bool):
        if success:
            logger.info(
                "M4.achieved: governance posture assessed — %d issues detected",
                flagged_count,
            )
        else:
            logger.warning(
                "M4.missed: governance assessment failed — Checks API returned error"
            )

    @tracer.start_as_current_span("M5-proactive-alert")
    async def milestone_proactive_alert(
        self,
        subaccount: str,
        service: str,
        breach_type: str,
        success: bool,
    ):
        if success:
            logger.info(
                "M5.achieved: proactive alert emitted — subaccount=%s, service=%s, breach_type=%s",
                subaccount,
                service,
                breach_type,
            )
        else:
            logger.warning(
                "M5.missed: alert generation skipped "
                "— no thresholds configured or Metrics API unavailable"
            )

    # ------------------------------------------------------------------
    # A2A protocol: stream() and invoke()
    # ------------------------------------------------------------------

    async def stream(self, query: str, context_id: str) -> AsyncGenerator[dict, None]:
        yield {
            "is_task_complete": False,
            "require_user_input": False,
            "content": "Querying BTP platform data...",
        }
        try:
            response = await self._run_agent(query, context_id)
            yield {
                "is_task_complete": True,
                "require_user_input": False,
                "content": response,
            }
        except Exception:
            logger.error("stream() failed for context_id=%s", context_id, exc_info=True)
            raise

    async def invoke(self, query: str, context_id: str) -> AgentResponse:
        try:
            response = await self._run_agent(query, context_id)
            return AgentResponse(status="completed", message=response)
        except Exception:
            logger.error("invoke() failed for context_id=%s", context_id, exc_info=True)
            return AgentResponse(status="error", message="An internal error occurred.")


# Alias expected by main.py bootstrap template
SampleAgent = BTPGuardianAgent
