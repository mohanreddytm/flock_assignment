from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class Meter(BaseModel):
    meter_id: str
    serial_number: str | None = None
    make: str | None = None
    phase: str | None = None
    status: str | None = None
    transformer_code: str | None = None


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    data: list[T]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    has_next: bool


class HierarchyLevel(BaseModel):
    level: str
    code: str | None
    name: str


class Location(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class MeterDetail(Meter):
    installation_type: str | None = None
    location: Location | None = None
    hierarchy: list[HierarchyLevel]
    nameplate: dict[str, str | None]
    warnings: list[str] = Field(default_factory=list)


class Reading(BaseModel):
    timestamp: datetime
    kwh: float | None
    kvah: float | None
    voltage_r: float | None
    interval_kwh: float | None = Field(
        description="Difference from the previous returned reading; null for the first reading, missing values, or a decreasing register."
    )


class Consumption(BaseModel):
    meter_id: str
    timezone: str = "Asia/Kolkata"
    register_interpretation: str = "cumulative_assumed"
    readings: list[Reading]
    count: int
    observed_usage_kwh: float | None = Field(
        description="Sum of valid consecutive register differences in this response, not a billing total. Null when no valid interval exists."
    )
    warnings: list[str]


class Transformer(BaseModel):
    code: str
    name: str | None
    feeder_code: str | None
    capacity_kva: float | None


class ExportMeter(Meter):
    installation_type: str | None
    location: Location | None
    hierarchy: list[HierarchyLevel]
    warnings: list[str]


class Export(BaseModel):
    data: list[ExportMeter]
    total: int
    fetched_at: datetime
    warnings: list[str]


class Health(BaseModel):
    status: str


class SessionStatus(BaseModel):
    authenticated: bool


class NetworkNode(BaseModel):
    id: str
    level: str
    code: str
    names: list[str]
    parent_ids: list[str]
    meter_ids: list[str]


class NetworkHierarchy(BaseModel):
    nodes: list[NetworkNode]
    unassigned_meter_ids: list[str]
    total_meters: int
    fetched_at: datetime
    warnings: list[str]
