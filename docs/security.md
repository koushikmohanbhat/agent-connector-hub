# Security

- **Identity from the caller, never the model.** Tools receive the verified end user in `ctx`; arguments cannot
  change who the call is for. Downstream credentials are per user where the system supports it.
- **Narrow tools.** Fixed services and entity sets, escaped query values, pattern-checked identifiers, size limits;
  no free-form SOQL, OData or SQL from the model. Generated OpenAPI writes are refused until allowed.
- **Deny by default at the edges.** Unknown tools and arguments are refused; read-only profiles refuse writes; a
  service-account pack without a per-user filter fails conformance; the SAP pack fails closed without a customer claim.
- **Approvals** are role- and tenant-checked, decided once, never self-approved, and re-run the tool's own checks.
- **Secrets** only through the environment or a secret manager (`*_env` names); inline secrets in config are
  rejected. Tokens are cached in memory, never logged, never shown to the model; refresh tokens are encrypted.
- **Prod guard.** `HUB_ENV=prod` refuses fake endpoints and fake identities, and `/readyz` fails while any remain.
- **Audit** with tokenised arguments; OpenTelemetry spans never carry arguments or results.
- **Your code** (packs, hooks) runs with the hub's permissions: review it like application code.
- **Supply chain**: CodeQL, Dependabot, pip-audit in CI.

Report vulnerabilities as described in [SECURITY.md](../SECURITY.md).
