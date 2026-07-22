---
name: deploy-solution
description: Deploys the solution. Requires solution.yaml per skill `setup-solution`.
metadata:
  version: 1.0.0
  author: sap-joule-studio
---

Deploys the solution using either the `deploy_solution` tool or the `joule-studio-cli` CLI. Current working directory must be the solution root (containing `solution.yaml`).

**CRITICAL:** Follow this deployment order:
1. Use the tools from **Method 1**: `deploy_solution`, `get_deployment_job`, `get_deployment_job_logs`.
2. If these tools are unavailable, invoke the skill from **Method 2**.
3. If neither the tools nor the CLI is available, inform the user that deployment cannot proceed and explain why.

**CRITICAL:** If deployment fails:
- Return an error message that clearly states the failure and its cause.
- Only attempt minor configuration file tweaks when appropriate.
- Ask for user permission before making any changes.
- Never delete configuration files or assets.
- If the issue cannot be resolved with minor tweaks, report that manual intervention is required.

# Method 1: Deploy Solution using tools ONLY

Deploy the solution using the `deploy_solution` tool, if it is available, DO NOT try anything else to deploy. 

## Method 1: Check Deployment Status

Check the deployment status using the `get_deployment_job` tool, if it is available. DO NOT try anything else to check the deployment status.

## Method 1: Deployment Result
Always store the result in `deploy_result.json` in the following format:

```json
{ "solution_id": "<some_uuid>" }
```

## Method 1: Debugging

If the deployment fails, fetch the job logs using the `get_deployment_job_logs` tool, if it is available. DO NOT try anything else to fetch the job logs.

# Method 2: Deploy Solution using CLI (ONLY if tools are not available)
Invoke the `joule-studio-cli` skill to deploy the solution via the CLI.

