from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PairRequest(BaseModel):
    client_id: str = Field(min_length=8, max_length=128)
    client_name: str = Field(min_length=1, max_length=80)
    pair_code: str = Field(min_length=6, max_length=20, pattern=r"^[0-9]+$")


class CopyMoveRequest(BaseModel):
    source_share_id: str
    source_path: str = ""
    destination_share_id: str
    destination_path: str = ""
    operation: Literal["copy", "move"] = "copy"
