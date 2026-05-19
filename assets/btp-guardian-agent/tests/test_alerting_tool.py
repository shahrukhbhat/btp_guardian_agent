"""Unit tests for proactive alerting and threshold monitoring (Milestone M5)."""

import pytest


@pytest.mark.asyncio
async def test_milestone_alert_achieved(btp_guardian_agent):
    """M5.achieved: proactive alert emitted."""
    await btp_guardian_agent.milestone_proactive_alert(
        subaccount="sa-0002-prod",
        service="hana-cloud",
        breach_type="entitlement_near_exhaustion",
        success=True,
    )


@pytest.mark.asyncio
async def test_milestone_alert_missed(btp_guardian_agent):
    """M5.missed: alert generation skipped."""
    await btp_guardian_agent.milestone_proactive_alert(
        subaccount="", service="", breach_type="", success=False
    )


@pytest.mark.asyncio
async def test_invoke_alert_query(btp_guardian_agent, mock_llm):
    """Agent can identify threshold breaches."""
    from langchain_core.messages import AIMessage
    mock_llm.ainvoke.return_value = AIMessage(
        content="ALERT [CRITICAL]: SAP HANA Cloud in subaccount 'Production' is at 90.5% utilization (9.05/10 instances). Recommended action: Request additional quota or reduce non-production HANA instances."
    )
    result = await btp_guardian_agent.invoke(
        query="Are there any services approaching capacity limits?",
        context_id="ctx-alert-001",
    )
    assert result.status == "completed"
    assert result.message
