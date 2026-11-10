"""Adapter base classes (spec section 2.1/3).

Adapters map each supplied JSON document into canonical records plus
evidence items. Unknown document types are preserved and reported as
unsupported — never silently discarded.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.schemas.canonical import (
    CanonicalDocument,
    ChecklistItem,
    ClaimRecord,
    DriverRecord,
    EntityRecord,
    EvidenceItem,
    ExposureRecord,
    ExternalObservation,
    LocationRecord,
    NarrativeRecord,
    QuoteTermRecord,
    RatingFactorRecord,
    SourceLocation,
    VehicleRecord,
    WCPayrollRow,
)
from app.schemas.envelope import InputEnvelope


class AdaptedDocument(BaseModel):
    document: CanonicalDocument
    # Compound documents (e.g. a combined ACORD 125/126/130/140 packet) emit
    # one AdaptedDocument per section here; the loader flattens them into
    # the case as sibling documents.
    extra_documents: List["AdaptedDocument"] = Field(default_factory=list)
    evidence: List[EvidenceItem] = Field(default_factory=list)
    entities: List[EntityRecord] = Field(default_factory=list)
    locations: List[LocationRecord] = Field(default_factory=list)
    vehicles: List[VehicleRecord] = Field(default_factory=list)
    drivers: List[DriverRecord] = Field(default_factory=list)
    claims: List[ClaimRecord] = Field(default_factory=list)
    wc_payroll_rows: List[WCPayrollRow] = Field(default_factory=list)
    rating_factors: List[RatingFactorRecord] = Field(default_factory=list)
    quote_terms: List[QuoteTermRecord] = Field(default_factory=list)
    exposures: List[ExposureRecord] = Field(default_factory=list)
    external_observations: List[ExternalObservation] = Field(default_factory=list)
    checklist_items: List[ChecklistItem] = Field(default_factory=list)
    narratives: List[NarrativeRecord] = Field(default_factory=list)


class EvidenceFactory:
    """Creates EvidenceItems with real, verifiable source locations.

    Provenance is attached only when the input envelope actually supplies
    it for the JSON path. Otherwise the source location is marked
    unavailable (available=False) rather than fabricated.
    """

    def __init__(self, envelope: InputEnvelope, document_id: str, document_name: Optional[str]):
        self._envelope = envelope
        self._document_id = document_id
        self._document_name = document_name
        self._seq = 0
        self.items: List[EvidenceItem] = []

    def make(
        self,
        json_path: str,
        canonical_field: str,
        raw_value: Any,
        normalized_value: Any = None,
        unit: Optional[str] = None,
        period: Optional[str] = None,
        snippet: Optional[str] = None,
        source_location_override: Optional[SourceLocation] = None,
    ) -> str:
        self._seq += 1
        evidence_id = f"EV-{self._document_id}-{self._seq:04d}"
        prov = self._envelope.provenance.fields.get(json_path)
        if source_location_override is not None:
            location = source_location_override
            confidence = prov.extraction_confidence if prov is not None else None
        elif prov is not None:
            location = SourceLocation(
                page=prov.source_page,
                sheet=prov.source_sheet,
                row=prov.source_row,
                column=prov.source_column,
                cell=prov.source_cell,
                record_id=None,
                json_path=json_path,
                available=True,
            )
            confidence = prov.extraction_confidence
        else:
            location = SourceLocation(json_path=json_path, available=False)
            confidence = None
        item = EvidenceItem(
            evidence_id=evidence_id,
            document_id=self._document_id,
            document_name=self._document_name,
            source_file=self._envelope.document.source_metadata.get("spectra_source_path"),
            field_path=canonical_field,
            source_location=location,
            raw_value=raw_value,
            normalized_value=normalized_value if normalized_value is not None else raw_value,
            unit=unit,
            period=period,
            snippet=snippet if snippet is not None else _short(raw_value),
            extraction_confidence=confidence,
        )
        self.items.append(item)
        return evidence_id


def _short(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def content_fingerprint(data: Dict[str, Any]) -> str:
    canonical = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_any(data: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Alias-tolerant lookup across common naming variants."""
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    lowered = {k.lower(): v for k, v in data.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value is not None:
            return value
    return default


def as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


class DocumentAdapter:
    """Base class for document-type adapters."""

    document_type: str = "unsupported"

    def adapt(self, envelope: InputEnvelope, document_id: str, generated_id: bool) -> AdaptedDocument:
        doc = envelope.document
        name = doc.document_name or envelope.provenance.source_file
        canonical_doc = CanonicalDocument(
            document_id=document_id,
            document_type=self.document_type,
            declared_document_type=doc.document_type,
            document_name=name,
            issuer=doc.issuer,
            version=doc.version,
            received_at=doc.received_at,
            effective_period=(
                doc.effective_period.model_dump() if doc.effective_period else None
            ),
            source_hash=doc.source_hash,
            content_hash=content_fingerprint(envelope.data),
            recognized=self.document_type != "unsupported",
            generated_id=generated_id,
            raw=envelope.data,
        )
        factory = EvidenceFactory(envelope, document_id, name)
        adapted = AdaptedDocument(document=canonical_doc)
        self.extract(envelope, factory, adapted)
        adapted.evidence = factory.items
        return adapted

    def extract(self, envelope: InputEnvelope, ev: EvidenceFactory, out: AdaptedDocument) -> None:
        raise NotImplementedError
