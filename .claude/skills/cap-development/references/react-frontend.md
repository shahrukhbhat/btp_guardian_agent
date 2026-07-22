# Generate a React frontend for a CAP backend

Create and modify React frontends in SAP CAP applications using **React** and **UI5 Web Components**.

## Step 1 — Get the service model

Use the `search_model` tool from the cds-mcp to get access of the service definition.

For each service, collect:

| Item | Source |
|---|---|
| Service name | Definition name where `kind === "service"` |
| OData base path | `@path` annotation or `endpoints[].path` from MCP |
| Entities | Entity names scoped to the service (e.g. `OrderService.Orders`) |
| Key field(s) | Elements with `key: true` |
| Scalar fields + types | Elements with `type` like `cds.String`, `cds.Integer`, `cds.Decimal`, `cds.Boolean`, `cds.Timestamp`, `cds.UUID`, etc. |
| Bound actions | `actions` block on the entity — name, params (name + type) |
| Readonly | `@readonly` annotation on the entity |

## Step 2 — Implement the UI application

  - Before implementing ANY UI component, call `list_components` from the ui5-wcr mcp server to retrieve available UI5 Web Components. Use ONLY components returned by this call.
  - Before implementing ANY UI component, call `get_component_api` from the ui5-wcr mcp server with the component name to retrieve its full API (properties, events, slots, methods). Use ONLY the API surface returned by this call.
  - Use only OData v4 as communication protocol with the cap service. The fetch URL for an entity is /<@path-value>/<EntitySet> — do not prepend /odata/v4/.
  - Never create internationalization / messagebundles unless specifically requested by user.
  - Always prefer UI5 web components over custom elements.
