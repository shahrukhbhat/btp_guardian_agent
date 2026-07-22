---
name: cap-development
description: Bootstraps a CAP Node.js application, provides expert guidance for building and extending CAP Node.js applications. Covers project initialization, CDS modeling, declarative annotations, and custom handler best practices. Use when the user wants to create a fullstack application or extend an existing CAP backend.
license: MIT
metadata:
  version: 1.0.0
  author: sap-joule-studio
---

## What I do

Provide correct, lean, idiomatic, testable guidance for CAP (Cloud Application Programming Model) Node.js
development — from project setup and CDS modeling through declarative annotations and custom
event handlers.

## Steps

### 1. MCP Server Exploration

**CRITICAL — You MUST complete this step before writing any code, models, or config.**

Always use the local cap-mcp server to:
- Search CAP documentation before guessing at APIs or annotations
- Read the effective CDS model of an existing project before adding or changing anything

Never use the local cap-mcp server as Fiori/SAPUI5 documentation. It contains useful information about
how CAP integrates with Fiori/SAPUI5 but is not a complete reference for those frameworks.

Always use the ui5-wcr Server to:
- Retrieve available UI components
- Search UI5 web component documentation before guessing at APIs

### 2. Project Initialization

When starting a new project, run `sh ./scripts/project-init.sh <name>` from the `assets/` directory. **Never** run it with bash as it will fail.

Rules:
- **Never** run `cds add sample` — it scaffolds a full demo app into the project.
- **Never** run `npm install` in the `app/react-ui` folder, only run it on root level as `app/react-ui` is a workspace.
- Use `cds add tiny-sample` only if the user explicitly wants a minimal starter model.
- Use `cds add <feature>` (e.g. `xsuaa`, `approuter`, `mta`) to add additional features incrementally and only when needed (e.g. when deployment is requested).

### 3. CDS Modeling

- **REQUIRED**: Before creating or modifying ANY `.cds` file, call `search_model`
- Reuse built-in aspects: `cuid`, `managed`, `temporal` from `@sap/cds/common`
- Use `Composition of many` for parent-child / document structures; `Association to` for references
- Use `localized String` for user-facing text that needs translation
- Naming: PascalCase for entities and types, camelCase for elements
- Define a `namespace` in `db/schema.cds` to avoid naming collisions between db and service layers
- Always expose db entities via projections in services — never expose db entities directly
- Expose only the elements clients actually need; use `excluding { ... }` to trim
- Don't expose an entity just because it exists — shape the projection for the consumer: trim with `excluding`, add calculated fields or flattened associations (e.g. `author.name as author`), and restrict with `@restrict`; only reach for actions/functions when the shape can't be expressed declaratively
- Entities written to only internally don't belong in the public service; put them in an admin service if needed
- Avoid two projections in the same service pointing to the same underlying entity — CDS can't auto-redirect associations and will error; remove the redundant projection or use `@cds.redirection.target`
- Always use `sap.common.CodeList` entities for status, type, category, or any classifying field — **never** use plain `String enum` types
- Avoid naming custom CDS actions with names that overlap ApplicationService base class methods (resolve, reject, run, send, emit, read, etc.). These cause silent or confusing overrides at handler registration time. Prefer domain-prefixed names like closePIP, submitOrder, approveRequest.

### 4. Declarative First

Prefer annotations over custom handler code. Only write handlers when declarative options are insufficient.

| Need | Annotation |
|---|---|
| Input validation (format) | `@assert.format: '...'` |
| Input validation (range) | `@assert.range: [min, max]` |
| Cross-field / exists check | `@assert: (case when ... then '...' end)` |
| Required field / parameter | `@mandatory` |
| Read-only entity | `@readonly` |
| Insert-only entity | `@insertonly` |
| Authorization | `@restrict` / `@requires` |
| Audit fields | `: managed` aspect |

### 5. File & Service Conventions

- Match `.cds` and `.js` file names exactly (e.g. `order-service.cds` + `order-service.js`) — CAP auto-discovers implementations by convention; no `@impl` annotation needed
- One service per `.cds` file — splitting services keeps convention-based matching clean
- Use `@restrict` with `where` conditions (e.g. `userID = $user`) for row-level access control; don't rely on application-level filtering

### 6. Custom Handlers

When handlers are necessary:

- Call `search_docs` on cds-mcp for the API you're about to use
- Register with `srv.on`, `srv.before`, `srv.after` — use the correct phase
- Reject with `req.reject(code, message)` — never throw raw errors
- Use explicit column lists in SELECT — never `SELECT *`
- Rely on CAP's intrinsic transaction handling — no manual transactions
- Minimize DB round-trips: combine checks into the query itself rather than SELECT + check + UPDATE
- Before writing any validation logic, check if it can be expressed declaratively

  ```js
  // ❌ two DB calls
  const row = await SELECT.one.from(Entity).where({ ID })
  if (row.status === 'locked') return req.reject(400, '...')
  await UPDATE(Entity, ID).with({ status: 'locked' })

  // ✅ one DB call
  const n = await UPDATE(Entity, ID)
    .where({ status: { '!=': 'locked' } })
    .with({ status: 'locked' })
  if (!n) return req.reject(409, 'Not found or already locked')
  ```

### 7. Sample Data

Generate data files with the CLI, never create them manually or invent UUIDs.

1. Generate CSVs for all entities: `cds add data --records <Amount>`
   Use `--filter <Entity>` to scope to specific entities (case-insensitive substring match; use regex like "books$" to exclude .texts compositions).
2. Replace placeholder values (e.g. title-29894036) with realistic domain content. Keep generated IDs and foreign-key references intact.
- **Gotcha**: `cds add data` without `--records` generates header-only CSVs, always pass `--records`.

### 8. Write Tests

*CRITICAL — NEVER skip this step*. Immediately after adding any custom logic in event handlers. **Never** write tests for generic functionality the service provider already handles. Use **jest** as the test runner.
Follow these [testing guidelines](./references/write-tests.md)

### 9. Frontend

The react source code lives in `app/react-ui/` (an npm workspace), already scaffolded via `cds add react --into react-ui`. Vite builds it into `app/react-ui/dist/`. Static-serve folders are configured in `package.json`:

- During development, **do not** set `cds.[development].folders.app`. CAP serves the default `app/` folder, and `cds watch` automatically starts the Vite dev server for `app/react-ui/`. Joule Studio's preview appends `/react-ui/` to the URL — overriding `folders.app` for the development profile breaks that path.
- `cds.[production].folders.app = app/react-ui/dist` — CAP serves the built output in production.

`cds watch` activates the `development` profile by default; `cds-serve` (the default `npm start`) honors `NODE_ENV=production` to activate the `production` profile.

Follow specific react instructions under [react-frontend.md](./references/react-frontend.md).

**Rules (all frontends):**

- Implement **all** features from the PRD — never build a reduced UI
- Frontend API calls must use correct URLs matching the backend service paths

### 10. Validate and Start Application in Background

*CRITICAL — NEVER skip this step*.

1. `npm install` (wait for it to finish).
2. Run `npm run build` and make sure there are no errors.
3. Start `cds watch`:
   - **If the `start_background_task` tool is available**: invoke it with `task_id: "cds-watch"` and `command: "cds watch < /dev/null 2>&1 | tee /tmp/cds-watch.log"` (run from the project directory). The `< /dev/null` keeps `cds watch` alive in a non-interactive shell; `tee` mirrors output to a log file. Use `check_background_task` and `stop_background_task` with the same `task_id` to read its output or kill it.
   - **Otherwise**: run `(cds watch < /dev/null > /tmp/cds-watch.log 2>&1 &)` (this backgrounds the server so it keeps running).
4. Verify that the ui is correctly served as the main entry point and that all service endpoints used in the UI are reachable on the CAP service.
5. When pointing the user to the running app:
   - **If the `start_background_task` tool is available** (Joule Studio runtime): tell the user to open the app via Joule Studio's **Preview** function — do **not** mention `http://localhost:4004` (it isn't reachable from the user's browser).
   - **Otherwise** (local development): share the `http://localhost:4004` URL printed by `cds watch`.

## Don't

- Write handlers for things the generic service provider already handles
- Hardcode tenant IDs, system IDs, or credentials anywhere
- Put user-facing strings inline — use `_i18n/` bundles
- Run `cds add sample`
- Use `await` inside a synchronous `cds.on('served', ...)` callback
