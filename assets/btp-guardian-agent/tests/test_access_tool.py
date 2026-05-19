"""Unit tests for access & identity governance visibility (R6)."""

import pytest


@pytest.mark.asyncio
async def test_invoke_access_query(btp_guardian_agent, mock_llm):
    """Agent reports role binding summary across subaccounts."""
    from langchain_core.messages import AIMessage
    mock_llm.ainvoke.return_value = AIMessage(
        content="Access Governance Summary:\n| Subaccount | User/Group | Role | Justification |\n|---|---|---|---|\n| Production | john.doe@example.com | Org Manager | Missing |\n| Development | team-devops | Space Developer | OK |"
    )
    result = await btp_guardian_agent.invoke(
        query="Who has admin access across subaccounts?",
        context_id="ctx-access-001",
    )
    assert result.status == "completed"
    assert result.message


@pytest.mark.asyncio
async def test_invoke_privilege_drift_query(btp_guardian_agent, mock_llm):
    """Agent can detect privilege drift."""
    from langchain_core.messages import AIMessage
    mock_llm.ainvoke.return_value = AIMessage(
        content="Privilege drift detected in 1 subaccount: Production subaccount has 1 Org Manager binding with no owner description set."
    )
    result = await btp_guardian_agent.invoke(
        query="Are there any privilege drift issues?",
        context_id="ctx-privilege-001",
    )
    assert result.status == "completed"
    assert result.message
