# PHI Data-Flow Inventory Template

Use this inventory before any proof of concept or production rollout. Treat every field as required for each provider and deployment track.

## Instructions

- Duplicate this template for each candidate provider and deployment architecture.
- Mark any unknown PHI handling behavior as `UNRESOLVED`.
- Any unresolved item that could affect PHI storage, transit, retention, or access should block approval.

## System metadata

- Provider:
- Deployment track: `Hyperscaler-hosted` or `Self-hosted / local compute`
- Environment: `Research`, `Engineering`, `Operational`, or other approved environment
- Architecture owner:
- Privacy owner:
- Security owner:
- Legal review status:
- BAA or equivalent contract reference:

## PHI entry points

| Entry point | Source system | PHI allowed | Controls before entry | Notes |
| --- | --- | --- | --- | --- |
| Prompt payload | Data warehouse / application / user input | Yes | Minimum-necessary prompt construction, redaction where possible | |
| Retrieved context | Warehouse / vector index / document store | Yes | Row and column filtering, policy checks, audit logging | |
| Tool input | Coding harness / pipeline metadata / job payloads | Yes | Scope-limited service identity, command allowlisting | |
| MCP server request | Custom MCP server and its backing systems | Yes | Server allowlisting, scoped auth, request policy evaluation, audit logging | |
| File upload | Documents / extracts / research assets | Yes or No | Explicit approval, malware scanning, content checks | |

## PHI transformations

| Transformation | Where it happens | Output contains PHI | Required controls | Status |
| --- | --- | --- | --- | --- |
| Prompt assembly | Application boundary | Maybe | Tokenization or redaction where possible, deterministic logging policy | |
| Retrieval and ranking | Retrieval layer | Maybe | Query scoping, access control, request logging | |
| Model inference | Managed endpoint or self-hosted serving stack | Maybe | Covered service confirmation, network isolation, encryption | |
| MCP tool execution | Approved custom MCP server | Maybe | Per-server authorization, action allowlisting, egress controls, immutable traces | |
| Post-processing | Application or harness | Maybe | PHI-safe validation, output filtering, approval gates | |
| Research export | Analytics or publication workflow | Maybe | IRB or privacy review, export controls, lineage capture | |

## PHI storage and persistence

| Location | Stores PHI | Data type | Retention rule | Encryption | Access model | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Data warehouse | Yes | Source EHR and curated datasets | | | | |
| Retrieval index | Yes or No | Chunks embeddings metadata | | | | |
| Model provider logs | Unknown until verified | Request and response metadata | | | | |
| Application logs | Should be minimized | Operational metadata | | | | |
| MCP server logs | Should be minimized | Tool calls backing-system metadata | | | | |
| Research outputs | Yes or No | Analysis artifacts and reports | | | | |
| Coding harness state | Yes or No | Tool traces dependency logs code diffs | | | | |

## PHI exits and disclosures

| Exit path | Destination | PHI allowed | Approval required | Logging required | Status |
| --- | --- | --- | --- | --- | --- |
| Model output to user | Internal application or analyst | Yes | Role-based approval for sensitive workflows | Yes | |
| Export to research environment | Approved research system | Yes only if approved | Yes | Yes | |
| Export outside governed boundary | External partner or publication workflow | Normally no | Mandatory legal and privacy review | Yes | |
| MCP server action | Backing API database queue or file system | Yes only if approved | Yes for sensitive actions | Yes | |
| Support or vendor access | Vendor staff or subprocessors | Only if contractually covered and necessary | Yes | Yes | |

## Logging and observability checks

- Are raw prompts logged?
- Are raw completions logged?
- Are retrieved source records logged?
- Are command traces and tool calls logged?
- Are MCP server calls, responses, and backing-system accesses logged?
- Are logs redacted before reaching developer-facing systems?
- Can logs be exported to the SIEM?
- Are logs immutable or tamper-evident?
- Is retention aligned to policy?

## Approval conditions

- `APPROVED`: All PHI paths are documented and controlled.
- `CONDITIONALLY APPROVED`: Minor non-PHI gaps remain with compensating controls.
- `REJECTED`: Any unresolved PHI exposure, unclear feature coverage, or unbounded logging remains.

## Final disposition

- Status:
- Blocking issues:
- Compensating controls:
- Review date:
