# Migration summary

_For full detail, read `state.json`._

- Agent: `assets/btp-guardian-agent`
- Last code run: 2026-06-17T13:57:07Z
- Last deploy run: 2026-06-17-161743
- Runtime mode: dual-mode
- Systems: btp_accounts (BTP_ACCOUNTS), btp_entitlements (BTP_ENTITLEMENTS), btp_resource_consumption (BTP_RESOURCE_CONSUMPTION), btp_metrics (BTP_METRICS), btp_usage_records (BTP_USAGE_RECORDS), btp_provisioning (BTP_PROVISIONING)
- AI Core destination: aicore
- LLM model: gpt-4o
- Deployment:
  - App: `btp-guardian-agent` in `SAP CoE NA / Development`
  - Route: https://btp-guardian-agent.cfapps.eu10.hana.ondemand.com
  - Memory: 512M
  - Services bound: proj-vector-destination-service (destination/lite)
  - Smoke verdict: running-destinations-not-configured
  - Last outcome: success
