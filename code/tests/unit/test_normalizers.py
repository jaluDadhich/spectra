from app.normalization import (
    classify_value_presence,
    normalize_address,
    normalize_class_code,
    normalize_date,
    normalize_entity_name,
    normalize_money,
    normalize_state,
    normalize_vin,
    parse_number,
)


def test_normalize_date_formats():
    assert normalize_date("2026-10-28") == "2026-10-28"
    assert normalize_date("10/28/2026") == "2026-10-28"
    assert normalize_date("2026-10-28T10:00:00Z") == "2026-10-28"
    assert normalize_date("not a date") is None
    assert normalize_date(None) is None
    assert normalize_date("") is None


def test_parse_number():
    assert parse_number("$1,234.56") == 1234.56
    assert parse_number("(500)") == -500.0
    assert parse_number("12.5%") == 12.5
    assert parse_number("N/A") is None
    assert parse_number(None) is None
    assert parse_number(True) is None
    assert parse_number(7) == 7.0


def test_normalize_money_currency():
    assert normalize_money("€1,000") == (1000.0, "EUR")
    assert normalize_money("$2,500") == (2500.0, "USD")
    assert normalize_money(100) == (100.0, "USD")
    assert normalize_money("abc") == (None, None)


def test_normalize_state():
    assert normalize_state("California") == "CA"
    assert normalize_state("ca") == "CA"
    assert normalize_state("XX") is None
    assert normalize_state(None) is None
    assert normalize_state("ST") is None  # street suffix is not a state


def test_entity_name_preserves_distinctness():
    assert normalize_entity_name("Acme Logistics, LLC") == "acme logistics"
    assert normalize_entity_name("ACME LOGISTICS INC") == "acme logistics"
    assert normalize_entity_name("Different Co") != normalize_entity_name("Acme Logistics LLC")
    assert normalize_entity_name("") is None


def test_address_normalization():
    assert normalize_address("100 Warehouse Street") == "100 warehouse st"
    assert normalize_address("Suite 4, 100 Warehouse St.") == "4 100 warehouse st" or \
        "100 warehouse st" in normalize_address("100 Warehouse St #4")


def test_vin_and_class_code():
    assert normalize_vin("1ft-yr10u33pa12345") == "1FTYR10U33PA12345"
    assert normalize_class_code("8810") == "8810"
    assert normalize_class_code(10) == "0010"
    assert normalize_class_code("") is None


def test_value_presence_classification():
    assert classify_value_presence(None) == "missing"
    assert classify_value_presence("") == "empty"
    assert classify_value_presence("N/A") == "not_applicable"
    assert classify_value_presence("None") == "explicit_negative"
    assert classify_value_presence(0) == "zero"
    assert classify_value_presence(5) == "present"
    assert classify_value_presence("x") == "present"
