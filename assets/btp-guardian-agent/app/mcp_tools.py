"""MCP tool loader.

Owned indirection layer between agent code and the Agent Gateway.
All agent code imports get_mcp_tools from here.

Behaviour is controlled by the IBD_TESTING environment variable:

  Production (IBD_TESTING not set):
      Uses MCPClient (mcp_client.py) to connect to the Agent Gateway via mTLS.
      Credentials are loaded from the UMS volume mount (/etc/ums/credentials/credentials)
      or the AGW_CREDENTIALS_JSON environment variable.

  Local / test mode (IBD_TESTING=1):
      Reads mcp-mock.json from the directory containing this file's parent
      (i.e. <asset-root>/mcp-mock.json) and returns LangChain StructuredTool
      instances built from the mock data â no network calls.
"""

import json
import logging
import os
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# mcp-mock.json lives at the asset root (one level above app/)
_MOCK_FILE = Path(__file__).parent.parent / "mcp-mock.json"


class _ScimGroupStore:
    """In-memory, mutable SCIM group state for local mock mode.

    Seeded from the getSCIMGroups mock response so reads start from the same
    data as before, but member add/remove via patch/update actually persist
    for the life of the process. This makes "add me to the admin group, now
    show the group" behave correctly against the mock instead of returning a
    frozen canned list. Local-only (IBD_TESTING); never used in prod.
    """

    def __init__(self, seed_list_response: dict):
        # {group_id: group_dict} — each group keeps its own members list.
        self._groups: dict[str, dict] = {}
        for grp in (seed_list_response or {}).get("Resources", []):
            gid = grp.get("id")
            if gid:
                self._groups[gid] = json.loads(json.dumps(grp))  # deep copy

    def _find(self, group_id=None, display_name=None):
        if group_id and group_id in self._groups:
            return self._groups[group_id]
        if display_name:
            for g in self._groups.values():
                if g.get("displayName") == display_name:
                    return g
        return None

    def list_groups(self) -> str:
        resources = list(self._groups.values())
        return json.dumps({
            "totalResults": len(resources),
            "startIndex": 1,
            "itemsPerPage": len(resources),
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "Resources": resources,
        })

    def get_group(self, group_id=None, displayName=None, **_) -> str:
        g = self._find(group_id, displayName)
        if g is None:
            return json.dumps({"error": "Group not found", "groupId": group_id, "displayName": displayName})
        return json.dumps(g)

    @staticmethod
    def _member(value=None, display=None):
        return {
            "value": value or f"a1b2c3d4-{uuid.uuid4().hex[:12]}",
            "type": "USER",
            "display": display or value or "",
        }

    def _add_member(self, group, display=None, value=None):
        members = group.setdefault("members", [])
        # de-dupe on the display email (case-insensitive) or value
        key = (display or value or "").lower()
        for m in members:
            if (m.get("display") or "").lower() == key or (m.get("value") or "").lower() == key:
                return  # already present
        members.append(self._member(value=value, display=display))

    def _remove_member(self, group, display=None, value=None):
        key = (display or value or "").lower()
        group["members"] = [
            m for m in group.get("members", [])
            if (m.get("display") or "").lower() != key and (m.get("value") or "").lower() != key
        ]

    def patch_group(self, groupId=None, operations=None, **_) -> str:
        g = self._find(groupId)
        if g is None:
            return json.dumps({"error": "Group not found", "groupId": groupId})
        for op in operations or []:
            action = (op.get("op") or op.get("operation") or "").lower()
            path = (op.get("path") or "").lower()
            value = op.get("value")
            if "member" not in path and path:  # only handle member ops
                continue
            entries = value if isinstance(value, list) else [value] if value else []
            for entry in entries:
                if isinstance(entry, str):
                    display, val = (entry, None) if "@" in entry else (None, entry)
                elif isinstance(entry, dict):
                    val = entry.get("value")
                    display = entry.get("display") or (val if val and "@" in val else None)
                else:
                    continue
                if action in ("add", ""):
                    self._add_member(g, display=display, value=val)
                elif action in ("remove", "delete"):
                    self._remove_member(g, display=display, value=val)
        return json.dumps(g)

    def update_group(self, groupId=None, members=None, description=None, **_) -> str:
        g = self._find(groupId)
        if g is None:
            return json.dumps({"error": "Group not found", "groupId": groupId})
        if members is not None:
            g["members"] = [
                self._member(value=m.get("value"), display=m.get("display")) if isinstance(m, dict)
                else self._member(display=m)
                for m in members
            ]
        if description is not None:
            g["description"] = description
        return json.dumps(g)


def _build_mock_tools() -> list:
    """Build LangChain StructuredTool instances from mcp-mock.json.

    Returns an empty list (without error) when mcp-mock.json is absent or
    cannot be parsed â add/fix the file to enable tool mocking.
    """
    if not _MOCK_FILE.exists():
        return []

    try:
        mock_data = json.loads(_MOCK_FILE.read_text())
    except Exception:
        logger.warning("Failed to parse mcp-mock.json at %s â returning empty tool list", _MOCK_FILE, exc_info=True)
        return []

    tools = []

    from langchain_core.tools import StructuredTool
    from pydantic import Field, create_model

    # Seed the stateful SCIM group store from the getSCIMGroups mock so member
    # add/remove persists in-process (local mock only).
    _scim_seed = (
        mock_data.get("servers", {})
        .get("xsuaa-scim", {})
        .get("tools", {})
        .get("getSCIMGroups", {})
        .get("mock_response", {})
    )
    scim_store = _ScimGroupStore(_scim_seed)
    _scim_handlers = {
        "getSCIMGroups": lambda **kw: scim_store.list_groups(),
        "getSCIMGroup": lambda **kw: scim_store.get_group(**kw),
        "patchSCIMGroup": lambda **kw: scim_store.patch_group(**kw),
        "updateSCIMGroup": lambda **kw: scim_store.update_group(**kw),
    }

    for _server_slug, server in mock_data.get("servers", {}).items():
        for tool_name, tool_def in server.get("tools", {}).items():
            description = tool_def.get("description", "")
            mock_response = tool_def.get("mock_response", {})
            input_schema = tool_def.get("input_schema", {})

            props = input_schema.get("properties", {})
            required_fields = set(input_schema.get("required", []))
            field_definitions: dict = {}
            for field_name, field_info in props.items():
                json_type = field_info.get("type", "string")
                if json_type == "integer":
                    python_type = int
                elif json_type == "number":
                    python_type = float
                elif json_type == "boolean":
                    python_type = bool
                elif json_type == "array":
                    python_type = list
                elif json_type == "object":
                    python_type = dict
                else:
                    python_type = str

                if field_name in required_fields:
                    field_definitions[field_name] = (python_type, Field(description=field_info.get("description", "")))
                else:
                    field_definitions[field_name] = (python_type, Field(default=None, description=field_info.get("description", "")))

            args_schema = create_model(f"{tool_name}_args", **field_definitions) if field_definitions else create_model(f"{tool_name}_args")

            if _server_slug == "xsuaa-scim" and tool_name in _scim_handlers:
                # Stateful: reads/mutations go through the shared in-memory store.
                _handler = _scim_handlers[tool_name]

                async def _coroutine(_h=_handler, **kwargs) -> str:
                    return _h(**kwargs)
            else:
                _response = json.dumps(mock_response)

                async def _coroutine(_resp=_response, **kwargs) -> str:
                    return _resp

            tools.append(
                StructuredTool(
                    name=tool_name,
                    description=description,
                    args_schema=args_schema,
                    coroutine=_coroutine,
                )
            )

    logger.info("Loaded %d mock MCP tool(s) from %s", len(tools), _MOCK_FILE)
    return tools


async def get_mcp_tools() -> list:
    """Return LangChain-compatible MCP tools.

    In local/test mode (IBD_TESTING=1): returns mock tools from mcp-mock.json.
    In production: uses MCPClient to connect to Agent Gateway via mTLS.
    """
    if os.environ.get("IBD_TESTING") == "1":
        return _build_mock_tools()

    from mcp_client import MCPClient, MCPToolConverter
    try:
        client = MCPClient()
        mcp_tools = await client.get_mcp_tools()
        converter = MCPToolConverter(client)
        langchain_tools = [converter.to_langchain(t) for t in mcp_tools]
        logger.info("Loaded %d MCP tool(s) from Agent Gateway via MCPClient", len(langchain_tools))
        return langchain_tools
    except Exception:
        logger.exception("Failed to load MCP tools from Agent Gateway")
        return []
