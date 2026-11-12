"""Input JSON envelope schema (spec section 2.1).

The envelope is the *preferred* shape. Adapters also accept raw JSON
documents without an envelope and map them into this shape, marking
synthesized metadata as generated (never fabricated provenance).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EffectivePeriod(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None


class DocumentMeta(BaseModel):
    model_config = ConfigDict(extra="allow")

    document_id: Optional[str] = None
    document_type: Optional[str] = None
    document_name: Optional[str] = None
    issuer: Optional[str] = None
    version: Optional[str] = None
    received_at: Optional[str] = None
    effective_period: Optional[EffectivePeriod] = None
    source_hash: Optional[str] = None
    source_metadata: Dict[str, Any] = Field(default_factory=dict)


class FieldProvenance(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_sheet: Optional[str] = None
    source_row: Optional[int] = None
    source_column: Optional[str] = None
    source_cell: Optional[str] = None
    source_page: Optional[int] = None
    source_line_start: Optional[int] = None
    source_line_end: Optional[int] = None
    source_span: Optional[str] = None
    extraction_confidence: Optional[float] = None

    @model_validator(mode="before")
    @classmethod
    def _map_extractor_aliases(cls, values: Any) -> Any:
        """Extractor JSON may use bare page/sheet/row/column/cell keys."""
        if not isinstance(values, dict):
            return values
        aliases = {
            "page": "source_page", "sheet": "source_sheet", "row": "source_row",
            "column": "source_column", "cell": "source_cell",
            "line_start": "source_line_start", "line_end": "source_line_end",
            "confidence": "extraction_confidence",
        }
        for alias, canonical in aliases.items():
            if alias in values and canonical not in values:
                values[canonical] = values[alias]
        return values


class Provenance(BaseModel):
    model_config = ConfigDict(extra="allow")

    extraction_source: Optional[str] = None
    source_file: Optional[str] = None
    fields: Dict[str, FieldProvenance] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _coerce_scalar_lists(cls, values: Any) -> Any:
        """Extractor JSON may list multiple source files; keep them all as a
        joined label rather than rejecting the document."""
        if not isinstance(values, dict):
            return values
        for key in ("source_file", "extraction_source"):
            if isinstance(values.get(key), list):
                values[key] = "; ".join(str(v) for v in values[key])
        return values


class InputEnvelope(BaseModel):
    """Validated form of one submitted JSON document."""

    model_config = ConfigDict(extra="allow")

    schema_version: str = Field(default="1.0")
    submission_id: Optional[str] = None
    document: DocumentMeta = Field(default_factory=DocumentMeta)
    data: Dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance = Field(default_factory=Provenance)
