"""Document type registry: type id -> adapter, plus content sniffing for
documents whose metadata does not declare a type."""
from __future__ import annotations

from typing import Dict, Optional, Type

from app.adapters.acord import (
    Acord125Adapter,
    Acord126Adapter,
    Acord130Adapter,
    Acord140Adapter,
)
from app.adapters.base import DocumentAdapter, get_any
from app.adapters.cellgrid_adapter import CellGridWorkbookAdapter
from app.adapters.combined import AcordCombinedAdapter, is_combined_acord
from app.adapters.commercial import (
    BrokerEmailAdapter,
    ChecklistAdapter,
    ExternalRecordsAdapter,
    GenericAdapter,
    OpsSupplementAdapter,
    QuoteAdapter,
    UnstructuredDocumentAdapter,
    WcRatingWorkbookAdapter,
)
from app.adapters.schedules import (
    DriverRosterAdapter,
    FleetScheduleAdapter,
    LossRunAdapter,
    SovAdapter,
)

_REGISTRY: Dict[str, Type[DocumentAdapter]] = {
    "acord_125": Acord125Adapter,
    "acord_126": Acord126Adapter,
    "acord_130": Acord130Adapter,
    "acord_140": Acord140Adapter,
    "sov": SovAdapter,
    "loss_run": LossRunAdapter,
    "fleet_schedule": FleetScheduleAdapter,
    "driver_roster": DriverRosterAdapter,
    "wc_rating_workbook": WcRatingWorkbookAdapter,
    "quote": QuoteAdapter,
    "broker_email": BrokerEmailAdapter,
    "submission_checklist": ChecklistAdapter,
    "ops_supplement": OpsSupplementAdapter,
    "external_records": ExternalRecordsAdapter,
    "unstructured_document": UnstructuredDocumentAdapter,
    "excel_workbook": CellGridWorkbookAdapter,
    "acord_combined": AcordCombinedAdapter,
}

# Declared-type aliases seen in the wild.
_ALIASES = {
    "combined_acord_submission": "acord_combined",
    "acord_combined_submission": "acord_combined",
    "combined_acord_packet": "acord_combined",
    "acord125": "acord_125", "acord126": "acord_126", "acord130": "acord_130",
    "acord140": "acord_140", "application": "acord_125",
    "wc_workbook": "wc_rating_workbook", "wc_rating": "wc_rating_workbook",
    "rating_workbook": "wc_rating_workbook",
    "loss_runs": "loss_run", "loss_detail": "loss_run",
    "fleet": "fleet_schedule", "vehicle_schedule": "fleet_schedule",
    "roster": "driver_roster", "drivers": "driver_roster",
    "driver_schedule": "driver_roster",
    "draft_quote": "quote", "checklist": "submission_checklist",
    "broker_narrative": "broker_email", "email": "broker_email",
    "operations_supplement": "ops_supplement",
    "external_research": "external_records", "public_records": "external_records",
    "schedule_of_values": "sov", "property_sov": "sov",
    "excel_workbook": "excel_workbook", "xlsx_workbook": "excel_workbook",
    "spreadsheet": "excel_workbook", "workbook": "excel_workbook",
}


class DocumentTypeRegistry:
    def adapter_for(self, declared_type: Optional[str], data: dict) -> DocumentAdapter:
        if declared_type:
            key = declared_type.strip().lower().replace(" ", "_").replace("-", "_")
            if (key.startswith(("acord_combined", "combined_acord"))
                    or (key.startswith("acord_") and "combined" in key)):
                return AcordCombinedAdapter()
            key = _ALIASES.get(key, key)
            if key in _REGISTRY:
                return _REGISTRY[key]()
            # Extractors use several account-agnostic names for packets that
            # contain multiple ACORD sections. Prefer the actual payload shape
            # over an unfamiliar declared label instead of degrading it to the
            # generic adapter.
            if is_combined_acord(data):
                return AcordCombinedAdapter()
            return GenericAdapter()
        sniffed = self.sniff(data)
        return _REGISTRY[sniffed]() if sniffed else UnstructuredDocumentAdapter()

    def canonical_type(self, declared_type: Optional[str], data: dict) -> str:
        return self.adapter_for(declared_type, data).document_type

    @staticmethod
    def sniff(data: dict) -> Optional[str]:
        from app.extraction.cellgrid import is_cell_grid_document

        if is_combined_acord(data):
            return "acord_combined"
        if is_cell_grid_document(data):
            return "excel_workbook"
        if get_any(data, "pages", "text_blocks", "full_text") is not None:
            return "unstructured_document"
        if get_any(data, "payroll_rows", "payroll_by_class") is not None or get_any(data, "experience_mod_history") is not None:
            return "wc_rating_workbook"
        if get_any(data, "class_rows", "classifications") is not None and get_any(data, "experience_mod") is not None:
            return "acord_130"
        if get_any(data, "claims", "losses", "loss_detail") is not None:
            return "loss_run"
        if get_any(data, "vehicles", "units") is not None:
            return "fleet_schedule"
        if get_any(data, "drivers", "roster") is not None:
            return "driver_roster"
        if get_any(data, "terms", "quote_terms") is not None or get_any(data, "rating_inputs") is not None:
            return "quote"
        if get_any(data, "items", "checklist") is not None:
            return "submission_checklist"
        if get_any(data, "observations", "records") is not None:
            return "external_records"
        if get_any(data, "body", "text") is not None and get_any(data, "subject") is not None:
            return "broker_email"
        if get_any(data, "premises", "buildings") is not None:
            return "acord_140"
        if get_any(data, "sales_rows", "area_rows") is not None or get_any(data, "claims_history") is not None:
            return "acord_126"
        if get_any(data, "applicant", "named_insured", "insured") is not None:
            return "acord_125"
        if get_any(data, "locations", "schedule", "values") is not None:
            return "sov"
        return None

    @property
    def supported_types(self) -> list[str]:
        return sorted(_REGISTRY)


_REGISTRY_SINGLETON: Optional[DocumentTypeRegistry] = None


def get_registry() -> DocumentTypeRegistry:
    global _REGISTRY_SINGLETON
    if _REGISTRY_SINGLETON is None:
        _REGISTRY_SINGLETON = DocumentTypeRegistry()
    return _REGISTRY_SINGLETON
