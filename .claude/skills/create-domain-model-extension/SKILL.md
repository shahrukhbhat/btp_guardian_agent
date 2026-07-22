---
name: create-domain-model-extension
description: |
  Create domain model extensions for existing CAP entities by adding custom fields.
  This skill guides the process of extending SAP data models with additional fields while maintaining
  validation and consistency with the base data model.
metadata:
  version: 1.0.0
  author: sap-joule-studio
---

# Domain Model Extension Skill

**This skill operates on the current working directory.** The caller is responsible for running it from the correct target directory (e.g., `assets/domain-model-extension/`).

## Step 1: Gather Context

**IMPORTANT: Always gather context first** by invoking the `fetch_extensibility_context` tool to validate the user's request.

**CRITICAL ERROR HANDLING:** If the `fetch_extensibility_context` tool fails with any error (4xx, 5xx, network error, timeout) OR returns an empty response, you MUST:
1. **Immediately terminate** the domain model extension creation process
2. **Do NOT proceed** with any validation, file generation, or solution setup
3. Display this message to the user:
   ```
   Domain model extensions are not available at this time. Please try again later or contact your administrator.
   ```
4. **Exit the skill** without creating any files or making any changes

### Tool: `fetch_extensibility_context`

This tool fetches all extensibility metadata in a single call:

```
Tool: fetch_extensibility_context
Parameters:
  entityId: (optional) "<entity-id>"   # If provided, also fetches fields for that entity
```

**Response without entityId:**
```json
{
  "apps": [
    { "id": "app-bookshop", "name": "Bookshop", "description": "Sample bookshop application", "appTenantId": "tenant-123" }
  ],
  "entities": [
    { "id": "entity-addresses", "entityName": "my.bookshop.Addresses", "displayName": "Addresses", "appTenantId": "tenant-123" }
  ],
  "types": [
    { "id": "type-string", "name": "String", "isBase": true, "isExtended": false, "type": "primitive" },
    { "id": "type-integer", "name": "Integer", "isBase": true, "isExtended": false, "type": "primitive" }
  ]
}
```

**Response with entityId:**
```json
{
  "apps": [...],
  "entities": [...],
  "types": [...],
  "fields": [
    { "id": "field-1", "fieldName": "street", "dataType": "String", "fieldCode": "STREET", "fieldLabel": "Street Address", "entityId": "entity-addresses" }
  ]
}
```

### Usage Pattern

1. **First call** (no entityId) - Get apps, entities, and types to validate the user's request
2. **Second call** (with entityId) - If you need to see existing fields for an entity before adding new ones

### Matching Entities to Apps

Use the `appTenantId` field to match entities with their parent app:

1. When the user specifies an entity (e.g., "my.bookshop.Addresses"), find that entity in the `entities` list
2. Get the entity's `appTenantId` value
3. Find the matching app in the `apps` list where `app.appTenantId === entity.appTenantId`
4. Use the matched app's `name` as the `appNameSpace` in `extension.yaml`

**Example Matching:**
```json
// From fetch_extensibility_context response:
"apps": [
  { "id": "app-bookshop", "name": "Bookshop", "appTenantId": "tenant-123" },
  { "id": "app-inventory", "name": "Inventory", "appTenantId": "tenant-456" }
],
"entities": [
  { "id": "entity-addresses", "entityName": "my.bookshop.Addresses", "appTenantId": "tenant-123" },
  { "id": "entity-stock", "entityName": "my.inventory.Stock", "appTenantId": "tenant-456" }
]

// For entity "my.bookshop.Addresses":
// 1. Entity has appTenantId: "tenant-123"
// 2. App "Bookshop" has matching appTenantId: "tenant-123"
// 3. Use appNameSpace: "Bookshop" in extension.yaml
```

**Important:** If no matching app is found for an entity's `appTenantId`, report an error and do not proceed with extension creation.

## Step 2: Validate User Request

After gathering context, validate the user's request against the fetched metadata:

### 2.1 Validation Rules

1. **Entity Validation:** The requested entity must exist in the `entities` list from the context
2. **App Matching Validation:** The entity's `appTenantId` must match an app in the `apps` list
3. **Field Type Validation:** All requested field types must exist in the `types` list from the context
4. **Field Name Validation:** Field names must:
   - Start with a letter
   - Contain only alphanumeric characters and underscores
   - Match pattern: `^[a-zA-Z][a-zA-Z0-9_]*$`

### 2.2 Validation Error Handling

If validation fails, display a clear error message:

```
Validation Failed

Issues Found:
- Entity "<requested_entity>": Not found in available entities
  Available entities: [list from metadata API]

- Entity "<requested_entity>": No matching app found for appTenantId "<tenant_id>"
  Available apps: [list app names and their appTenantIds]

- Field Type "<requested_type>": Not a valid data type
  Available types: [list from metadata API]

Please correct the request and try again.
```

If validation succeeds, proceed to Step 3.

## Step 3: Generate Extension Files

Generate both `asset.yaml` and `extension.yaml` files in the current working directory.

**IMPORTANT:** The asset folder must contain ONLY these two files. No other files should be created.

### Generated File Structure

```
./                              # Current working directory (e.g., assets/domain-model-extension/)
├── asset.yaml                  # Asset descriptor (references extension.yaml)
└── extension.yaml              # Extension definition (entities, fields, translations)
```

### `asset.yaml` Template

```yaml
apiVersion: asset.sap/v1
kind: Asset

metadata:
  name: <extension-name>           # kebab-case, e.g., "bookshop-addresses-extension"
  version: 1.0.0
  description: "<description>"

type: domain-model-extension

sourceRoot: "."

provides:
  extensions:
    - descriptorFile: extension.yaml
      name: <extension-name>
```

### `extension.yaml` Template

```yaml
# Opaque content for domain model extension asset
appNameSpace: <app-namespace>
extensions:
  - extensionProject:
      name: <EXTENSION-PROJECT-NAME>
      description: <extension-project-description>
      customAttributes: []
    types: []
    entities:
      - entityName: <full-entity-name>
        displayName: <display-name>
        isParametric: false
        fields:
          - fieldName: <field-name>
            isKey: false
            type:
              name: <type-name>
              isExtended: false
              isBase: true
            fieldLabel: <field-label>
    translations:
      - entityName: <full-entity-name>
        locale: <locale>
        translationKey: '{i18n><field-name>}'
        translationValue: <translated-label>
```

## Step 4: Setup Solution

After generating both `asset.yaml` and `extension.yaml`, **automatically execute the `setup-solution` skill** to create a `solution.yaml` in this same directory with `buildPath: .` referencing this asset.

## Error Handling

### Critical: `fetch_extensibility_context` Failures (Top Priority)

**STOP IMMEDIATELY** if the `fetch_extensibility_context` tool call:
- Returns a 4xx or 5xx HTTP error status
- Returns an empty response (`{}`, `null`, or missing required fields like `apps`, `entities`, `types`)
- Throws a network error, timeout, or connection failure
- Returns any error status or exception

**When this happens:**
1. **DO NOT** proceed with validation, file generation, or solution setup
2. **DO NOT** create any files or directories
3. **DO NOT** invoke the `setup-solution` skill
4. Display this message to the user:

```
Domain model extensions are not available at this time. Please try again later or contact your administrator.
```

5. **Exit the skill immediately**

### Common Validation Errors (Only after successful context fetch)

| Error | Cause | Resolution |
|-------|-------|------------|
| `Entity not found` | Requested entity doesn't exist | Check `entities` in context response |
| `No matching app for entity` | Entity's `appTenantId` has no matching app | Verify app exists with same `appTenantId` |
| `Invalid field type` | Requested type not in allowed types | Check `types` in context response |
| `Invalid field name` | Field name doesn't match pattern | Use alphanumeric + underscore, start with letter |

## Example Usage

**User Request:**
```
Create a domain model extension for the my.bookshop.Addresses entity.
Add a field:
- testField1 (String) with label "Test Field1"
Include German translation: "Testfeld 1"
```

**Execution Flow:**
1. Fetch context via `fetch_extensibility_context` tool (returns apps, entities, types)
2. Validate "my.bookshop.Addresses" exists in entities list
3. Match entity's `appTenantId` to find the parent app (e.g., "Bookshop")
4. Validate "String" exists in types list (isBase: true, isExtended: false)
5. Validate field name "testField1" matches pattern
6. Generate `asset.yaml` and `extension.yaml` using matched app name as `appNameSpace`
7. Execute `setup-solution` skill to update solution structure
8. Display success summary

**Generated `asset.yaml`:**
```yaml
apiVersion: asset.sap/v1
kind: Asset

metadata:
  name: bookshop-addresses-extension
  version: 1.0.0
  description: "Extends my.bookshop.Addresses entity with testField1"

type: domain-model-extension

sourceRoot: "."

provides:
  extensions:
    - descriptorFile: extension.yaml
      name: bookshop-addresses-extension
```

**Generated `extension.yaml`:**
```yaml
# Opaque content for domain model extension asset
appNameSpace: Bookshop
extensions:
  - extensionProject:
      name: BOOKSHOP-ADDRESSES-EXTENSION
      description: Extends my.bookshop.Addresses entity with testField1
      customAttributes: []
    types: []
    entities:
      - entityName: my.bookshop.Addresses
        displayName: Addresses
        isParametric: false
        fields:
          - fieldName: testField1
            isKey: false
            type:
              name: String
              isExtended: false
              isBase: true
            fieldLabel: Test Field1
    translations:
      - entityName: my.bookshop.Addresses
        locale: de
        translationKey: '{i18n>testField1}'
        translationValue: Testfeld 1
```

**Success Output:**
```
Domain Model Extension Created Successfully

Entity: my.bookshop.Addresses
Extension Name: bookshop-addresses-extension
App Namespace: Bookshop (auto-detected via appTenantId)

Fields Added:
- testField1 (String) - "Test Field1"

Translations:
- de: "Testfeld 1"

Files Created:
- assets/bookshop-addresses-extension/asset.yaml
- assets/bookshop-addresses-extension/extension.yaml

Solution Updated:
- solution.yaml now references ./assets/bookshop-addresses-extension/asset.yaml
```

## Relationship with Agent Extensions

Domain model extensions can be **optionally referenced** by agent extensions to indicate dependency. This is useful when:
- An agent extension needs the custom fields from a domain model extension
- You want to establish a clear dependency chain between assets

**Example: Referencing from agent-extension**

In an agent-extension's `asset.yaml`, you can add an **optional** `requires` section:

```yaml
# In assets/my-agent-extension/asset.yaml
requires:
  # OPTIONAL: domain-model-extension dependency
  - name: bookshop-addresses-extension      # Name of the domain model extension asset
    type: domain-model-extension            # Asset type
    version: 1.0.0                          # Version of the domain model extension
  
  # Other dependencies (agent, workflow, mcp, etc.)
  - name: my-agent
    type: agent
    version: 1.0.0
    ordId: sap.agent:aiagent:my-agent:v1
```

**When to add this reference:**
- Only if the user explicitly requests it
- When an agent extension genuinely depends on the domain model extension's fields
- To establish asset relationships for platform discoverability

## Notes

- All metadata API calls require authentication (JWT token passed via request headers)
- Extensions are additive only — they do not modify or remove existing fields
- The base CAP service must support entity extensibility for this to work
- Always gather context from metadata APIs before proceeding with extension creation
- The `type` block for each field must include `name`, `isBase`, and `isExtended` properties from the types metadata
- **The asset folder must contain ONLY `asset.yaml` and `extension.yaml` — no other files**
- The `asset.yaml` file references the `extension.yaml` file via the `descriptorFile` property
- The `extension.yaml` file contains the actual extension definition (entities, fields, translations)
- Domain model extensions are standalone assets — references from agent extensions are optional and only needed when there's an actual dependency
