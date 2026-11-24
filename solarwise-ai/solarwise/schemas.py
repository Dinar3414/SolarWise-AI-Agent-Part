"""Pydantic schemas: validated project input, equipment specs and design results (C1)."""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator

Status = Literal["ok", "warn", "fail", "incomplete"]


class ModuleSpec(BaseModel):
    id: str
    manufacturer: str
    model: str
    pmax_w: float = Field(gt=0, le=1000)
    voc_v: float = Field(gt=0, le=100)
    vmp_v: float = Field(gt=0, le=100)
    isc_a: float = Field(gt=0, le=40)
    imp_a: float = Field(gt=0, le=40)
    temp_coeff_voc_pct_per_c: float = Field(lt=0, ge=-1.0, description="%/°C, negative")
    temp_coeff_pmax_pct_per_c: Optional[float] = None
    length_mm: float = Field(gt=0)
    width_mm: float = Field(gt=0)
    weight_kg: Optional[float] = None
    max_system_voltage_v: float = 1500
    max_series_fuse_a: Optional[float] = None
    source_file: str
    source_page: int
    revision: str = ""
    verification: Literal["text-layer", "ocr-reviewed", "manual"] = "text-layer"
    is_example: bool = False


class InverterSpec(BaseModel):
    id: str
    manufacturer: str
    model: str
    rated_ac_kw: float = Field(gt=0)
    max_dc_voltage_v: float = Field(gt=0)
    mppt_min_v: float = Field(gt=0)
    mppt_max_v: float = Field(gt=0)
    mppt_count: int = Field(ge=1)
    max_input_current_per_mppt_a: float = Field(gt=0)
    max_isc_per_mppt_a: float = Field(gt=0)
    strings_per_mppt: Optional[int] = None
    startup_voltage_v: Optional[float] = None
    source_file: str
    source_page: int
    revision: str = ""
    is_example: bool = False


class ProjectInput(BaseModel):
    """Everything is explicit-unit. Missing values stay None -> 'incomplete', never guessed."""
    target_kw: Optional[float] = Field(default=None, gt=0, le=10_000, description="kW DC target")
    module_wp: Optional[float] = Field(default=None, gt=0, le=1000, description="W per module")
    module_id: Optional[str] = None
    inverter_id: Optional[str] = None
    inverter_ac_kw: Optional[float] = Field(default=None, gt=0, le=10_000)
    modules_per_string: Optional[int] = Field(default=None, ge=1, le=60)
    parallel_strings_per_mppt: Optional[int] = Field(default=None, ge=1, le=10)
    min_temp_c: Optional[float] = Field(default=None, ge=-60, le=30)
    max_temp_c: Optional[float] = Field(default=None, ge=0, le=70)
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    tilt_deg: Optional[float] = Field(default=None, ge=0, le=90)
    azimuth_deg: Optional[float] = Field(default=None, ge=-180, le=180, description="0=south (PVGIS)")
    system_loss_pct: float = Field(default=14.0, ge=0, le=60)

    @field_validator("module_wp")
    @classmethod
    def _wp_not_kwp(cls, v):
        if v is not None and v < 100:
            raise ValueError("module_wp looks like kW or kWp; enter watts per module (e.g. 615)")
        return v


class ToolResult(BaseModel):
    tool: str
    status: Status = "ok"
    values: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    inputs: dict = Field(default_factory=dict)

    def summary(self) -> str:
        bits = [f"{k}={v}" for k, v in self.values.items()]
        return f"[{self.tool}] {self.status.upper()} " + "; ".join(bits)


class Citation(BaseModel):
    manufacturer: str
    model: str
    revision: str = ""
    source_file: str
    page: int
    score: float = 0.0
    text: str = ""


class DesignResult(BaseModel):
    project: ProjectInput
    results: list[ToolResult] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    approval_status: Literal["preliminary", "pending_review", "approved"] = "preliminary"
    reviewer: Optional[str] = None
