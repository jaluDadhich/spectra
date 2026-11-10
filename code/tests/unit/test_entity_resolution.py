from app.entity_resolution import align_periods, match_locations, match_names
from app.schemas.canonical import LocationRecord


def loc(**kw):
    return LocationRecord(record_id=kw.pop("record_id", "x"), document_id="d", **kw)


def test_name_match_is_never_conclusive():
    assert match_names("Acme LLC", "Acme LLC").state == "PARTIAL"
    assert match_names("Acme Logistics LLC", "Acme Logistics Inc").state == "PARTIAL"
    assert match_names("Acme", "Totally Different").state == "UNRESOLVED"
    assert match_names(None, "Acme").state == "UNRESOLVED"


def test_location_match_requires_strong_identifiers():
    a = loc(location_id="L1", address="100 Main St", city="Fresno", state="CA",
            postal_code="93721")
    same = loc(location_id="L1", address="different text", city="Elsewhere", state="NV")
    assert match_locations(a, same).state == "UNRESOLVED"  # conflicting street addresses override ID
    b = loc(location_id="L2", address="100 Main Street", city="Fresno", state="CA",
            postal_code="93721")
    assert match_locations(a, b).state == "MATCHED"  # full normalized address
    partial = loc(location_id="L3", address="100 Main St", state="CA")
    assert match_locations(a, partial).state in {"PARTIAL", "MATCHED"}
    other_state = loc(location_id="L4", address="100 Main St", city="Reno", state="NV")
    assert match_locations(a, other_state).state in {"UNRESOLVED", "PARTIAL"}
    bare = loc(location_id="L5")
    assert match_locations(a, bare).state == "UNRESOLVED"


def test_period_alignment():
    assert align_periods("2025", "2025").state == "MATCHED"
    assert align_periods("2025-01-01", "2025-06-01").state == "PARTIAL"
    assert align_periods("2024", "2025").state == "UNRESOLVED"
    assert align_periods(None, "2025").state == "PARTIAL"
    assert align_periods(None, None).state == "UNRESOLVED"
