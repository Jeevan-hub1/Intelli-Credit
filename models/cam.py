"""Credit Appraisal Memo (CAM) domain models (Requirements 15, 16, 29, 30)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import Field

from models.base import IntelliBaseModel, Recommendation, utcnow


class CAMSection(IntelliBaseModel):
    """A single CAM section with explainability and source citations."""

    key: str
    title: str
    body: str = ""
    data_points: dict = Field(default_factory=dict)
    citations: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class GraphVisualization(IntelliBaseModel):
    """Circular-trading graph visualization spec (Requirement 29)."""

    nodes: list[dict] = Field(default_factory=list, description="{id, gstin, label, type}")
    edges: list[dict] = Field(default_factory=list, description="{from, to, amount, date}")
    cycle_path: list[str] = Field(default_factory=list)
    cycle_summary: dict = Field(default_factory=dict)
    image_key: Optional[str] = Field(
        default=None, description="Storage key of rendered image >=300 DPI"
    )
    legend: dict = Field(default_factory=dict)


class CAMVersionDelta(IntelliBaseModel):
    """Delta between two CAM versions (Requirement 30.3)."""

    field: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None


class CAM(IntelliBaseModel):
    """A Credit Appraisal Memo document."""

    id: str
    application_id: str
    version: int = 1
    recommendation: Recommendation = Recommendation.REJECT
    overall_score: float = 0.0
    risk_band: str = ""
    sections: list[CAMSection] = Field(default_factory=list)
    graph_visualization: Optional[GraphVisualization] = None
    data_completeness: float = 0.0
    low_confidence: bool = False
    model_version: Optional[str] = None
    generated_at: datetime = Field(default_factory=utcnow)
    generated_by: Optional[str] = None
    is_final: bool = False
    pdf_key: Optional[str] = None

    # Version history metadata (Requirement 30)
    modification_reason: Optional[str] = None
    deltas_from_previous: list[CAMVersionDelta] = Field(default_factory=list)

    def section(self, key: str) -> Optional[CAMSection]:
        for s in self.sections:
            if s.key == key:
                return s
        return None
