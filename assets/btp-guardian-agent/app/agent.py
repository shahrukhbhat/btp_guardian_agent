import logging
import os
from dataclasses import dataclass
from typing import AsyncGenerator, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
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
- When answering cost queries, always specify the time period and currency.
- For governance queries, classify issues by severity: critical / warning / info.
- You are read-only — never suggest or imply write or modify operations on BTP resources.
- When a query requires multiple API calls (e.g. topology then cost), chain them step by step
  and synthesise a single, cohesive answer.
"""


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


def _build_domain_tools(
    accounts_client: Client,
    entitlements_client: Client,
    consumption_client: Client,
    metrics_client: Client,
    usage_records_client: Client,
    provisioning_client: Client,
) -> list:
    """Build LangChain StructuredTool instances backed by direct BTP REST clients."""
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    # -----------------------------------------------------------------------
    # Accounts Service tools
    # -----------------------------------------------------------------------

    class GetGlobalAccountInput(BaseModel):
        expand: bool = Field(default=False, description="Whether to expand child entities")

    async def get_global_account(expand: bool = False) -> str:
        import json
        result = await accounts_client.get(
            "/accounts/v1/globalAccount",
            params={"expand": str(expand).lower()},
        )
        return json.dumps(result)

    class GetSubaccountsInput(BaseModel):
        directoryGUID: str | None = Field(default=None, description="Filter by directory GUID")
        labelSelector: str | None = Field(default=None, description="Label selector filter")

    async def get_subaccounts(
        directoryGUID: str | None = None,
        labelSelector: str | None = None,
    ) -> str:
        import json
        params: dict = {}
        if directoryGUID:
            params["directoryGUID"] = directoryGUID
        if labelSelector:
            params["labelSelector"] = labelSelector
        result = await accounts_client.get("/accounts/v1/subaccounts", params=params)
        return json.dumps(result)

    class GetDirectoriesInput(BaseModel):
        expand: bool = Field(default=True, description="Whether to include subaccounts in response")

    async def get_directories(expand: bool = True) -> str:
        import json
        # Directories are retrieved via global account with expand
        result = await accounts_client.get(
            "/accounts/v1/globalAccount",
            params={"expand": str(expand).lower()},
        )
        return json.dumps(result)

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

    async def get_global_account_assignments(
        entitledServicesOnly: bool = False,
        assignedServiceName: str | None = None,
    ) -> str:
        import json
        params: dict = {"entitledServicesOnly": str(entitledServicesOnly).lower()}
        if assignedServiceName:
            params["assignedServiceName"] = assignedServiceName
        result = await entitlements_client.get(
            "/entitlements/v1/globalAccountAssignments", params=params
        )
        return json.dumps(result)

    class GetSubaccountAssignmentsInput(BaseModel):
        subaccountGUID: str | None = Field(
            default=None, description="Subaccount GUID to filter assignments"
        )
        entitledServicesOnly: bool = Field(
            default=False, description="Return only entitled services"
        )

    async def get_subaccount_assignments(
        subaccountGUID: str | None = None,
        entitledServicesOnly: bool = False,
    ) -> str:
        import json
        params: dict = {"entitledServicesOnly": str(entitledServicesOnly).lower()}
        if subaccountGUID:
            params["subaccountGUID"] = subaccountGUID
        result = await entitlements_client.get(
            "/entitlements/v1/assignments", params=params
        )
        return json.dumps(result)

    # -----------------------------------------------------------------------
    # Resource Consumption tools
    # -----------------------------------------------------------------------

    class MonthlySubaccountCmCostsInput(BaseModel):
        filter: str | None = Field(default=None, description="OData $filter expression")
        top: int = Field(default=100, description="Maximum number of results (max 100)")
        skip: int | None = Field(default=None, description="Number of results to skip")

    async def monthly_subaccount_cm_costs(
        filter: str | None = None,
        top: int = 100,
        skip: int | None = None,
    ) -> str:
        import json
        params: dict = _enforce_page_size({"$top": top}, "$top")
        if filter:
            params["$filter"] = filter
        if skip is not None:
            params["$skip"] = skip
        result = await consumption_client.get(
            "/odata/MonthlySubaccountCmCosts", params=params
        )
        return json.dumps(result)

    class MonthlyUsageInput(BaseModel):
        fromDate: str | None = Field(
            default=None, description="Start date (YYYY-MM format)"
        )
        toDate: str | None = Field(
            default=None, description="End date (YYYY-MM format)"
        )

    async def monthly_usage(
        fromDate: str | None = None,
        toDate: str | None = None,
    ) -> str:
        import json
        params: dict = {}
        if fromDate:
            params["fromDate"] = fromDate
        if toDate:
            params["toDate"] = toDate
        result = await consumption_client.get("/reports/v1/monthlyUsage", params=params)
        return json.dumps(result)

    class CloudCreditsDetailsInput(BaseModel):
        viewPhases: str | None = Field(
            default=None, description="Comma-separated view phases to include"
        )

    async def cloud_credits_details(viewPhases: str | None = None) -> str:
        import json
        params: dict = {}
        if viewPhases:
            params["viewPhases"] = viewPhases
        result = await consumption_client.get(
            "/reports/v1/cloudCreditsDetails", params=params
        )
        return json.dumps(result)

    # -----------------------------------------------------------------------
    # Metrics API tools
    # -----------------------------------------------------------------------

    class GetAppMetricsInput(BaseModel):
        subaccountName: str = Field(description="Subaccount technical name")
        appName: str = Field(description="Application name")

    async def get_app_metrics(subaccountName: str, appName: str) -> str:
        import json
        result = await metrics_client.get(
            f"/accounts/{subaccountName}/apps/{appName}/metrics"
        )
        return json.dumps(result)

    class GetAppStateInput(BaseModel):
        subaccountName: str = Field(description="Subaccount technical name")
        appName: str = Field(description="Application name")

    async def get_app_state(subaccountName: str, appName: str) -> str:
        import json
        result = await metrics_client.get(
            f"/accounts/{subaccountName}/apps/{appName}/state"
        )
        return json.dumps(result)

    # -----------------------------------------------------------------------
    # Usage Records tools
    # -----------------------------------------------------------------------

    class GetUsageRecordsInput(BaseModel):
        limit: int = Field(default=100, description="Maximum number of records (max 100)")

    async def get_usage_records(limit: int = 100) -> str:
        import json
        params = _enforce_page_size({"limit": limit}, "limit")
        result = await usage_records_client.get("/usage-records", params=params)
        return json.dumps(result)

    # -----------------------------------------------------------------------
    # Provisioning Service tools
    # -----------------------------------------------------------------------

    class GetEnvironmentInstancesInput(BaseModel):
        subaccountGUID: str | None = Field(
            default=None, description="Filter by subaccount GUID"
        )

    async def get_environment_instances(subaccountGUID: str | None = None) -> str:
        import json
        params: dict = {}
        if subaccountGUID:
            params["subaccountGUID"] = subaccountGUID
        result = await provisioning_client.get(
            "/provisioning/v1/environments", params=params
        )
        return json.dumps(result)

    class GetAvailableEnvironmentsInput(BaseModel):
        pass

    async def get_available_environments() -> str:
        import json
        result = await provisioning_client.get("/provisioning/v1/availableEnvironments")
        return json.dumps(result)

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
            description="Get monthly subaccount costs from consumption-based commercial model billing",
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
    ):
        self._accounts_client = accounts_client or Client(destination_name=DEST_ACCOUNTS)
        self._entitlements_client = entitlements_client or Client(destination_name=DEST_ENTITLEMENTS)
        self._consumption_client = consumption_client or Client(destination_name=DEST_RESOURCE_CONSUMPTION)
        self._metrics_client = metrics_client or Client(destination_name=DEST_METRICS)
        self._usage_records_client = usage_records_client or Client(destination_name=DEST_USAGE_RECORDS)
        self._provisioning_client = provisioning_client or Client(destination_name=DEST_PROVISIONING)

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
                get_model_name(), temperature=get_temperature()
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
        return builder.compile()

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
        messages = [
            SystemMessage(content=get_system_prompt()),
            HumanMessage(content=query),
        ]
        graph = await self._get_graph()
        result = await graph.ainvoke({"messages": messages})
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
