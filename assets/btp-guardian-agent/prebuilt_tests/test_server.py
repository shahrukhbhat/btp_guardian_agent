"""Tests for agent server startup and A2A endpoints."""

import json
import urllib.error
import urllib.request
import uuid

import pytest


@pytest.mark.server
class TestServerStartup:
    """Test that the agent server starts correctly."""

    def test_server_starts(self, start_agent):
        """Test that the server starts without errors."""
        # If we get here, the running_server fixture successfully started the server
        assert start_agent["process"].poll() is None, "Server process should be running"
        assert start_agent["port"] > 0, "Server should have a valid port"


@pytest.mark.server
class TestA2AEndpoints:
    """Test A2A protocol endpoints."""

    def test_agent_card_endpoint(self, start_agent):
        """Test that the agent card endpoint is accessible and returns valid JSON."""
        port = start_agent["port"]
        url = f"http://localhost:{port}/.well-known/agent-card.json"

        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                raw = resp.read().decode()
                status = resp.status
        except urllib.error.URLError as e:
            pytest.fail(f"Could not connect to server on port {port}: {e}")

        # Check status code
        assert status == 200, f"Agent card endpoint returned status {status}"

        # Parse and validate JSON
        try:
            card_data = json.loads(raw)
        except ValueError as e:
            pytest.fail(
                f"""Agent card endpoint returned invalid JSON: {e}
Response text: {raw[:200]}"""
            )

        # Validate it's a proper agent card with required fields
        assert "name" in card_data or "agentName" in card_data, (
            "Agent card should have a 'name' or 'agentName' field"
        )

        name = card_data.get("name") or card_data.get("agentName", "unknown")
        description = card_data.get("description", "")
        skills = card_data.get("skills") or []
        skill_names = [s.get("name", s.get("id", "?")) for s in skills]
        print(
            """
--- Agent card ---
"""
            f"""  name:        {name}
"""
            f"""  description: {description}
"""
            f"""  skills:      {', '.join(skill_names) if skill_names else '(none)'}
"""
            """------------------"""
        )

    # def test_invoke_agent(self, start_agent):
    #     """Test that the agent can be invoked and returns a response."""
    #     port = start_agent["port"]
    #     url = f"http://localhost:{port}"
    #
    #     # Create a JSON-RPC request using the A2A protocol message/send method
    #     payload = json.dumps(
    #         {
    #             "jsonrpc": "2.0",
    #             "method": "message/send",
    #             "params": {
    #                 "message": {
    #                     "messageId": str(uuid.uuid4()),
    #                     "role": "user",
    #                     "parts": [{"kind": "text", "text": "hello world"}],
    #                 }
    #             },
    #             "id": 1,
    #         }
    #     ).encode()
    #
    #     req = urllib.request.Request(
    #         url,
    #         data=payload,
    #         headers={"Content-Type": "application/json"},
    #         method="POST",
    #     )
    #
    #     try:
    #         with urllib.request.urlopen(req, timeout=60) as resp:
    #             raw = resp.read().decode()
    #             status = resp.status
    #     except urllib.error.URLError as e:
    #         pytest.fail(f"Could not connect to server on port {port}: {e}")
    #
    #     # Check status code
    #     assert status == 200, (
    #         f"""Agent invocation returned status {status}
Response: {raw[:500]}"""
    #     )
    #
    #     # Parse response
    #     try:
    #         result = json.loads(raw)
    #     except ValueError as e:
    #         pytest.fail(
    #             f"""Agent invocation returned invalid JSON: {e}
Response text: {raw[:200]}"""
    #         )
    #
    #     # Validate JSON-RPC response structure
    #     assert "jsonrpc" in result, "Response should have jsonrpc field"
    #     assert "id" in result, "Response should have id field"
    #
    #     # Check for errors
    #     if "error" in result:
    #         pytest.fail(f"Agent returned error: {result['error']}")
    #
    #     # Validate result exists and has content
    #     assert "result" in result, "Response should have result field"
    #     task_or_message = result["result"]
    #     assert task_or_message is not None, "Result should not be None"
    #
    #     # Result should be either a Task or Message object with some content
    #     assert isinstance(task_or_message, dict), "Result should be a dictionary"
    #
    #     # Validate the task did not produce an error response.
    #     # A swallowed exception in the agent yields content starting with "Error:"
    #     # which the executor maps to a completed task â check artifacts for this.
    #     artifacts = task_or_message.get("artifacts") or []
    #     for artifact in artifacts:
    #         for part in artifact.get("parts") or []:
    #             text = (part.get("text") or "").strip()
    #             if text:
    #                 print(f"""
    #--- Agent response ---
    #{text}
    #----------------------""")
    #                 assert not text.startswith("Error:"), (
    #                     f"Agent returned an error response: {text[:200]}"
    #                 )
