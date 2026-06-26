"""Integration test: end-to-end agent flow with mocked LLM and fake BTP clients."""

import pytest
from langchain_core.messages import AIMessage


@pytest.mark.asyncio
async def test_end_to_end_cost_ranking(btp_guardian_agent, mock_llm):
    """
    End-to-end flow: user asks for highest-cost subaccounts.
    Agent calls Resource Consumption tool, returns ranked list.
    """
    expected_response = (
        "Top subaccounts by cost in April 2026 (EUR):\n"
        "1. Production — EUR 18,450.00\n"
        "2. Staging — EUR 4,200.00\n"
        "3. Development — EUR 1,890.00\n"
        "4. Testing — EUR 750.00\n"
        "5. Sandbox — EUR 120.00\n"
        "Note: page-size limit of 100 was applied."
    )
    mock_llm.ainvoke.return_value = AIMessage(content=expected_response)

    result = await btp_guardian_agent.invoke(
        query="Which subaccounts have the highest cost this month?",
        context_id="ctx-integration-001",
    )

    assert result.status == "completed"
    assert result.message
    assert len(result.message) > 0


@pytest.mark.asyncio
async def test_end_to_end_stream(btp_guardian_agent, mock_llm):
    """
    End-to-end stream flow: agent yields intermediate + final chunks.
    """
    mock_llm.ainvoke.return_value = AIMessage(
        content="SAP HANA Cloud in Production is at 90.5% capacity."
    )

    chunks = []
    async for chunk in btp_guardian_agent.stream(
        query="Show me near-capacity entitlements",
        context_id="ctx-stream-001",
    ):
        chunks.append(chunk)

    assert len(chunks) >= 2  # intermediate + final
    final = chunks[-1]
    assert final["is_task_complete"] is True
    assert final["content"]


@pytest.mark.asyncio
async def test_end_to_end_governance_assessment(btp_guardian_agent, mock_llm):
    """
    End-to-end governance flow: agent identifies policy violations.
    """
    mock_llm.ainvoke.return_value = AIMessage(
        content="[WARNING] Sandbox subaccount has zero usage of hana-cloud entitlement for 30+ days.\n[INFO] 1 admin binding without owner description in Production."
    )

    result = await btp_guardian_agent.invoke(
        query="Run a full governance posture check",
        context_id="ctx-governance-e2e-001",
    )

    assert result.status == "completed"
    assert result.message
