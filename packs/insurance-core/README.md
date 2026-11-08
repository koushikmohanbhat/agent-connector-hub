# insurance-core

Claims-intake tools for a core insurance platform with no MCP server: `get_policy`, `list_claims`, `open_claim`,
`upload_claim_document`, `get_claim_status`. Use it as the template for any system without an MCP server
(`hub new pack <name>` creates a smaller skeleton).

## Business rules in the tools

- A claim can only be opened on an active policy, for a loss inside the cover period and not in the future, for a
  covered peril, with an estimate no higher than the sum insured.
- Documents: PDF, JPG or PNG, up to `INSURANCE_MAX_DOCUMENT_MB` (default 2), of an allowed type.
- Claims estimated above `CLAIM_APPROVAL_LIMIT` (default 100,000) are held for `claims_supervisor`.

## Connecting your platform

Set `INSURANCE_CORE_URL` and replace the paths in `backend.py` with your platform's API. Keep the method names, and
the tools and conformance cases keep working. `mock_api/app.py` is the fake (policyholders see only their own
policies; `claims_handler` sees all). It also runs on its own: `uvicorn mock_api.app:app --port 8090`.

Validation: fixture tested against the mock API.
