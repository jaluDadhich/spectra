"""Deterministic parser for cell-level workbook extraction JSON.

Spreadsheet extractors emit JSON shaped like::

    {"sheets": [{"sheet_name": "...", "all_nonempty_rows": [
        {"row": 4, "cells": [{"coordinate": "A4", "column": "A",
                              "value": "Driver ID", ...}, ...]}, ...]}]}

This module normalizes that shape (and the ``worksheets[].rows[].cells``
variant) into typed :class:`GridTable` objects: a title/context block, a
header row, and data rows keyed by header name — each cell retaining its
worksheet, row number, column letter and JSON path for citation.

No account-specific values appear here; only the mechanical structure of
the extraction format is interpreted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class CellValue:
    column: str           # column letter, e.g. "B"
    coordinate: str       # e.g. "B7"
    value: Any
    row: int              # 1-based worksheet row number
    row_index: int        # 0-based index into the sheet's row list
    cell_index: int       # 0-based index into the row's cell list
    formula: Optional[str] = None
    cached_value: Any = None


@dataclass
class GridTable:
    sheet_name: str
    sheet_index: int
    header_row: int                       # 1-based worksheet row number
    header_row_index: int                 # 0-based row-list index
    headers: List[str]                    # header text per column letter
    columns: List[str]                    # column letters aligned to headers
    header_coordinates: Dict[str, str]    # header text -> cell coordinate
    data_rows: List[Tuple[int, int, Dict[str, CellValue]]]  # (row_number, row_index, header->cell)
    title_text: str = ""                  # banner/context rows above the header
    path_prefix: str = ""                 # e.g. "sheets[0].all_nonempty_rows"

    def row_json_path(self, row_index: int) -> str:
        return f"{self.path_prefix}[{row_index}]"

    def cell_json_path(self, row_index: int, cell_index: int) -> str:
        return f"{self.path_prefix}[{row_index}].cells[{cell_index}]"

    def header_json_path(self, header: str) -> str:
        return f"{self.path_prefix}[{self.header_row_index}]"


@dataclass
class SheetGrid:
    name: str
    index: int
    path_prefix: str
    rows: List[Tuple[int, int, List[CellValue]]] = field(default_factory=list)
    # (row_number, row_index, cells)


def _iter_sheets(data: Dict[str, Any]) -> List[Tuple[int, str, Dict[str, Any], str]]:
    """Return (sheet_index, name, sheet_dict, row_list_path) for each sheet."""
    sheets = data.get("sheets") or data.get("worksheets")
    if not isinstance(sheets, list):
        return []
    found = []
    for i, sheet in enumerate(sheets):
        if not isinstance(sheet, dict):
            continue
        name = str(sheet.get("sheet_name") or sheet.get("name") or f"Sheet {i + 1}")
        if isinstance(sheet.get("all_nonempty_rows"), list):
            found.append((i, name, sheet, f"sheets[{i}].all_nonempty_rows"))
        elif isinstance(sheet.get("rows"), list):
            base = "sheets" if "sheets" in data else "worksheets"
            found.append((i, name, sheet, f"{base}[{i}].rows"))
    return found


def _parse_cells(row_obj: Dict[str, Any]) -> List[Tuple[str, str, Any, int, Optional[str], Any]]:
    """Normalize cells and preserve formula metadata for cited calculations."""
    cells = row_obj.get("cells")
    out: List[Tuple[str, str, Any, int, Optional[str], Any]] = []
    if isinstance(cells, list):
        for idx, cell in enumerate(cells):
            if not isinstance(cell, dict):
                continue
            value = cell.get("value")
            cached_value = cell.get("cached_value")
            if (value is None and cached_value is None) or (
                    isinstance(value, str) and not value.strip()
                    and cached_value is None):
                continue
            column = str(cell.get("column") or "").strip().upper()
            coordinate = str(cell.get("coordinate") or "")
            if not column and coordinate:
                column = "".join(ch for ch in coordinate if ch.isalpha()).upper()
            if not column:
                column = coordinate
            out.append((column, coordinate or column, value, idx,
                        cell.get("formula"), cached_value))
    elif isinstance(cells, dict):
        for idx, (coord, cell) in enumerate(sorted(cells.items())):
            value = cell.get("value") if isinstance(cell, dict) else cell
            cached_value = cell.get("cached_value") if isinstance(cell, dict) else None
            if (value is None and cached_value is None) or (
                    isinstance(value, str) and not value.strip()
                    and cached_value is None):
                continue
            raw_coordinate = str(coord)
            column = "".join(ch for ch in raw_coordinate if ch.isalpha()).upper()
            # Direct workbook loading stores a row's cells as {"A": value}
            # and carries the actual worksheet row on the row object.
            # Expand that to A17 so citation links have a real cell address.
            coordinate = (raw_coordinate if any(ch.isdigit() for ch in raw_coordinate)
                          else f"{column}{row_obj.get('row', row_obj.get('row_number', ''))}")
            formula = cell.get("formula") if isinstance(cell, dict) else None
            out.append((column, coordinate, value, idx, formula, cached_value))
    return out


def parse_workbook(data: Dict[str, Any]) -> List[SheetGrid]:
    grids: List[SheetGrid] = []
    for sheet_index, name, sheet, path_prefix in _iter_sheets(data):
        row_key = "all_nonempty_rows" if "all_nonempty_rows" in sheet else "rows"
        grid = SheetGrid(name=name, index=sheet_index, path_prefix=path_prefix)
        for row_index, row_obj in enumerate(sheet.get(row_key) or []):
            if not isinstance(row_obj, dict):
                continue
            row_number = row_obj.get("row", row_obj.get("row_number", row_index + 1))
            try:
                row_number = int(row_number)
            except (TypeError, ValueError):
                row_number = row_index + 1
            cells = [
                CellValue(column=col, coordinate=coord, value=val,
                          row=row_number, row_index=row_index, cell_index=cell_idx,
                          formula=formula, cached_value=cached_value)
                for col, coord, val, cell_idx, formula, cached_value
                in _parse_cells(row_obj)
            ]
            if cells:
                grid.rows.append((row_number, row_index, cells))
        grids.append(grid)
    return grids


def _looks_like_header(cells: List[CellValue], next_cells: Optional[List[CellValue]]) -> bool:
    if len(cells) < 2:
        return False
    text_cells = sum(1 for c in cells if isinstance(c.value, str))
    if text_cells < max(2, len(cells) // 2):
        return False
    if next_cells is None or len(next_cells) < 2:
        return False
    # Header labels should not duplicate the next row's values wholesale.
    header_vals = {str(c.value).strip().lower() for c in cells}
    next_vals = {str(c.value).strip().lower() for c in next_cells}
    return header_vals != next_vals


def extract_tables(grid: SheetGrid) -> List[GridTable]:
    """Detect at most one primary table per sheet (banner rows, then a
    header row, then data rows). Sheets without a detectable header return
    no tables."""
    rows = grid.rows
    if not rows:
        return []
    header_pos: Optional[int] = None
    for pos in range(len(rows)):
        _, _, cells = rows[pos]
        next_cells = rows[pos + 1][2] if pos + 1 < len(rows) else None
        if len(cells) == 1 and cells[0].column == "A":
            continue  # banner/title row
        if _looks_like_header(cells, next_cells):
            header_pos = pos
            break
    if header_pos is None:
        return []

    header_number, header_index, header_cells = rows[header_pos]
    headers = [str(c.value).strip() for c in header_cells]
    columns = [c.column for c in header_cells]
    header_coordinates = {str(c.value).strip(): c.coordinate for c in header_cells}

    title_lines = []
    for _, _, cells in rows[:header_pos]:
        title_lines.append(" | ".join(str(c.value).strip() for c in cells))

    data_rows: List[Tuple[int, int, Dict[str, CellValue]]] = []
    for row_number, row_index, cells in rows[header_pos + 1:]:
        by_header: Dict[str, CellValue] = {}
        for cell in cells:
            if cell.column in columns:
                header = headers[columns.index(cell.column)]
                by_header[header] = cell
        if by_header:
            data_rows.append((row_number, row_index, by_header))

    return [GridTable(
        sheet_name=grid.name, sheet_index=grid.index,
        header_row=header_number, header_row_index=header_index,
        headers=headers, columns=columns,
        header_coordinates=header_coordinates,
        data_rows=data_rows, title_text="\n".join(title_lines),
        path_prefix=grid.path_prefix,
    )]


def parse_tables(data: Dict[str, Any]) -> List[GridTable]:
    tables: List[GridTable] = []
    for grid in parse_workbook(data):
        tables.extend(extract_tables(grid))
    return tables


def is_cell_grid_document(data: Dict[str, Any]) -> bool:
    return bool(_iter_sheets(data))
