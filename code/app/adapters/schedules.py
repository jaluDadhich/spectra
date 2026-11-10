"""Adapters for SOV, loss runs, fleet schedules, and driver rosters."""
from __future__ import annotations

from app.adapters.acord import _location_from
from app.adapters.base import (
    AdaptedDocument,
    DocumentAdapter,
    EvidenceFactory,
    as_list,
    get_any,
)
from app.normalization import (
    normalize_date,
    normalize_money,
    normalize_state,
    normalize_vin,
    parse_number,
)
from app.schemas.canonical import ClaimRecord, DriverRecord, VehicleRecord
from app.schemas.envelope import InputEnvelope


class SovAdapter(DocumentAdapter):
    document_type = "sov"

    def extract(self, envelope: InputEnvelope, ev: EvidenceFactory, out: AdaptedDocument) -> None:
        data, doc_id = envelope.data, out.document.document_id
        for i, entry in enumerate(as_list(get_any(data, "locations", "schedule", "values"))):
            if isinstance(entry, dict):
                out.locations.append(_location_from(entry, i, doc_id, ev, f"locations[{i}]"))


class LossRunAdapter(DocumentAdapter):
    document_type = "loss_run"

    def extract(self, envelope: InputEnvelope, ev: EvidenceFactory, out: AdaptedDocument) -> None:
        data, doc_id = envelope.data, out.document.document_id
        valuation = normalize_date(get_any(data, "valuation_date", "as_of"))
        for i, row in enumerate(as_list(get_any(data, "claims", "losses", "loss_detail"))):
            if not isinstance(row, dict):
                continue
            claim_id = get_any(row, "claim_id", "claim_number", "id") or f"{doc_id}#claim{i}"
            rid = str(claim_id)
            paid, _ = normalize_money(get_any(row, "paid", "paid_amount"))
            reserves, _ = normalize_money(get_any(row, "reserves", "reserve", "outstanding"))
            incurred, _ = normalize_money(get_any(row, "incurred", "total_incurred"))
            fe = {}
            for field, raw, norm in (
                ("claim_id", claim_id, str(claim_id)),
                ("loss_date", get_any(row, "loss_date", "date_of_loss"),
                 normalize_date(get_any(row, "loss_date", "date_of_loss"))),
                ("paid", get_any(row, "paid", "paid_amount"), paid),
                ("reserves", get_any(row, "reserves", "reserve", "outstanding"), reserves),
                ("incurred", get_any(row, "incurred", "total_incurred"), incurred),
                ("status", get_any(row, "status"), get_any(row, "status")),
            ):
                if raw is not None:
                    fe[field] = ev.make(f"claims[{i}].{field}", f"claims[{rid}].{field}", raw,
                                        normalized_value=norm, unit="USD" if field in {"paid", "reserves", "incurred"} else None,
                                        period=valuation)
            out.claims.append(ClaimRecord(
                record_id=rid, document_id=doc_id, field_evidence=fe, claim_id=str(claim_id),
                line_of_business=get_any(row, "line_of_business", "lob"),
                loss_date=normalize_date(get_any(row, "loss_date", "date_of_loss")),
                paid=paid, reserves=reserves, incurred_reported=incurred,
                status=get_any(row, "status"),
                valuation_date=valuation,
                policy_year=(str(y) if (y := get_any(row, "policy_year")) is not None else None)))
        summary = get_any(data, "summary", "totals", default={}) or {}
        if isinstance(summary, dict) and summary:
            from app.schemas.canonical import ExposureRecord
            total_incurred, _ = normalize_money(get_any(summary, "total_incurred", "incurred"))
            count = parse_number(get_any(summary, "claim_count", "total_claims", "count"))
            fe = {}
            if total_incurred is not None:
                fe["total_incurred"] = ev.make("summary.total_incurred", "loss_summary.total_incurred",
                                               get_any(summary, "total_incurred", "incurred"),
                                               normalized_value=total_incurred, unit="USD", period=valuation)
            if count is not None:
                fe["claim_count"] = ev.make("summary.claim_count", "loss_summary.claim_count",
                                            get_any(summary, "claim_count", "total_claims", "count"),
                                            normalized_value=count, period=valuation)
            out.exposures.append(ExposureRecord(
                record_id=f"{doc_id}#summary", document_id=doc_id, field_evidence=fe,
                exposure_type="loss_summary", value=total_incurred, unit="USD",
                period=valuation,
                description=f"declared_count={int(count) if count is not None else 'unknown'}"))
            for i, entry in enumerate(as_list(get_any(summary, "annual_counts", "by_year"))):
                if not isinstance(entry, dict):
                    continue
                cnt = parse_number(get_any(entry, "count", "claim_count"))
                year = get_any(entry, "year", "policy_year")
                eid = ev.make(f"summary.annual_counts[{i}].count",
                              f"loss_summary.annual_counts[{year}].count",
                              get_any(entry, "count", "claim_count"),
                              normalized_value=cnt, period=str(year))
                out.exposures.append(ExposureRecord(
                    record_id=f"{doc_id}#annual{i}", document_id=doc_id,
                    field_evidence={"count": eid}, exposure_type="annual_claim_count",
                    value=cnt, unit="count",
                    period=str(year) if year is not None else None,
                    description=str(get_any(entry, "line_of_business", "lob", default=""))))


class FleetScheduleAdapter(DocumentAdapter):
    document_type = "fleet_schedule"

    def extract(self, envelope: InputEnvelope, ev: EvidenceFactory, out: AdaptedDocument) -> None:
        data, doc_id = envelope.data, out.document.document_id
        for i, row in enumerate(as_list(get_any(data, "vehicles", "units", "schedule"))):
            if not isinstance(row, dict):
                continue
            unit_id = get_any(row, "unit_id", "unit", "id") or f"{doc_id}#veh{i}"
            rid = str(unit_id)
            fe = {}
            for field, raw, norm in (
                ("vin", get_any(row, "vin"), normalize_vin(get_any(row, "vin"))),
                ("status", get_any(row, "status"), (str(s).lower() if (s := get_any(row, "status")) else None)),
                ("gvwr", get_any(row, "gvwr", "gvw"), parse_number(get_any(row, "gvwr", "gvw"))),
                ("garaging_state", get_any(row, "garaging_state", "garage_state"),
                 normalize_state(get_any(row, "garaging_state", "garage_state"))),
                ("garaging_address", get_any(row, "garaging_address", "garage_address"),
                 get_any(row, "garaging_address", "garage_address")),
                ("assigned_driver_id", get_any(row, "assigned_driver_id", "driver_id"),
                 get_any(row, "assigned_driver_id", "driver_id")),
                ("cost_new", get_any(row, "cost_new"), parse_number(get_any(row, "cost_new"))),
            ):
                if raw is not None:
                    fe[field] = ev.make(f"vehicles[{i}].{field}", f"vehicles[{rid}].{field}", raw,
                                        normalized_value=norm)
            out.vehicles.append(VehicleRecord(
                record_id=rid, document_id=doc_id, field_evidence=fe, unit_id=str(unit_id),
                vin=normalize_vin(get_any(row, "vin")),
                status=(str(s).lower() if (s := get_any(row, "status")) else None),
                gvwr=parse_number(get_any(row, "gvwr", "gvw")),
                vehicle_type=get_any(row, "vehicle_type", "type"),
                garaging_state=normalize_state(get_any(row, "garaging_state", "garage_state")),
                garaging_address=get_any(row, "garaging_address", "garage_address"),
                assigned_driver_id=(str(d) if (d := get_any(row, "assigned_driver_id", "driver_id")) is not None else None),
                cost_new=parse_number(get_any(row, "cost_new"))))


class DriverRosterAdapter(DocumentAdapter):
    document_type = "driver_roster"

    def extract(self, envelope: InputEnvelope, ev: EvidenceFactory, out: AdaptedDocument) -> None:
        data, doc_id = envelope.data, out.document.document_id
        roster_date = normalize_date(get_any(data, "roster_date", "as_of", "review_date"))
        for i, row in enumerate(as_list(get_any(data, "drivers", "roster"))):
            if not isinstance(row, dict):
                continue
            driver_id = get_any(row, "driver_id", "id") or f"{doc_id}#drv{i}"
            rid = str(driver_id)
            fe = {}
            for field, raw, norm in (
                ("name", get_any(row, "name", "driver_name"), get_any(row, "name", "driver_name")),
                ("license_class", get_any(row, "license_class", "license"), get_any(row, "license_class", "license")),
                ("mvr_review_date", get_any(row, "mvr_review_date", "mvr_date"),
                 normalize_date(get_any(row, "mvr_review_date", "mvr_date"))),
                ("hire_date", get_any(row, "hire_date"), normalize_date(get_any(row, "hire_date"))),
            ):
                if raw is not None:
                    fe[field] = ev.make(f"drivers[{i}].{field}", f"drivers[{rid}].{field}", raw,
                                        normalized_value=norm)
            out.drivers.append(DriverRecord(
                record_id=rid, document_id=doc_id, field_evidence=fe, driver_id=str(driver_id),
                name=get_any(row, "name", "driver_name"),
                license_class=get_any(row, "license_class", "license"),
                cdl_endorsements=[str(e) for e in as_list(get_any(row, "cdl_endorsements", "endorsements"))],
                mvr_review_date=normalize_date(get_any(row, "mvr_review_date", "mvr_date")),
                hire_date=normalize_date(get_any(row, "hire_date"))))
