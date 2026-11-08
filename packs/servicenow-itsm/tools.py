"""ServiceNow ITSM tools for an employee helpdesk agent."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from connector_hub.sdk import ToolContext, tool

from .backend import INCIDENT_FIELDS, REQUEST_FIELDS

STATES = {"1": "New", "2": "In Progress", "3": "On Hold", "6": "Resolved", "7": "Closed", "8": "Canceled"}
Incident = Annotated[str, Field(pattern=r"^INC\d{7}$", description="Incident number, e.g. INC0010001")]
Level = Literal[1, 2, 3]


def _incident(r: dict) -> dict:
    return {"number": r.get("number"), "short_description": r.get("short_description"),
            "state": STATES.get(str(r.get("state")), r.get("state")), "priority": r.get("priority"),
            "assignment_group": r.get("assignment_group"), "opened_at": r.get("opened_at"),
            **({"work_notes": r["work_notes"]} if r.get("work_notes") else {})}


@tool
async def get_incident(ctx: ToolContext, number: Incident) -> dict:
    """Get one of the user's incidents: description, state, priority, assignment group and work notes."""
    return _incident(await ctx.backend.one(ctx, "incident", number, INCIDENT_FIELDS))


@tool
async def list_my_incidents(ctx: ToolContext, open_only: bool = True,
                            limit: Annotated[int, Field(ge=1, le=20)] = 10) -> dict:
    """List the user's incidents, newest first. By default only the ones still open."""
    return {"incidents": [_incident(r) for r in await ctx.backend.my_incidents(ctx, open_only, limit)]}


@tool(writes=True)
async def create_incident(ctx: ToolContext, short_description: Annotated[str, Field(min_length=5, max_length=160)],
                          description: Annotated[str, Field(max_length=4000)] = "", urgency: Level = 3,
                          impact: Level = 3) -> dict:
    """Raise an incident for the user. Urgency and impact are 1 (high) to 3 (low); 1 + 1 is a P1 and needs a lead."""
    r = await ctx.backend.new_incident(ctx, {"short_description": short_description, "description": description,
                                             "urgency": str(urgency), "impact": str(impact)})
    return {"number": r.get("number"), "priority": r.get("priority"), "state": STATES.get(str(r.get("state")), r.get("state"))}


@tool(writes=True)
async def add_work_note(ctx: ToolContext, number: Incident, note: Annotated[str, Field(min_length=1, max_length=4000)]) -> dict:
    """Add a work note to one of the user's incidents (visible to IT, not a customer-facing comment)."""
    r = await ctx.backend.note(ctx, number, note)
    return {"number": r.get("number", number), "added": True}


@tool
async def get_request_status(ctx: ToolContext, number: Annotated[str, Field(pattern=r"^REQ\d{7}$")]) -> dict:
    """Get the status of one of the user's service catalog requests (e.g. REQ0020001)."""
    r = await ctx.backend.one(ctx, "sc_request", number, REQUEST_FIELDS)
    return {k: r.get(k) for k in ("number", "short_description", "request_state", "approval", "stage", "opened_at")}
