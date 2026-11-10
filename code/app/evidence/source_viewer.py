"""Resolve review evidence to original PDF/XLSX sources for citation previews."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.schemas.findings import EvidenceRef
from app.schemas.review import ReviewResult

_SOURCE_SUFFIXES = {".pdf", ".xlsx", ".xlsm", ".xls", ".txt", ".md", ".eml"}


def _safe_child(root: Path, relative: str) -> Optional[Path]:
    try:
        path = (root / relative).resolve()
        path.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return path if path.is_file() and path.suffix.lower() in _SOURCE_SUFFIXES else None


def _metadata(data: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    document = data.get("document") if isinstance(data.get("document"), dict) else {}
    provenance = data.get("provenance") if isinstance(data.get("provenance"), dict) else {}
    body = data.get("data") if isinstance(data.get("data"), dict) else data
    return document, provenance, body


def _source_names(document: Dict[str, Any], provenance: Dict[str, Any]) -> list[str]:
    result: list[str] = []
    for value in (document.get("document_name"), provenance.get("source_file")):
        if isinstance(value, str) and value.strip():
            result.append(value.strip().split(" :: ", 1)[0])
        elif isinstance(value, list):
            result.extend(str(item).split(" :: ", 1)[0] for item in value if item)
    meta = document.get("source_metadata") or {}
    for value in meta.get("source_files", []) if isinstance(meta, dict) else []:
        if isinstance(value, dict) and value.get("source_file"):
            result.append(str(value["source_file"]))
    return result


def _match_sibling(folder: Path, name: str) -> Optional[Path]:
    leaf = Path(name).name
    exact = folder / leaf
    if exact.is_file() and exact.suffix.lower() in _SOURCE_SUFFIXES:
        return exact.resolve()
    target_stem = re.sub(r"\(\d+\)$", "", Path(leaf).stem).casefold()
    target_suffix = Path(leaf).suffix.casefold()
    matches = []
    for candidate in folder.iterdir():
        if not candidate.is_file() or candidate.suffix.lower() not in _SOURCE_SUFFIXES:
            continue
        stem = re.sub(r"\(\d+\)$", "", candidate.stem).casefold()
        if stem == target_stem and (not target_suffix or candidate.suffix.casefold() == target_suffix):
            matches.append(candidate.resolve())
    return matches[0] if len(matches) == 1 else None


def _legacy_account_source(evidence: EvidenceRef, account_dir: Path) -> Optional[Path]:
    # First use the source PDF/XLSX name already present on extracted evidence.
    if evidence.document_name:
        direct = _match_sibling(account_dir, evidence.document_name.split(" :: ", 1)[0])
        if direct:
            return direct
    sheet = evidence.source_location.sheet
    for json_path in sorted(account_dir.glob("*.json")):
        if json_path.name.casefold() in {"extraction_report.json", "validation_report.json"}:
            continue
        try:
            raw = json.loads(json_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        document, provenance, body = _metadata(raw)
        form_match = re.search(r"acord[_ -]?(125|126|130|140)",
                               evidence.document_name or "", re.IGNORECASE)
        if form_match:
            ranges = (document.get("source_metadata") or {}).get("section_page_ranges", {})
            form_name = f"acord_{form_match.group(1)}"
            section = ranges.get(form_name) if isinstance(ranges, dict) else None
            if isinstance(section, dict) and section.get("source_file"):
                path = _match_sibling(account_dir, str(section["source_file"]))
                if path:
                    return path
        if sheet:
            sheets = body.get("sheets") or body.get("worksheets") or []
            if not any(isinstance(item, dict)
                       and str(item.get("sheet_name") or item.get("name") or "") == sheet
                       for item in sheets):
                continue
        names = _source_names(document, provenance)
        names.extend(json_path.stem + suffix for suffix in (".xlsx", ".pdf", ".xlsm"))
        for name in names:
            path = _match_sibling(account_dir, name)
            if path and (not sheet or path.suffix.lower() in {".xlsx", ".xlsm", ".xls"}):
                return path
    return None


def resolve_source_file(
    result: ReviewResult,
    evidence: EvidenceRef,
    accounts_dir: Path,
    workspace_root: Path,
) -> Optional[Path]:
    """Resolve only to existing PDF/XLSX files beneath configured roots."""
    if evidence.source_file:
        path = _safe_child(workspace_root, evidence.source_file)
        if path:
            return path
    account_root = accounts_dir.resolve()
    account_dir = (account_root / result.submission_id).resolve()
    try:
        account_dir.relative_to(account_root)
    except ValueError:
        return None
    if not account_dir.is_dir():
        return None
    return _legacy_account_source(evidence, account_dir)


def source_page_range(path: Path, evidence: EvidenceRef,
                      account_dir: Optional[Path] = None) -> Optional[Tuple[int, int]]:
    """Use supplied page provenance; otherwise use declared section ranges."""
    if evidence.source_location.page is not None:
        page = evidence.source_location.page
        return (page, page) if page > 0 else None
    if account_dir is None or not account_dir.is_dir():
        return None
    target_name = path.name.casefold()
    form_match = re.search(r"acord[_ -]?(125|126|130|140)",
                           evidence.document_name or "", re.IGNORECASE)
    form = f"acord_{form_match.group(1)}" if form_match else None
    for json_path in account_dir.glob("*.json"):
        if json_path.name.casefold() in {"extraction_report.json", "validation_report.json"}:
            continue
        try:
            raw = json.loads(json_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        document, provenance, _ = _metadata(raw)
        if not any(Path(name).name.casefold() == target_name
                   or re.sub(r"\(\d+\)(?=\.)", "", Path(name).name).casefold()
                   == re.sub(r"\(\d+\)(?=\.)", "", target_name)
                   for name in _source_names(document, provenance)):
            continue
        ranges = (document.get("source_metadata") or {}).get("section_page_ranges", {})
        value = None
        if isinstance(ranges, dict) and form:
            value = ranges.get(form)
        if isinstance(value, dict):
            value = value.get("pages")
        if isinstance(value, (list, tuple)) and len(value) == 2:
            try:
                return int(value[0]), int(value[1])
            except (TypeError, ValueError):
                return None
    return None


def find_matching_pdf_page(path: Path, evidence: EvidenceRef,
                           page_range: Optional[Tuple[int, int]]) -> Optional[int]:
    try:
        import fitz
        pdf = fitz.open(path)
    except Exception:
        return None
    try:
        if not len(pdf):
            return None
        start, end = page_range or (1, len(pdf))
        start, end = max(1, start), min(len(pdf), end)
        needles = []
        for value in (evidence.raw_value, evidence.normalized_value):
            if isinstance(value, (str, int, float)) and str(value).strip():
                needle = re.sub(r"[^a-z0-9]", "", str(value).casefold())
                if len(needle) >= 4 and needle not in needles:
                    needles.append(needle)
        for page_number in range(start, end + 1):
            text = re.sub(r"[^a-z0-9]", "", pdf[page_number - 1].get_text().casefold())
            if any(needle in text for needle in needles):
                return page_number
        return None
    finally:
        pdf.close()


def find_pdf_highlights(page: Any, evidence: EvidenceRef) -> list[Any]:
    """Locate citation values in PDF text, including display-formatted money.

    Extracted numeric values commonly omit currency symbols and grouping
    separators (12500000), while the visible form prints "$12,500,000".
    Search both the raw representation and conventional numeric display forms.
    """
    needles: list[str] = []
    for value in (evidence.raw_value, evidence.normalized_value):
        if isinstance(value, (str, int, float)) and str(value).strip():
            candidates = [str(value).strip()]
            if isinstance(value, (int, float)):
                number = float(value)
                if number.is_integer():
                    formatted = f"{int(number):,}"
                    candidates.extend((formatted, f"${formatted}", f"USD {formatted}"))
                else:
                    formatted = f"{number:,.2f}"
                    candidates.extend((formatted, f"${formatted}", f"USD {formatted}"))
            for candidate in candidates:
                if candidate.casefold() not in {item.casefold() for item in needles}:
                    needles.append(candidate)
    for needle in needles:
        try:
            matches = page.search_for(needle)
        except Exception:
            matches = []
        if matches:
            return matches
    return []


def workbook_preview(path: Path, evidence: EvidenceRef) -> Dict[str, Any]:
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=False, data_only=True)
    try:
        name = evidence.source_location.sheet
        if not name or name not in wb.sheetnames:
            return {"sheet": name, "rows": [], "highlight_cell": evidence.source_location.cell}
        ws = wb[name]
        center = evidence.source_location.row or 1
        match = re.search(r"rows\s+(\d+)\s*:\s*(\d+)",
                          evidence.source_location.json_path or "", re.IGNORECASE)
        if match:
            first, last = int(match.group(1)), min(ws.max_row, int(match.group(2)))
        else:
            first, last = max(1, center - 2), min(ws.max_row, center + 2)
        rows = []
        for row in ws.iter_rows(min_row=first, max_row=last):
            cells = [{"coordinate": cell.coordinate, "value": cell.value}
                     for cell in row if cell.value is not None]
            if cells:
                rows.append({"row": row[0].row, "cells": cells})
        return {"sheet": name, "rows": rows,
                "highlight_cell": evidence.source_location.cell,
                "source_row": center}
    finally:
        wb.close()
