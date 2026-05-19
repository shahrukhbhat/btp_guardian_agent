"""Unit tests for consumption & cost queries (Milestone M2)."""

import pytest


@pytest.mark.asyncio
async def test_milestone_consumption_achieved(btp_guardian_agent):
    """M2.achieved: consumption data retrieved."""
    await btp_guardian_agent.milestone_consumption_data(
        service_count=3, period="2026-04", success=True
    )


@pytest.mark.asyncio
async def test_milestone_consumption_missed(btp_guardian_agent):
    """M2.missed: consumption data unavailable."""
    await btp_guardian_agent.milestone_consumption_data(
        service_count=0, period="", success=False
    )


@pytest.mark.asyncio
async def test_invoke_cost_query(btp_guardian_agent, mock_llm):
    """Agent can answer a cost ranking query via invoke()."""
    from langchain_core.messages import AIMessage
    mock_llm.ainvoke.return_value = AIMessage(
        content="Production subaccount had the highest cost at EUR 18,450.00 in April 2026."
    )
    result = await btp_guardian_agent.invoke(
        query="Which subaccount had the highest cost this month?",
        context_id="ctx-cost-001",
    )
    assert result.status == "completed"
    assert result.message
