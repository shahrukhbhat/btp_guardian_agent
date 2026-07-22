# Specification: {{asset-name}}

> **Guidelines**: Read [guidelines.md](../guidelines.md) and [guidelines-cap.md](../guidelines-cap.md) before executing ANY tasks below. Follow all constraints described there throughout execution.

## Basic Setup

- [ ] Read the project input (`product-requirements-document.md`, `intent.md`, or the user prompt that triggered this specification)
- [ ] Invoke the `cap-development` skill from `assets/{{asset-name}}/` to set up the CAP project structure
- [ ] Install dependencies (`npm install`), validate the project starts (`cds watch`) and responds

{{project-specific-tasks}}

- [ ] Implement all backend functionality from PRD also in the UI (frontend in `assets/{{asset-name}}/ui/`)
- [ ] Run `cds compile srv/` to validate CDS models compile without errors
- [ ] Write tests for custom handler logic following `cap-development` skill testing guidelines — never test generic CRUD
- [ ] Run `cds watch` and curl endpoints to confirm service starts and responds correctly
