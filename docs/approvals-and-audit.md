# Policies, approvals and audit

## Policies

```yaml
policies:
  - tool: open_claim
    require_approval_if: "args.estimated_amount > ${CLAIM_APPROVAL_LIMIT:-100000}"
    approver_role: claims_supervisor
    message: "Claims above the limit need a claims supervisor."
  - tool: create_return_request
    deny_if: "len(args.items) > 20"
  - tool: "*"
    rate_limit: {per_user: "30/min"}       # or per_tenant
```

Rules are small expressions over `args` and `user` (`id`, `tenant`, `roles`): comparisons, `and` / `or` / `not`,
`in`, arithmetic, `len`, `lower`, `upper`. Anything else is rejected when the hub loads. A missing argument makes a
comparison false. Pack policies run first, then the profile's. It is the same language as agent-starter-kit.

Rules that need the system's data (is the policy active? what is this return worth?) belong in the tool. A tool can
hold itself: `ctx.require_approval("Return value 64,000 is above 50,000", approver_role="sales_ops")`.

## Approvals

A held call returns `{"status": "pending_approval", "approval_id": "...", "message": "..."}` to the agent.

- `GET /v1/approvals` lists pending calls the caller may decide (their roles, their tenant).
- `POST /v1/approvals/{id}` with `{"approve": true, "reason": "..."}` decides it, once. You cannot approve your own
  request.
- On approval the hub runs the call again with the original user's identity and the approver as the actor (the
  `act` claim of a hub-signed assertion, and `acting_for` in the audit log). The tool's own checks run again.

## Audit

One row per call: time, tenant, profile, client, user, tool, pack, system, decision, approval id, status, latency,
tokenised arguments, error, trace id. `GET /v1/audit` (role `hub_auditor`), or query the `hub_audit` table. An
`on_audit` hook receives each row, for a SIEM. Erasure (`DELETE /v1/users/{id}`, role `privacy_admin`) removes linked
accounts and pending held calls; audit rows stay, with tokenised arguments.
