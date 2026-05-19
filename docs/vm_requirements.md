# AutoDQA — VM Requirements

This document specifies the infrastructure requirements for two VMs: a **Linux application server** running the AutoDQA agent framework, and a **Windows database server** hosting the clinical data warehouse on SQL Server.

## VM 1: Linux Application Server (AutoDQA)

Runs the Claude Code multi-agent framework. Agents execute SQL queries against the remote database VM and read ETL source files locally.

### Operating System

- **Ubuntu Server 24.04 LTS** (x86_64)

### Compute

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| vCPUs | 4 | 8 |
| RAM | 8 GB | 16 GB |
| Storage | 50 GB SSD | 100 GB SSD |

**Rationale:**
- Claude Code runs multiple concurrent sub-agent processes (coordinator + up to 3 workers), each a separate Node.js process plus Python MCP server processes
- 16 GB RAM accommodates parallel agent sessions for a small team (2–5 users)
- Storage covers the OS, Python virtual environment, ETL codebase clone, and DQA run results (JSON profiles + reports)

### Software Requirements

| Software | Version | Purpose |
|----------|---------|---------|
| Node.js | 20 LTS+ | Claude Code CLI runtime |
| Python | 3.11+ | MCP tool servers (SQL executor, ETL reader, spec query, doc search) |
| Claude Code CLI | Latest | Agent orchestration (`npm install -g @anthropic-ai/claude-code`) |
| ODBC Driver 18 for SQL Server | Latest | Database connectivity from Python/pyodbc |
| Git | 2.x+ | Clone and manage ETL codebase |

### Python Packages (installed in virtual environment)

- `mcp[cli]` — MCP server framework
- `pyodbc` — SQL Server connectivity
- `pyyaml` — Configuration file parsing

### Outbound Network Access

| Destination | Port | Purpose |
|-------------|------|---------|
| `api.anthropic.com` | 443 (HTTPS) | Claude API calls from Claude Code |
| Database VM (VM 2) | 1433 (TCP) | SQL Server queries |

### User Accounts

Each user needs:
- A Linux user account with access to the project directory
- Their own `ANTHROPIC_API_KEY` (set in their shell profile or a shared key via environment config)
- A SQL Server login on VM 2 with `db_datareader` permissions on the CDW database

---

## VM 2: Windows Database Server (SQL Server)

Hosts the clinical data warehouse. The AutoDQA agents connect remotely from the Linux VM to execute read-only queries.

### Operating System

- **Windows Server 2022** (or 2019)

### Compute

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| vCPUs | 4 | 8 |
| RAM | 32 GB | 64 GB |
| Storage (OS) | 100 GB SSD | 100 GB SSD |
| Storage (Data) | 500 GB SSD | 1 TB SSD |

**Rationale:**
- SQL Server memory is proportional to database size; 64 GB RAM provides adequate buffer pool for a medium CDW (50–500 GB)
- SSD storage recommended for data files to support profiling queries that scan large tables
- Separate OS and data volumes recommended

### Software Requirements

| Software | Version | Purpose |
|----------|---------|---------|
| SQL Server | 2019 or 2022 (Standard or Enterprise) | CDW host |
| SQL Server Management Studio (SSMS) | Latest | Database administration and manual queries |

### SQL Server Configuration

- **Authentication mode:** SQL Server and Windows Authentication (mixed mode)
  - AutoDQA connects from Linux using SQL Authentication (username/password)
  - Windows Authentication can still be used for local SSMS administration
- **TCP/IP protocol:** Enabled (required for remote connections from the Linux VM)
  - Configure via SQL Server Configuration Manager > SQL Server Network Configuration > Protocols
- **Listening port:** 1433 (default)
- **Service account for AutoDQA:** Create a SQL login with `db_datareader` role on the CDW database — the tool only runs SELECT queries

### Database Requirements

- PCORnet CDM database with the following metadata tables for spec queries:
  - `pcornet_fields` — column definitions
  - `pcornet_valuesets` — valid value sets
  - `pcornet_constraints` — primary/foreign key relationships

---

## Summary

| | Linux VM (App Server) | Windows VM (Database) |
|---|---|---|
| **OS** | Ubuntu Server 24.04 LTS | Windows Server 2022 |
| **vCPUs** | 4–8 | 4–8 |
| **RAM** | 8–16 GB | 32–64 GB |
| **Storage** | 50–100 GB SSD | 600 GB–1.1 TB SSD |
| **Key software** | Claude Code, Python 3.11+, ODBC Driver 18 | SQL Server 2019/2022, SSMS |
| **Connectivity** | Outbound to api.anthropic.com:443, DB VM:1433 | Inbound on TCP 1433 from Linux VM |
