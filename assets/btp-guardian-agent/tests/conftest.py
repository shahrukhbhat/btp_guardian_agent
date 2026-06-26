"""Test fixtures for BTP Guardian agent tests.

Replaces MCP tool fixtures (Joule) with direct FakeClient instances (CF).
The btp_guardian_agent fixture builds the LangGraph with a mock LLM and
fake BTP API clients — no network calls, no VCAP_SERVICES required.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage

# Ensure app/ is on sys.path for peer-level imports
_app_path = str(Path(__file__).parent.parent / "app")
if _app_path not in sys.path:
    sys.path.insert(0, _app_path)

from tests._helpers import FakeClient, make_fake  # noqa: E402


def _make_fake_clients() -> dict[str, FakeClient]:
    """Return a dict of FakeClient instances for all 6 BTP services."""
    return {
        "accounts": make_fake({
            "/accounts/v1/globalAccount": {
                "guid": "ga-001",
                "displayName": "Test Global Account",
                "subaccounts": [
                    {"guid": "sa-001", "displayName": "Production", "region": "eu10"},
                    {"guid": "sa-002", "displayName": "Development", "region": "eu10"},
                ],
            },
            "/accounts/v1/subaccounts": {
                "value": [
                    {"guid": "sa-001", "displayName": "Production", "region": "eu10"},
                    {"guid": "sa-002", "displayName": "Development", "region": "eu10"},
                ]
            },
        }),
        "entitlements": make_fake({
            "/entitlements/v1/globalAccountAssignments": {
                "entitledServices": [
                    {
                        "name": "hana-cloud",
                        "displayName": "SAP HANA Cloud",
                        "servicePlans": [{"name": "hana", "amount": 10, "remaining": 0.95}],
                    }
                ]
            },
            "/entitlements/v1/assignments": {"entitledServices": []},
        }),
        "consumption": make_fake({
            "/odata/MonthlySubaccountCmCosts": {
                "value": [
                    {"SubaccountId": "sa-001", "Cost": "18450.00", "Currency": "EUR"},
                    {"SubaccountId": "sa-002", "Cost": "1890.00", "Currency": "EUR"},
                ]
            },
            "/reports/v1/monthlyUsage": {"items": []},
            "/reports/v1/cloudCreditsDetails": {"cloudCredits": []},
        }),
        "metrics": make_fake({
            "/apps/": {"metrics": []},
            "/state": {"state": "STARTED"},
        }),
        "usage_records": make_fake({
            "/usage-records": {"value": []},
        }),
        "provisioning": make_fake({
            "/provisioning/v1/environments": {"environmentInstances": []},
            "/provisioning/v1/availableEnvironments": {"availableEnvironments": []},
        }),
    }


@pytest.fixture
def mock_llm():
    """Return a mock LLM that returns a canned AIMessage by default."""
    llm = MagicMock()
    llm.bind_tools.return_value = llm
    llm.ainvoke = AsyncMock(
        return_value=AIMessage(content="Mock LLM response for BTP Guardian test")
    )
    return llm


@pytest.fixture
def fake_clients() -> dict[str, FakeClient]:
    """Return a fresh set of FakeClient instances for all BTP services."""
    return _make_fake_clients()


@pytest.fixture
def btp_guardian_agent(mock_llm, fake_clients):
    """Return a BTPGuardianAgent instance wired with mock LLM and fake BTP clients."""
    from agent import BTPGuardianAgent

    agent = BTPGuardianAgent(
        accounts_client=fake_clients["accounts"],
        entitlements_client=fake_clients["entitlements"],
        consumption_client=fake_clients["consumption"],
        metrics_client=fake_clients["metrics"],
        usage_records_client=fake_clients["usage_records"],
        provisioning_client=fake_clients["provisioning"],
    )
    # Inject mock LLM so tests don't hit AI Core
    agent._llm = mock_llm

    # Pre-build the graph with the mock LLM
    tools = agent._get_tools()
    agent._graph = agent._build_graph(tools, mock_llm)

    return agent
