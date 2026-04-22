# LLM Provider Evaluation Pack for Strict-PHI EHR Workloads

This workspace contains decision artifacts for selecting an LLM provider and deployment model for workloads that may handle protected health information (PHI) from electronic health records (EHR).

The evaluation is intentionally biased toward:

- A hyperscaler-hosted deployment as the default path
- A self-hosted or local-compute deployment as a high-control fallback
- Strict PHI handling requirements across operational, engineering, and research use cases
- A coding harness with no direct public internet access and package installation through an internal mirror only
- Custom MCP servers treated as governed integrations with the same PHI, network, and approval controls as any other tool or service boundary

## Included artifacts

- `provider_scorecard.csv`: Pass/fail and weighted comparison template for providers and deployment tracks
- `phi_data_flow_inventory.md`: PHI entry, transformation, storage, logging, and exit inventory
- `reference_architecture.md`: Reference architecture for hyperscaler-managed and self-hosted deployments
- `secure_coding_harness_design.md`: Implementation design for an isolated coding harness that cannot directly access PHI
- `decision_memo_template.md`: Decision document template to capture the final recommendation and rationale

## How to use this pack

1. Duplicate the scorecard rows per provider and deployment track you want to evaluate.
2. Mark any requirement with unclear PHI coverage as `FAIL` until legal, privacy, and security teams clear it.
3. Complete the PHI data-flow inventory for each candidate architecture before any proof of concept.
4. Use the decision memo to record which workloads are approved for hyperscaler-managed AI, which require self-hosting, and which are prohibited.

## Default assumptions

- PHI may appear in prompts, retrieved context, outputs, logs, and tool inputs.
- No provider passes unless both compliance/legal and technical/security gates pass.
- Hyperscaler-hosted AI is the default for most workloads if exact feature coverage under the BAA is confirmed.
- Self-hosted or local compute is the fallback for the most sensitive PHI workloads or where provider feature coverage is too narrow.
- Research workloads must be reviewed separately from operational workloads, even when both are allowed to process PHI.
