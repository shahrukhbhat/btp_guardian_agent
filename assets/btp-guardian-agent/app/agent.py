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

MAX_PAGE_SIZE = int(os.environ.get("BTP_MAX_PAGE_SIZE", "100"))

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
    return """You are BTP Guardian, an expert AI assistant for SAP BTP platform teams and FinOps stakeholders.

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
  to surface notable events. Present the returned markdown as-is without reformatting.
- You are read-only — never suggest or imply write or modify operations on BTP resources.
- When a query requires multiple API calls (e.g. topology then cost), chain them step by step
  and synthesise a single, cohesive answer.
- To act on a subaccount referred to by NAME (e.g. "coena"), first call getSubaccounts with the
  `name` parameter to resolve it to a GUID, then pass that GUID to subaccount-scoped tools like
  getSubaccountAssignments(subaccountGUID=...). Never put a plain name in `labelSelector` — that
  field only accepts key=value label selectors. If the name resolves to no subaccount, say so.

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

    for raw in records:
        r = _parse_audit_record(raw)
        cat = r.get("category", "")
        attrs = r.get("attributes", [])
        attr_names = {a.get("name", "").lower() for a in attrs if isinstance(a, dict)}
        if cat == "audit.security-events":
            critical.append(r)
        elif cat == "audit.configuration" and attr_names & ROLE_KEYWORDS:
            warning.append(r)
        else:
            info.append(r)

    sections = [
        "## Audit Log — Notable Events Summary", "",
        f"**Total records analysed:** {len(records)}  ",
        f"**Critical:** {len(critical)} | **Warning:** {len(warning)} | **Info:** {len(info)}",
        "",
    ]
    if critical:
        sections += ["### 🔴 Critical — Security Events",
                     _format_audit_log_markdown(critical, title=""), ""]
    if warning:
        sections += ["### 🟡 Warning — Configuration Changes (role/trust/binding)",
                     _format_audit_log_markdown(warning, title=""), ""]
    if info:
        sections += [
            f"### ℹ️ Info — Other Events ({len(info)} record(s), showing first 10)",
            _format_audit_log_markdown(info[:10], title=""),
        ]
    return "\n".join(sections)


def _build_domain_tools(
    accounts_client: Client,
    entitlements_client: Client,
    consumption_client: Client,
    metrics_client: Client,
    usage_records_client: Client,
    provisioning_client: Client,
    audit_logs_client: Client,
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

    # -----------------------------------------------------------------------
    # Metrics API tools
    # -----------------------------------------------------------------------

    class GetAppMetricsInput(BaseModel):
        subaccountName: str = Field(description="Subaccount technical name")
        appName: str = Field(description="Application name")

    async def get_app_metrics(subaccountName: str, appName: str) -> str:
        result = await metrics_client.get(
            f"/accounts/{subaccountName}/apps/{appName}/metrics"
        )
        return _shape_result(result)

    class GetAppStateInput(BaseModel):
        subaccountName: str = Field(description="Subaccount technical name")
        appName: str = Field(description="Application name")

    async def get_app_state(subaccountName: str, appName: str) -> str:
        result = await metrics_client.get(
            f"/accounts/{subaccountName}/apps/{appName}/state"
        )
        return _shape_result(result)

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
    # Assemble tool list
    # -----------------------------------------------------------------------
    return [
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
            coroutine=get_directories,
            name="getDirectories",
            description="Get directories and account topology from the global account",
            args_schema=GetDirectoriesInput,
        ),
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
            coroutine=get_app_metrics,
            name="GET_accounts-subaccountName-apps-appName-metrics",
            description="Get runtime metrics for a specific application in a subaccount",
            args_schema=GetAppMetricsInput,
        ),
        StructuredTool.from_function(
            coroutine=get_app_state,
            name="GET_accounts-subaccountName-apps-appName-state",
            description="Get the running state of a specific application in a subaccount",
            args_schema=GetAppStateInput,
        ),
        StructuredTool.from_function(
            coroutine=get_usage_records,
            name="get_usage-records",
            description="Get subscription billing usage records",
            args_schema=GetUsageRecordsInput,
        ),
        StructuredTool.from_function(
            coroutine=get_environment_instances,
            name="getEnvironmentInstances",
            description="Get all environment instances (Kyma, Cloud Foundry) provisioned in the global account",
            args_schema=GetEnvironmentInstancesInput,
        ),
        StructuredTool.from_function(
            coroutine=get_available_environments,
            name="getAvailableEnvironments",
            description="Get available environment types that can be provisioned",
            args_schema=GetAvailableEnvironmentsInput,
        ),
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
    ]


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
    ):
        self._accounts_client = accounts_client or Client(destination_name=DEST_ACCOUNTS)
        self._entitlements_client = entitlements_client or Client(destination_name=DEST_ENTITLEMENTS)
        self._consumption_client = consumption_client or Client(destination_name=DEST_RESOURCE_CONSUMPTION)
        self._metrics_client = metrics_client or Client(destination_name=DEST_METRICS)
        self._usage_records_client = usage_records_client or Client(destination_name=DEST_USAGE_RECORDS)
        self._provisioning_client = provisioning_client or Client(destination_name=DEST_PROVISIONING)
        self._audit_logs_client = audit_logs_client or Client(destination_name=DEST_AUDIT_LOGS)

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
            response = await llm_with_tools.ainvoke(state["messages"])
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
