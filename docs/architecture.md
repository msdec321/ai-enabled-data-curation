# AutoDQA — Architecture

```mermaid
flowchart TD
    subgraph inputs["Inputs"]
        DB[(CDW Database<br><i>SQL Server</i>)]
        ETL[ETL Codebase<br><i>/gitlab/cdw</i>]
        SPECS[PCORnet Specs<br><i>pcornet_fields<br>pcornet_valuesets<br>pcornet_constraints</i>]
        DOCS[Documentation<br><i>85+ markdown files</i>]
    end

    subgraph tools["MCP Tool Servers"]
        T_SQL[sql_executor<br><i>Read-only T-SQL</i>]
        T_ETL[etl_reader<br><i>Search & read SQL files</i>]
        T_SPEC[spec_query<br><i>Query PCORnet metadata</i>]
        T_DOC[doc_search<br><i>Search documentation</i>]
    end

    DB --- T_SQL
    ETL --- T_ETL
    SPECS --- T_SPEC
    DOCS --- T_DOC

    START([run.sh]) --> COORD

    subgraph pipeline["Coordinator Pipeline"]
        direction TB

        COORD{Coordinator Agent}

        subgraph phase0["Phase 0: Context Ingestion"]
            P0[Parse ETL structure<br>+ query PCORnet specs]
            P0_OUT1[/etl_index.json/]
            P0_OUT2[/expectations.json/]
            P0 --> P0_OUT1 & P0_OUT2
        end

        subgraph phase1["Phase 1: Profiling"]
            P1_AGENT[[Profiler Worker]]
            P1_OUT[/profile_DEMOGRAPHIC.json<br>profile_ENCOUNTER.json<br>profile_DIAGNOSIS.json/]
            P1_AGENT --> P1_OUT
        end

        subgraph phase2["Phase 2: Issue Detection"]
            P2_AGENT[[Analyst Worker]]
            P2_OUT[/issues.json/]
            P2_AGENT --> P2_OUT
        end

        subgraph phase3["Phase 3: Clustering"]
            P3[Coordinator clusters<br>issues by likely<br>shared root cause]
            P3_OUT[/clusters.json/]
            P3 --> P3_OUT
        end

        subgraph phase4["Phase 4: Root Cause Investigation"]
            P4_AGENT[[ETL Investigator<br><i>one per cluster</i>]]
            P4_OUT[/investigation_cluster_N.json/]
            P4_AGENT --> P4_OUT
        end

        subgraph phase5["Phase 5: Review"]
            P5_AGENT[[Reviewer<br><i>independent session</i>]]
            P5_OUT[/review.json/]
            P5_AGENT --> P5_OUT
            P5_VERDICT{Verdict}
            P5_OUT --> P5_VERDICT
        end

        subgraph phase6["Phase 6: Reporting"]
            P6_AGENT[[Report Writer]]
            P6_OUT[/dqa_report.md/]
            P6_AGENT --> P6_OUT
        end

        COORD --> phase0
        phase0 --> phase1
        phase1 --> phase2
        phase2 --> phase3
        phase3 --> phase4
        phase4 --> phase5
        P5_VERDICT -->|ACCEPT| phase6
        P5_VERDICT -->|REVISE| phase4
    end

    T_SQL -.-> P1_AGENT
    T_SQL -.-> P5_AGENT
    T_SQL -.-> P4_AGENT
    T_SPEC -.-> P0
    T_ETL -.-> P0
    T_ETL -.-> P4_AGENT
    T_ETL -.-> P5_AGENT
    T_DOC -.-> P4_AGENT

    style inputs fill:#e8f4f8,stroke:#2980b9
    style tools fill:#fef9e7,stroke:#f39c12
    style pipeline fill:#f9f9f9,stroke:#7f8c8d
    style phase0 fill:#eaf2e3,stroke:#27ae60
    style phase1 fill:#eaf2e3,stroke:#27ae60
    style phase2 fill:#eaf2e3,stroke:#27ae60
    style phase3 fill:#fdebd0,stroke:#e67e22
    style phase4 fill:#eaf2e3,stroke:#27ae60
    style phase5 fill:#f5eef8,stroke:#8e44ad
    style phase6 fill:#eaf2e3,stroke:#27ae60
    style COORD fill:#d4efdf,stroke:#1e8449,stroke-width:2px
    style P5_VERDICT fill:#fadbd8,stroke:#e74c3c
```

## Agent Legend

| Shape | Meaning |
|-------|---------|
| `[[ ]]` double-bordered | Sub-agent (independent Claude Code session via `claude -p`) |
| `{ }` diamond | Decision point (coordinator or reviewer verdict) |
| `/ /` parallelogram | Output file (JSON or markdown) |
| Dotted lines | Tool connections (which tools each agent uses) |

## Key Design Points

- **Clustering is the coordinator's job** (Phase 3, orange) — it groups related issues before spawning investigators, so each investigator traces a coherent set of symptoms to a single root cause.
- **Review is independent** (Phase 5, purple) — the reviewer runs in a fresh session with no access to prior agents' reasoning.
- **REVISE loops back** to investigation, not to the beginning — the reviewer can send specific clusters back for re-investigation while accepting the rest.
