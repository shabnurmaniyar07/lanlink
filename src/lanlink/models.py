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


class CreateFolderRequest(BaseModel):
    path: str = ""
    name: str = Field(min_length=1, max_length=255)


class RenameRequest(BaseModel):
    path: str = Field(min_length=1)
    new_name: str = Field(min_length=1, max_length=255)


class RemoteTransferRequest(BaseModel):
    """Hub-mediated transfer between two paired remote nodes."""

    source_device_id: str
    source_share_id: str
    source_path: str = Field(min_length=1)
    destination_device_id: str
    destination_share_id: str
    destination_path: str = ""
    operation: Literal["copy", "move"] = "copy"
