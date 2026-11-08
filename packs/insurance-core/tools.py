"""Claims-intake tools. The business rules a model must not be trusted with live here: the policy must be active,
the loss must fall inside the cover period and be a covered peril, the estimate cannot exceed the sum insured.
Large claims are held for a claims supervisor by policies.yaml."""
from __future__ import annotations

import base64
import binascii
import hashlib
from datetime import date
from typing import Annotated

from pydantic import Field

from connector_hub.sdk import ToolContext, ToolError, tool

PolicyNo = Annotated[str, Field(pattern=r"^POL-\d{4,8}$", description="Policy number, e.g. POL-1001")]
ClaimId = Annotated[str, Field(pattern=r"^CLM-\d{4,8}$", description="Claim id, e.g. CLM-50001")]
ALLOWED_FILES = (".pdf", ".jpg", ".jpeg", ".png")


def _policy_view(p: dict) -> dict:
    return {k: p.get(k) for k in ("policy_number", "product", "status", "start", "end", "sum_insured", "covers",
                                  "deductible", "vehicle") if p.get(k) is not None}


@tool
async def get_policy(ctx: ToolContext, policy_number: PolicyNo) -> dict:
    """Get a policy: product, status, cover period, sum insured, covered perils and deductible."""
    return _policy_view(await ctx.backend.policy(ctx, policy_number))


@tool
async def list_claims(ctx: ToolContext, policy_number: PolicyNo) -> dict:
    """List the claims on a policy with their status."""
    rows = await ctx.backend.claims(ctx, policy_number)
    return {"policy_number": policy_number, "claims": [
        {k: c.get(k) for k in ("claim_id", "loss_date", "loss_type", "estimated_amount", "status")} for c in rows]}


@tool(writes=True)
async def open_claim(ctx: ToolContext, policy_number: PolicyNo, loss_date: date,
                     loss_type: Annotated[str, Field(pattern=r"^[a-z_]{3,30}$", description="e.g. collision, theft")],
                     description: Annotated[str, Field(min_length=10, max_length=4000)],
                     estimated_amount: Annotated[float, Field(gt=0, le=100_000_000)]) -> dict:
    """Register a first notice of loss on an active policy. Check the policy with get_policy first."""
    p = await ctx.backend.policy(ctx, policy_number)
    if p["status"] != "active":
        raise ToolError(f"policy {policy_number} is {p['status']}; a claim cannot be opened")
    if loss_date > date.today():
        raise ToolError("the loss date is in the future")
    if not (date.fromisoformat(p["start"]) <= loss_date <= date.fromisoformat(p["end"])):
        raise ToolError(f"the loss date is outside the cover period ({p['start']} to {p['end']})")
    if loss_type not in p.get("covers", []):
        raise ToolError(f"{loss_type} is not covered by this policy (covers: {', '.join(p.get('covers', []))})")
    if estimated_amount > float(p["sum_insured"]):
        raise ToolError(f"the estimate is above the sum insured ({p['sum_insured']})")
    c = await ctx.backend.open_claim(ctx, {"policy_number": policy_number, "loss_date": loss_date.isoformat(),
                                           "loss_type": loss_type, "description": description,
                                           "estimated_amount": estimated_amount})
    return {"claim_id": c["claim_id"], "status": c["status"], "policy_number": policy_number,
            "next_step": "upload photos, invoices or a police report with upload_claim_document"}


@tool(writes=True)
async def upload_claim_document(ctx: ToolContext, claim_id: ClaimId,
                                filename: Annotated[str, Field(min_length=3, max_length=120)],
                                doc_type: Annotated[str, Field(max_length=30)],
                                content_base64: Annotated[str, Field(min_length=4)]) -> dict:
    """Attach a document (PDF, JPG or PNG, base64-encoded) to a claim: photo, invoice, police_report, estimate."""
    if not filename.lower().endswith(ALLOWED_FILES):
        raise ToolError("only PDF, JPG and PNG files are accepted")
    try:
        raw = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise ToolError("content_base64 is not valid base64") from None
    limit = float(ctx.settings.get("max_document_mb") or 2) * 1024 * 1024
    if len(raw) > limit:
        raise ToolError(f"the file is larger than {limit / 1024 / 1024:g} MB")
    await ctx.backend.claim(ctx, claim_id)
    doc = await ctx.backend.add_document(ctx, claim_id, {"filename": filename, "doc_type": doc_type.lower(),
                                                         "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    return {"claim_id": claim_id, "document_id": doc["document_id"], "size_bytes": len(raw)}


@tool
async def get_claim_status(ctx: ToolContext, claim_id: ClaimId) -> dict:
    """Get a claim's status, loss details and attached documents."""
    c = await ctx.backend.claim(ctx, claim_id)
    return {"claim_id": c["claim_id"], "policy_number": c["policy_number"], "status": c["status"],
            "loss_date": c["loss_date"], "loss_type": c["loss_type"], "estimated_amount": c["estimated_amount"],
            "documents": [{"document_id": d["document_id"], "filename": d["filename"], "doc_type": d["doc_type"]}
                          for d in c.get("documents", [])]}
