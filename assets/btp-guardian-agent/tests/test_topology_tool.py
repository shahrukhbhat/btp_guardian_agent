"""Unit tests for account topology resolution (Milestone M1)."""

import pytest


@pytest.mark.asyncio
async def test_milestone_account_topology_achieved(btp_guardian_agent):
    """M1.achieved: topology resolved with subaccount count."""
    await btp_guardian_agent.milestone_account_topology(subaccount_count=5, success=True)
    # No exception = milestone logged successfully


@pytest.mark.asyncio
async def test_milestone_account_topology_missed(btp_guardian_agent):
    """M1.missed: topology could not be resolved."""
    await btp_guardian_agent.milestone_account_topology(subaccount_count=0, success=False)


@pytest.mark.asyncio
async def test_invoke_topology_query(btp_guardian_agent, mock_llm):
    """Agent can answer an account topology question via invoke()."""
    mock_llm.ainvoke.return_value = _make_ai_message("Here is your account topology: ...")

    result = await btp_guardian_agent.invoke(
        query="Show me the account topology", context_id="ctx-topology-001"
    )
    assert result.status == "completed"
    assert result.message


def _make_ai_message(content: str):
    from langchain_core.messages import AIMessage
    return AIMessage(content=content)
