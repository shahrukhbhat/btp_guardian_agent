"""Unit tests for entitlement utilization analysis (Milestone M3)."""

import pytest


@pytest.mark.asyncio
async def test_milestone_entitlement_achieved(btp_guardian_agent):
    """M3.achieved: entitlement utilization computed."""
    await btp_guardian_agent.milestone_entitlement_utilization(
        entitlement_count=3, success=True
    )


@pytest.mark.asyncio
async def test_milestone_entitlement_missed(btp_guardian_agent):
    """M3.missed: entitlement analysis incomplete."""
    await btp_guardian_agent.milestone_entitlement_utilization(
        entitlement_count=0, success=False, reason="Entitlements API returned 403"
    )


@pytest.mark.asyncio
async def test_invoke_entitlement_query(btp_guardian_agent, mock_llm):
    """Agent answers entitlement utilization query."""
    from langchain_core.messages import AIMessage
    mock_llm.ainvoke.return_value = AIMessage(
        content="SAP HANA Cloud is 90.5% utilized (9.05/10 instances). SAP BTP Kyma Runtime is 50% utilized (2/4 clusters). SAP Business Application Studio is 10% utilized (5/50 users)."
    )
    result = await btp_guardian_agent.invoke(
        query="Show me entitlement utilization across all services",
        context_id="ctx-ent-001",
    )
    assert result.status == "completed"
    assert result.message
