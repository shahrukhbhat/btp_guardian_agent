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


class _CfAppStore:
    """In-memory, mutable Cloud Foundry app state for local mock mode.

    Seeded from the cf-controller `_seed` block in mcp-mock.json. Models a single
    CF org/space with a set of apps. Lifecycle (start/stop/restart), scale, and
    metadata-only deploy actually mutate the in-process state, so a follow-up
    "list apps" / "get app" reflects the change within the session (like the SCIM
    store). Read shapes mirror the CF v3 Cloud Controller API. Local-only
    (IBD_TESTING); never used in prod.
    """

    def __init__(self, seed: dict):
        seed = seed or {}
        self._orgs = _deepcopy(seed.get("organizations", []))
        self._spaces = _deepcopy(seed.get("spaces", []))
        # {app_guid: app_dict}; each app carries process/stats/logs/events inline.
        self._apps: dict[str, dict] = {}
        for app in seed.get("apps", []):
            gid = app.get("guid")
            if gid:
                self._apps[gid] = _deepcopy(app)

    # -- resolution helpers --------------------------------------------------
    def _find_app(self, guid=None, name=None):
        if guid and guid in self._apps:
            return self._apps[guid]
        if guid:  # a guid was given but not found — also allow guid==name match
            for a in self._apps.values():
                if a.get("guid") == guid:
                    return a
        if name:
            for a in self._apps.values():
                if a.get("name") == name:
                    return a
        return None

    @staticmethod
    def _now_ms():
        import time
        return int(time.time() * 1000)

    def _app_summary(self, app: dict) -> dict:
        # The list/get shape: CF v3 app resource core fields.
        web = (app.get("stats") or {}).get("web") or []
        cpus = [s.get("usage", {}).get("cpu_pct") for s in web
                if isinstance(s.get("usage", {}).get("cpu_pct"), (int, float))]
        cpu_pct = round(sum(cpus) / len(cpus), 1) if cpus else None
        running = sum(1 for s in web if s.get("state") == "RUNNING")
        return {
            "guid": app.get("guid"),
            "name": app.get("name"),
            "state": app.get("state"),
            "lifecycle": app.get("lifecycle", {"type": "buildpack"}),
            "current_version": app.get("current_version"),
            "space_guid": app.get("space_guid"),
            "instances": app.get("instances"),
            "cpu_pct": cpu_pct,
            "running_instances": running,
            "total_instances": app.get("instances"),
            "memory_in_mb": app.get("memory_in_mb"),
            "disk_in_mb": app.get("disk_in_mb"),
            "created_at": app.get("created_at"),
            "updated_at": app.get("updated_at"),
        }

    # -- reads ---------------------------------------------------------------
    def list_orgs(self, **_) -> str:
        return json.dumps({"pagination": {"total_results": len(self._orgs)},
                           "resources": self._orgs})

    def list_spaces(self, organizationGuid=None, **_) -> str:
        spaces = self._spaces
        if organizationGuid:
            spaces = [s for s in spaces if s.get("organization_guid") == organizationGuid]
        return json.dumps({"pagination": {"total_results": len(spaces)},
                           "resources": spaces})

    def list_apps(self, spaceGuid=None, name=None, **_) -> str:
        apps = list(self._apps.values())
        if spaceGuid:
            apps = [a for a in apps if a.get("space_guid") == spaceGuid]
        if name:
            apps = [a for a in apps if a.get("name") == name]
        resources = [self._app_summary(a) for a in apps]
        return json.dumps({"pagination": {"total_results": len(resources)},
                           "resources": resources})

    def get_app(self, appGuid=None, name=None, **_) -> str:
        app = self._find_app(appGuid, name)
        if app is None:
            return json.dumps({"errors": [{"detail": "App not found",
                                           "title": "CF-ResourceNotFound", "code": 10010}]})
        return json.dumps(self._app_summary(app))

    def get_app_processes(self, appGuid=None, name=None, **_) -> str:
        app = self._find_app(appGuid, name)
        if app is None:
            return json.dumps({"errors": [{"detail": "App not found", "code": 10010}]})
        return json.dumps({"pagination": {"total_results": len(app.get("processes", []))},
                           "resources": _deepcopy(app.get("processes", []))})

    def get_process_stats(self, appGuid=None, name=None, processType="web", **_) -> str:
        app = self._find_app(appGuid, name)
        if app is None:
            return json.dumps({"errors": [{"detail": "App not found", "code": 10010}]})
        stats = app.get("stats", {}).get(processType or "web", [])
        return json.dumps({"resources": _deepcopy(stats)})

    def get_recent_logs(self, appGuid=None, name=None, limit=None, **_) -> str:
        app = self._find_app(appGuid, name)
        if app is None:
            return json.dumps({"errors": [{"detail": "App not found", "code": 10010}]})
        logs = app.get("recent_logs", [])
        if isinstance(limit, int) and limit > 0:
            logs = logs[-limit:]
        return json.dumps({"app": app.get("name"), "envelopes": _deepcopy(logs)})

    def get_app_events(self, appGuid=None, name=None, **_) -> str:
        app = self._find_app(appGuid, name)
        if app is None:
            return json.dumps({"errors": [{"detail": "App not found", "code": 10010}]})
        events = app.get("events", [])
        return json.dumps({"pagination": {"total_results": len(events)},
                           "resources": _deepcopy(events)})

    # -- writes (stateful) ---------------------------------------------------
    def _set_state(self, app, state):
        app["state"] = state
        app["updated_at"] = self._nowiso()
        # reflect on the web process stats
        for st in app.get("stats", {}).get("web", []):
            st["state"] = "RUNNING" if state == "STARTED" else "DOWN"

    @staticmethod
    def _nowiso():
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def start_app(self, appGuid=None, name=None, **_) -> str:
        app = self._find_app(appGuid, name)
        if app is None:
            return json.dumps({"errors": [{"detail": "App not found", "code": 10010}]})
        self._set_state(app, "STARTED")
        return json.dumps(self._app_summary(app))

    def stop_app(self, appGuid=None, name=None, **_) -> str:
        app = self._find_app(appGuid, name)
        if app is None:
            return json.dumps({"errors": [{"detail": "App not found", "code": 10010}]})
        self._set_state(app, "STOPPED")
        return json.dumps(self._app_summary(app))

    def restart_app(self, appGuid=None, name=None, **_) -> str:
        app = self._find_app(appGuid, name)
        if app is None:
            return json.dumps({"errors": [{"detail": "App not found", "code": 10010}]})
        self._set_state(app, "STARTED")
        app.setdefault("events", []).insert(0, {
            "type": "audit.app.restart", "actor": "guardian-agent",
            "created_at": self._nowiso(),
        })
        return json.dumps(self._app_summary(app))

    def scale_process(self, appGuid=None, name=None, processType="web",
                      instances=None, memory_in_mb=None, disk_in_mb=None, **_) -> str:
        app = self._find_app(appGuid, name)
        if app is None:
            return json.dumps({"errors": [{"detail": "App not found", "code": 10010}]})
        if instances is not None:
            app["instances"] = instances
        if memory_in_mb is not None:
            app["memory_in_mb"] = memory_in_mb
        if disk_in_mb is not None:
            app["disk_in_mb"] = disk_in_mb
        app["updated_at"] = self._nowiso()
        # rebuild web process stats to match the new instance count
        proc_type = processType or "web"
        template = None
        existing = app.get("stats", {}).get(proc_type, [])
        if existing:
            template = _deepcopy(existing[0])
        new_stats = []
        for i in range(int(app.get("instances") or 0)):
            inst = _deepcopy(template) if template else {"state": "RUNNING"}
            inst["index"] = i
            inst["state"] = "RUNNING" if app.get("state") == "STARTED" else "DOWN"
            if memory_in_mb is not None:
                inst["mem_quota"] = memory_in_mb
            if disk_in_mb is not None:
                inst["disk_quota"] = disk_in_mb
            new_stats.append(inst)
        app.setdefault("stats", {})[proc_type] = new_stats
        # update the matching process resource's instance count too
        for p in app.get("processes", []):
            if p.get("type") == proc_type:
                if instances is not None:
                    p["instances"] = instances
                if memory_in_mb is not None:
                    p["memory_in_mb"] = memory_in_mb
                if disk_in_mb is not None:
                    p["disk_in_mb"] = disk_in_mb
        return json.dumps({
            "guid": app.get("guid"), "name": app.get("name"), "type": proc_type,
            "instances": app.get("instances"), "memory_in_mb": app.get("memory_in_mb"),
            "disk_in_mb": app.get("disk_in_mb"), "state": app.get("state"),
        })

    def create_deployment(self, appGuid=None, name=None, version=None,
                          droplet_guid=None, **_) -> str:
        app = self._find_app(appGuid, name)
        if app is None:
            return json.dumps({"errors": [{"detail": "App not found", "code": 10010}]})
        new_version = version or f"v-{uuid.uuid4().hex[:6]}"
        app["current_version"] = new_version
        app["state"] = "STARTED"
        app["updated_at"] = self._nowiso()
        app.setdefault("events", []).insert(0, {
            "type": "audit.app.deployment.create", "actor": "guardian-agent",
            "created_at": self._nowiso(), "metadata": {"version": new_version},
        })
        return json.dumps({
            "guid": f"deployment-{uuid.uuid4().hex[:12]}",
            "state": "DEPLOYED",
            "status": {"value": "FINALIZED", "reason": "DEPLOYED"},
            "app": {"guid": app.get("guid"), "name": app.get("name")},
            "new_version": new_version,
            "strategy": "rolling",
        })


def _deepcopy(obj):
    return json.loads(json.dumps(obj))


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

    # Seed the stateful Cloud Foundry app store from the cf-controller `_seed`
    # block so start/stop/scale/deploy persist in-process (local mock only).
    _cf_seed = (
        mock_data.get("servers", {})
        .get("cf-controller", {})
        .get("_seed", {})
    )
    cf_store = _CfAppStore(_cf_seed)
    _cf_handlers = {
        "getCfOrganizations": lambda **kw: cf_store.list_orgs(**kw),
        "getCfSpaces": lambda **kw: cf_store.list_spaces(**kw),
        "getCfApps": lambda **kw: cf_store.list_apps(**kw),
        "getCfApp": lambda **kw: cf_store.get_app(**kw),
        "getCfAppProcesses": lambda **kw: cf_store.get_app_processes(**kw),
        "getCfAppProcessStats": lambda **kw: cf_store.get_process_stats(**kw),
        "getCfAppRecentLogs": lambda **kw: cf_store.get_recent_logs(**kw),
        "getCfAppEvents": lambda **kw: cf_store.get_app_events(**kw),
        "startCfApp": lambda **kw: cf_store.start_app(**kw),
        "stopCfApp": lambda **kw: cf_store.stop_app(**kw),
        "restartCfApp": lambda **kw: cf_store.restart_app(**kw),
        "scaleCfAppProcess": lambda **kw: cf_store.scale_process(**kw),
        "createCfAppDeployment": lambda **kw: cf_store.create_deployment(**kw),
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
            elif _server_slug == "cf-controller" and tool_name in _cf_handlers:
                # Stateful CF app store: reads + lifecycle/scale/deploy mutations.
                _handler = _cf_handlers[tool_name]

                async def _coroutine(_h=_handler, **kwargs) -> str:
                    return _h(**kwargs)
            elif isinstance(tool_def.get("mock_responses_by"), dict):
                # Parameter-aware mock: return a different payload depending on the
                # value of one call parameter (e.g. appName). Falls back to `default`
                # (or the tool's plain `mock_response`) for unlisted/absent values.
                # Local mock only — the production path is unaffected.
                _by = tool_def["mock_responses_by"]
                _param = _by.get("param")
                _variants = {
                    str(k): json.dumps(v) for k, v in (_by.get("values") or {}).items()
                }
                _fallback = json.dumps(_by.get("default", mock_response))

                async def _coroutine(_param=_param, _variants=_variants, _fallback=_fallback, **kwargs) -> str:
                    key = kwargs.get(_param)
                    return _variants.get(str(key), _fallback)
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
