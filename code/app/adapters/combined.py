"""Adapter for combined ACORD packets (``data.acord_125/126/130/140``).

Extractor JSON for these submissions nests each ACORD form under its own
section key, with per-field provenance keyed by ``data.<section>.<path>``.
This adapter remaps provenance to section-relative paths and emits one
canonical document per form — the per-form adapters read the nested
section keys directly — so document-type-scoped rules work unchanged. An ACORD 140 section with received/resubmitted (or similarly
labelled) versions becomes one document per version, enabling the U02
conflicting-versions check.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.adapters.acord import (
    Acord125Adapter,
    Acord126Adapter,
    Acord130Adapter,
    Acord140Adapter,
)
from app.adapters.base import AdaptedDocument, DocumentAdapter
from app.schemas.envelope import (
    DocumentMeta,
    FieldProvenance,
    InputEnvelope,
    Provenance,
)

_FORM_ADAPTERS = {
    "acord_125": Acord125Adapter,
    "acord_126": Acord126Adapter,
    "acord_130": Acord130Adapter,
    "acord_140": Acord140Adapter,
}

_FORM_KEY_RE = re.compile(r"^acord_(125|126|130|140)$", re.IGNORECASE)
_VERSION_SPLIT_RE = re.compile(
    r"^(received|resubmitted|revised|corrected|original|amended|v\d+)$", re.IGNORECASE)


def _section_source(section_ranges: Any, source_files: Any, form: str,
                    version: Optional[str]) -> Optional[Dict[str, Any]]:
    """Find the source metadata for this form version without guessing."""
    if isinstance(section_ranges, dict):
        keys = ([f"{form}_{version}".casefold(), form.casefold()]
                if version else [form.casefold()])
        ranges_by_key = {str(key).casefold(): value
                         for key, value in section_ranges.items()}
        for key in keys:
            value = ranges_by_key.get(key)
            if isinstance(value, dict) and value.get("source_file"):
                return value
    if isinstance(source_files, list):
        matches = [item for item in source_files
                   if isinstance(item, dict)
                   and str(item.get("document_section", "")).casefold() == form.casefold()
                   and (not version or str(item.get("version", "")).casefold()
                        == version.casefold())]
        if len(matches) == 1 and matches[0].get("source_file"):
            return matches[0]
    return None


def _section_source_from_documents(source_documents: Any, form: str
                                   ) -> Optional[Dict[str, Any]]:
    """Resolve physical PDFs listed as source_documents in extraction metadata.

    Some extractors describe one physical file with multiple form sections
    instead of using the more granular ``source_files`` representation.
    Attach that source to each contained form so citations still open the PDF.
    """
    if not isinstance(source_documents, list):
        return None
    form_label = form.replace("_", " ").casefold()
    matches = []
    for item in source_documents:
        if not isinstance(item, dict) or not item.get("source_file"):
            continue
        sections = item.get("form_sections", [])
        normalized = {str(section).replace("_", " ").casefold()
                      for section in sections} if isinstance(sections, list) else set()
        if form_label in normalized:
            matches.append(item)
    return matches[0] if len(matches) == 1 else None


def _physical_source_name(source_dir: Any, source_name: str) -> Optional[Tuple[str, str]]:
    """Return (workspace-relative path, actual sibling name) for a source."""
    workspace_root = Path(__file__).resolve().parents[3]
    folder = Path(str(source_dir)) if source_dir else None
    if folder is None:
        return None
    if not folder.is_absolute():
        folder = workspace_root / folder
    try:
        folder = folder.resolve()
        folder.relative_to(workspace_root.resolve())
    except (OSError, ValueError):
        return None
    leaf = Path(source_name).name
    exact = folder / leaf
    if exact.is_file() and exact.suffix.lower() in {".pdf", ".xlsx", ".xlsm", ".xls"}:
        path = exact.resolve()
    else:
        stem = re.sub(r"\(\d+\)$", "", Path(leaf).stem).casefold()
        suffix = Path(leaf).suffix.casefold()
        matches = [candidate.resolve() for candidate in folder.iterdir()
                   if candidate.is_file()
                   and candidate.suffix.casefold() == suffix
                   and re.sub(r"\(\d+\)$", "", candidate.stem).casefold() == stem]
        if len(matches) != 1:
            return None
        path = matches[0]
    return path.relative_to(workspace_root.resolve()).as_posix(), path.name


def is_combined_acord(data: Dict[str, Any]) -> bool:
    return any(_FORM_KEY_RE.match(str(k)) for k in data)


class AcordCombinedAdapter(DocumentAdapter):
    document_type = "acord_combined"

    def extract(self, envelope: InputEnvelope, ev: EvidenceFactory, out: AdaptedDocument) -> None:
        data = envelope.data
        base_id = out.document.document_id
        base_name = out.document.document_name or "ACORD combined"
        parent_meta = dict(envelope.document.source_metadata)
        source_dir = parent_meta.get("spectra_source_dir")
        section_ranges = parent_meta.get("section_page_ranges", {})
        source_files = parent_meta.get("source_files", [])
        source_documents = parent_meta.get("source_documents", [])
        for key, section in data.items():
            if not isinstance(section, dict):
                continue
            m = _FORM_KEY_RE.match(str(key))
            if not m:
                continue
            form = f"acord_{m.group(1)}"
            versions = _split_versions(section)
            for version_label, version_data in versions:
                section_source = _section_source(
                    section_ranges, source_files, form, version_label)
                if section_source is None:
                    section_source = _section_source_from_documents(
                        source_documents, form)
                sub_meta = dict(parent_meta)
                # A combined JSON may list several PDFs. Never let its parent
                # source (often the ACORD 125) leak into every nested form.
                sub_meta.pop("spectra_source_path", None)
                sub_name = f"{base_name} :: {form.upper()}"
                if section_source:
                    source_name = str(section_source["source_file"])
                    resolved = _physical_source_name(source_dir, source_name)
                    if resolved:
                        sub_meta["spectra_source_path"], actual_name = resolved
                        sub_name = f"{actual_name} :: {form.upper()}"
                    else:
                        sub_name = f"{Path(source_name).name} :: {form.upper()}"
                sub_id = f"{base_id}#{form}" + (f"#{version_label}" if version_label else "")
                sub_doc_meta = DocumentMeta(
                    document_id=sub_id,
                    document_type=form,
                    document_name=sub_name,
                    issuer=envelope.document.issuer,
                    version=version_label or envelope.document.version,
                    received_at=envelope.document.received_at,
                    effective_period=envelope.document.effective_period,
                    source_metadata=sub_meta,
                )
                sub_envelope = InputEnvelope(
                    schema_version=envelope.schema_version,
                    submission_id=envelope.submission_id,
                    document=sub_doc_meta,
                    data=dict(version_data),
                    provenance=_remap_provenance(
                        envelope.provenance, f"data.{key}" + (
                            f".{version_label}" if version_label else "")),
                )
                adapter = _FORM_ADAPTERS[form]()
                out.extra_documents.append(adapter.adapt(sub_envelope, sub_id, False))


def _split_versions(section: Dict[str, Any]) -> List[Tuple[Optional[str], Dict[str, Any]]]:
    """Split a form section into (version_label, data) pairs. A section made
    only of version-labelled sub-dicts (received/resubmitted/…) yields one
    pair per version; anything else yields a single unversioned pair."""
    if section and all(isinstance(v, dict) for v in section.values()):
        keys = [str(k) for k in section]
        if len(keys) > 1 and all(_VERSION_SPLIT_RE.match(k) for k in keys):
            return [(k, section[k]) for k in section]
    return [(None, section)]


def _remap_provenance(prov: Provenance, prefix: str) -> Provenance:
    """Strip the ``data.<section>[.<version>].`` prefix so sub-adapter
    evidence paths resolve against the section-relative JSON paths."""
    stripped: Dict[str, FieldProvenance] = {}
    for path, field_prov in prov.fields.items():
        if path.startswith(prefix + "."):
            stripped[path[len(prefix) + 1:]] = field_prov
    return Provenance(
        extraction_source=prov.extraction_source,
        source_file=prov.source_file,
        fields=stripped,
    )
