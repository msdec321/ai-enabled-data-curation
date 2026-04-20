# Decision Memo Template

## Decision summary

- Decision date:
- Decision owner:
- Recommended provider:
- Recommended deployment track: `Hyperscaler-hosted` or `Self-hosted / local compute`
- Final status: `Approved`, `Conditionally approved`, or `Rejected`

## Workload scope

- Operational EHR workflows in scope:
- Research workflows in scope:
- Coding harness workflows in scope:
- Explicitly prohibited workflows:

## Recommendation

State which provider and deployment track are recommended and why. Make the default recommendation concise and specific.

## Why this option passes

- BAA or equivalent contractual coverage status:
- Exact covered AI features:
- IAM and network controls:
- Logging retention and SIEM integration:
- Coding harness package-control support:
- Custom MCP server control model:
- Research governance fit:
- Operational supportability:

## Why alternatives did not pass

| Alternative | Reason it failed or was conditionally approved | Blocking issue owner |
| --- | --- | --- |
| Hyperscaler candidate 1 | | |
| Hyperscaler candidate 2 | | |
| Self-hosted candidate | | |

## Deployment guidance by workload

| Workload | Approved path | Conditions | Notes |
| --- | --- | --- | --- |
| Operational PHI pipeline improvement | | | |
| Research using PHI | | | |
| Research using de-identified data | | | |
| Coding harness dependency installation | Internal mirror only | Mandatory | |
| Custom MCP server access | Approved servers only | Mandatory | Per-server allowlist scoped auth immutable logging |
| Production warehouse writes | Approval-gated only | Mandatory | |

## Required controls before go-live

- Complete PHI data-flow inventory
- Confirm covered features under contract
- Validate private networking and IAM controls
- Validate outbound network restrictions and package mirror behavior
- Validate custom MCP server allowlists auth scopes and audit logging
- Validate PHI-safe logging and retention settings
- Complete red-team and failure-mode testing
- Complete legal privacy and security sign-off

## Residual risks and compensating controls

| Risk | Impact | Compensating control | Owner |
| --- | --- | --- | --- |
| | | | |

## Final approvals

- Legal:
- Privacy:
- Security:
- Platform engineering:
- Research governance:
