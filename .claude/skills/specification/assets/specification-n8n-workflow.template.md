# Specification: {{asset-name}}

> **Guidelines**: Read [guidelines.md](../guidelines.md) and [guidelines-n8n-workflow.md](../guidelines-n8n-workflow.md) before executing ANY tasks below. Follow all constraints described there throughout execution.

## Basic Setup

- [ ] Read the project input (`product-requirements-document.md`, `intent.md`, or the user prompt that triggered this specification)

{{project-specific-tasks}}

- [ ] Write all workflow JSON files to `assets/n8n/workflows/` using the `n8n-workflow` skill
- [ ] Ensure `connections` in JSON reference nodes by `name`, not `id`
- [ ] Validate all workflow JSON files are well-formed
