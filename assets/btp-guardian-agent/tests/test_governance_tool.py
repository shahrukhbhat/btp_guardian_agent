"""Unit tests for governance posture assessment (Milestone M4)."""

import pytest


@pytest.mark.asyncio
async def test_milestone_governance_achieved(btp_guardian_agent):
    """M4.achieved: governance posture assessed."""
    await btp_guardian_agent.milestone_governance_posture(flagged_count=2, success=True)


@pytest.mark.asyncio
async def test_milestone_governance_no_issues(btp_guardian_agent):
    """M4.achieved: governance posture clean."""
    await btp_guardian_agent.milestone_governance_posture(flagged_count=0, success=True)


@pytest.mark.asyncio
async def test_milestone_governance_missed(btp_guardian_agent):
    """M4.missed: governance assessment failed."""
    await btp_guardian_agent.milestone_governance_posture(flagged_count=0, success=False)


@pytest.mark.asyncio
async def test_invoke_governance_query(btp_guardian_agent, mock_llm):
    """Agent detects governance issues."""
    from langchain_core.messages import AIMessage
    mock_llm.ainvoke.return_value = AIMessage(
        content="Governance posture assessed. 2 issues detected:\n- [WARNING] Sandbox subaccount has hana-cloud entitlement assigned but 0 usage in last 30 days.\n- [INFO] Staging subaccount has 3 admin bindings without owner description."
    )
    result = await btp_guardian_agent.invoke(
        query="Are there any governance issues in my BTP landscape?",
        context_id="ctx-gov-001",
    )
    assert result.status == "completed"
    assert result.message
