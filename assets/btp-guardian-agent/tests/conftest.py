"""Test fixtures for BTP Guardian agent tests."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

# Ensure app/ is on sys.path for peer-level imports
_app_path = str(Path(__file__).parent.parent / "app")
if _app_path not in sys.path:
    sys.path.insert(0, _app_path)


def _make_mock_tools() -> list:
    """Return a minimal list of mock StructuredTool instances."""

    def _noop(**kwargs):
        return {"mock": "response"}

    return [
        StructuredTool.from_function(
            func=_noop,
            name="getSubaccounts",
            description="Mock: list subaccounts",
        ),
        StructuredTool.from_function(
            func=_noop,
            name="monthlySubaccountCmCosts",
            description="Mock: monthly subaccount costs",
        ),
        StructuredTool.from_function(
            func=_noop,
            name="getGlobalAccountAssignments",
            description="Mock: global account entitlement assignments",
        ),
    ]


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
def btp_guardian_agent(mock_llm):
    """Return a BTPGuardianAgent instance wired with mock LLM and mock MCP tools."""
    from agent import BTPGuardianAgent

    agent = BTPGuardianAgent.__new__(BTPGuardianAgent)
    agent.llm = mock_llm
    agent._tools = _make_mock_tools()
    agent._graph = None

    # Pre-build the graph so tests don't need async setup
    agent._graph = agent._build_graph(agent._tools)

    # Patch the LLM in the built graph's model node
    original_get_graph = agent._get_graph

    async def patched_get_graph():
        return agent._graph

    agent._get_graph = patched_get_graph
    return agent
