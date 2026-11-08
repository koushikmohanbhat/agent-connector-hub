"""A mock policy-administration and claims API, standing in for a core insurance platform. It is the fake for the
insurance-core pack and also runs on its own (`uvicorn mock_api.app:app --port 8090` from the pack folder, or in
docker compose). Policyholders see only their own policies; users with the claims_handler role see all.

Accepts the hub's fake tokens only: it is a mock, not an authorisation server."""
from __future__ import annotations

import copy
import itertools
from datetime import date

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from connector_hub.fakes import read_fake_token

SEED = {
    "policies": {
        "POL-1001": {"policy_number": "POL-1001", "holder": "cust-001", "product": "Motor comprehensive",
                     "status": "active", "start": "2026-01-15", "end": "2027-01-14", "sum_insured": 650000,
                     "covers": ["collision", "theft", "fire", "flood"], "deductible": 2500, "vehicle": "KA-01-AB-1234"},
        "POL-1002": {"policy_number": "POL-1002", "holder": "cust-001", "product": "Home contents",
                     "status": "lapsed", "start": "2025-03-01", "end": "2026-02-28", "sum_insured": 400000,
                     "covers": ["fire", "theft", "flood"], "deductible": 5000},
        "POL-2001": {"policy_number": "POL-2001", "holder": "cust-002", "product": "Motor third party",
                     "status": "active", "start": "2026-06-01", "end": "2027-05-31", "sum_insured": 750000,
                     "covers": ["third_party"], "deductible": 0, "vehicle": "MH-12-CD-9876"},
    },
    "claims": {
        "CLM-50001": {"claim_id": "CLM-50001", "policy_number": "POL-1001", "loss_date": "2026-08-10",
                      "loss_type": "collision", "description": "Rear-ended at a signal", "estimated_amount": 42000,
                      "status": "settled", "documents": []},
    },
}


class NewClaim(BaseModel):
    policy_number: str
    loss_date: date
    loss_type: str
    description: str = Field(max_length=4000)
    estimated_amount: float = Field(gt=0)


class NewDocument(BaseModel):
    filename: str
    doc_type: str
    size_bytes: int
    sha256: str


def create_app() -> FastAPI:
    app = FastAPI(title="Mock core insurance API")
    state = {"data": copy.deepcopy(SEED), "ids": itertools.count(50002)}

    def reset():
        state["data"] = copy.deepcopy(SEED)
        state["ids"] = itertools.count(50002)
    app.state.reset = reset

    def who(request: Request) -> dict:
        c = read_fake_token(request.headers.get("authorization"))
        if c is None:
            raise HTTPException(401, "unauthorized")
        return c

    def policy_for(c: dict, number: str) -> dict:
        p = state["data"]["policies"].get(number)
        if p is None or ("claims_handler" not in c.get("roles", []) and p["holder"] != c["sub"]):
            raise HTTPException(404, "policy not found")
        return p

    @app.get("/v1/policies/{number}")
    def get_policy(number: str, request: Request):
        return policy_for(who(request), number)

    @app.get("/v1/policies/{number}/claims")
    def list_claims(number: str, request: Request):
        policy_for(who(request), number)
        return {"claims": [c for c in state["data"]["claims"].values() if c["policy_number"] == number]}

    @app.post("/v1/claims", status_code=201)
    def open_claim(body: NewClaim, request: Request):
        policy_for(who(request), body.policy_number)
        cid = f"CLM-{next(state['ids'])}"
        claim = {"claim_id": cid, **body.model_dump(mode="json"), "status": "registered", "documents": []}
        state["data"]["claims"][cid] = claim
        return claim

    @app.get("/v1/claims/{claim_id}")
    def get_claim(claim_id: str, request: Request):
        c = who(request)
        claim = state["data"]["claims"].get(claim_id)
        if claim is None:
            raise HTTPException(404, "claim not found")
        policy_for(c, claim["policy_number"])
        return claim

    @app.post("/v1/claims/{claim_id}/documents", status_code=201)
    def add_document(claim_id: str, body: NewDocument, request: Request):
        claim = get_claim(claim_id, request)
        doc = {"document_id": f"DOC-{len(claim['documents']) + 1:03d}", **body.model_dump()}
        claim["documents"].append(doc)
        return doc

    return app


app = create_app()
