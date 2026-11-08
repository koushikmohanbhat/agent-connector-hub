"""Salesforce Service Cloud tools for a support agent. Five business actions, not a SOQL console."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from connector_hub.sdk import ToolContext, ToolError, tool

CaseNumber = Annotated[str, Field(pattern=r"^\d{8}$", description="Eight-digit case number, e.g. 00001001")]


@tool
async def find_account(ctx: ToolContext, name: Annotated[str, Field(min_length=2, max_length=80)]) -> dict:
    """Find customer accounts by (part of) their name. Returns up to 10 matches the user can see."""
    return {"accounts": await ctx.backend.find_accounts(ctx, name)}


@tool
async def get_case(ctx: ToolContext, case_number: CaseNumber) -> dict:
    """Get a support case: subject, description, status, priority and its comments."""
    c = await ctx.backend.case(ctx, case_number)
    return {"case_number": c["CaseNumber"], "account_id": c["AccountId"], "subject": c["Subject"],
            "description": c.get("Description"), "status": c["Status"], "priority": c["Priority"],
            "comments": await ctx.backend.comments(ctx, c["Id"])}


@tool(writes=True)
async def create_case(ctx: ToolContext, account_id: Annotated[str, Field(pattern=r"^001[A-Za-z0-9]{12,15}$")],
                      subject: Annotated[str, Field(min_length=3, max_length=255)],
                      description: Annotated[str, Field(max_length=4000)],
                      priority: Literal["Low", "Medium", "High"] = "Medium") -> dict:
    """Open a support case on a customer account. Find the account first with find_account."""
    c = await ctx.backend.create_case(ctx, account_id, subject, description, priority)
    return {"case_number": c["CaseNumber"], "status": c["Status"], "priority": c["Priority"]}


@tool(writes=True)
async def add_case_comment(ctx: ToolContext, case_number: CaseNumber,
                           comment: Annotated[str, Field(min_length=1, max_length=4000)], public: bool = False) -> dict:
    """Add a comment to a case. Public comments are visible to the customer; internal ones are not."""
    c = await ctx.backend.case(ctx, case_number)
    await ctx.backend.add_comment(ctx, c["Id"], comment, public)
    return {"case_number": case_number, "added": True, "public": public}


@tool(writes=True)
async def update_case_status(ctx: ToolContext, case_number: CaseNumber,
                             status: Literal["Working", "Escalated", "On Hold", "Closed"]) -> dict:
    """Change a case's status. Closing a case is held for a support lead's approval."""
    c = await ctx.backend.case(ctx, case_number)
    if c["Status"] == "Closed":
        raise ToolError(f"case {case_number} is already closed")
    await ctx.backend.set_status(ctx, c["Id"], status)
    return {"case_number": case_number, "status": status, "previous_status": c["Status"]}
