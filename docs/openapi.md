# Packs from OpenAPI

```bash
hub generate openapi warranty.yaml --out packs/warranty-api --system warranty \
    --ops getWarranty,listWarrantyClaims,createWarrantyClaim
hub conformance warranty-api
```

Each selected operation becomes a tool: path, query and header parameters become arguments, a JSON request body
becomes `body`, and the description comes from the operation. `$ref`s inside the spec are resolved (bundle external
ones first). OpenAPI 3.x only.

- **Writes are refused** until listed in `openapi.allow_writes`, and the generated `policies.yaml` holds every write
  for approval until you change it.
- **Fake**: with the default `fake://` base URL, each operation answers with its documented example response, so the
  tools run offline. Calls in `conformance.yaml` are generated from the spec's parameter and body examples.
- **Auth**: set `<SYSTEM>_IDENTITY` and the token settings; `auth.header` / `auth.scheme` change how the credential
  is sent (e.g. `header: X-API-Key`, `scheme: ""`).

Generated tools are only as good as the spec's descriptions; edit `pack.yaml` (`openapi.rename`) or write a Python
pack when an operation needs business rules.
