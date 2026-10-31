"""Typed contracts. TOOL_MANIFEST lists investigation tools only.

Human/API approve, apply and synthetic reset routes exist in main.py but are
intentionally absent from this manifest so model output cannot invoke them.
"""
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProposeRequest(Strict):
    incident_id: str = Field(min_length=1, max_length=64)
    product_id: str = Field(min_length=1, max_length=64)
    evidence_event_ids: List[int] = Field(default_factory=list, min_length=0, max_length=64)


class ApproveRequest(Strict):
    snapshot_hash: str = Field(min_length=8, max_length=128)


class ApplyRequest(Strict):
    proposal_id: str = Field(min_length=1, max_length=64)
    approval_id: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=4, max_length=128)


class VerifyRequest(Strict):
    product_id: str = Field(min_length=1, max_length=64)
    proposal_id: Optional[str] = Field(default=None, max_length=64)


TOOL_MANIFEST = [
    {"name": "inspect_incident", "method": "GET", "path": "/incidents/{id}", "read_only": True},
    {"name": "inspect_evidence", "method": "GET", "path": "/incidents/{id}/evidence", "read_only": True},
    {"name": "inspect_patch", "method": "GET", "path": "/incidents/{id}/patch", "read_only": True},
    {"name": "inspect_product", "method": "GET", "path": "/products/{id}", "read_only": True},
    {"name": "propose", "method": "POST", "path": "/proposals", "read_only": False, "note": "creates a draft proposal only; cannot approve or apply"},
    {"name": "preview", "method": "GET", "path": "/proposals/{id}/preview", "read_only": True},
    {"name": "verify", "method": "POST", "path": "/verify", "read_only": True},
]
