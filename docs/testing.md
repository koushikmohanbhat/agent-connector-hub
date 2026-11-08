# Testing: fakes, conformance, recording

**Fakes.** Every pack's settings default to `fake://` endpoints, served in-process by emulators that apply per-user
access like the real systems: Salesforce (a SOQL subset, sObject writes, sharing by account), ServiceNow (Table API,
encoded queries, caller-based access), the AWS for SAP MCP Server's tool shape (OData reads with `$filter`, customer
returns, sold-to scoping) and the insurance mock API. The pack's real backend code runs against them: REST over an
in-process ASGI transport, MCP through an in-process client. They are test doubles, not the products.

**Conformance** (`hub conformance [pack]`): descriptions, schemas, write marking, bad-input handling, the pack's
`conformance.yaml` calls (including other users' data being hidden), identity documentation for service accounts, and
**parity**: packs with several backends must return the same results for every read call.

**Recording** (`hub record <pack> --out file.jsonl --user <id>`): runs the pack's read calls against whatever the
environment points at (a vendor sandbox) and saves tokenised results to review before keeping them.

**Agents and evals.** Run the hub on fakes and point the agent at it; with the 4.1 GenAI Eval Framework, a tool-agent
suite can check final state in the fakes. `/v1/dev/reset` restores every fake's seed between runs (dev mode).

**Validation levels**, as in 4.1 and 3.1: implemented, fixture tested, live tested, customer validated. Each pack
records its level per backend in `pack.yaml`. Nothing is above fixture tested yet.
