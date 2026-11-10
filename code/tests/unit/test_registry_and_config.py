import pytest

from app.catalog.profiles import default_profile, load_profile
from app.catalog.issue_register import load_register
from app.config import Settings
from app.rules import load_registry
from app.rules.registry import RuleRegistry, Ruleset


def test_default_ruleset_validates():
    registry = load_registry("default")
    assert len(registry.enabled_rules()) >= 20
    assert registry.ruleset.version


def test_issue_register_is_exactly_u01_through_u14():
    register = load_register()
    assert register.issue_ids == [f"U{number:02d}" for number in range(1, 15)]


def test_registry_rejects_missing_handler():
    ruleset = Ruleset.model_validate({
        "ruleset_id": "x", "version": "1",
        "rules": [{"rule_id": "R1", "version": "1", "taxonomy_id": "T",
                   "handler": "does_not_exist", "title": "t"}]})
    with pytest.raises(ValueError, match="no implementation"):
        RuleRegistry(ruleset, {})


def test_registry_rejects_orphan_handler():
    ruleset = Ruleset.model_validate({
        "ruleset_id": "x", "version": "1",
        "rules": [{"rule_id": "R1", "version": "1", "taxonomy_id": "T",
                   "handler": "h1", "title": "t"}]})
    with pytest.raises(ValueError, match="not present in ruleset"):
        RuleRegistry(ruleset, {"h1": lambda c: [], "h2": lambda c: []})


def test_registry_rejects_duplicate_rule_ids():
    ruleset = Ruleset.model_validate({
        "ruleset_id": "x", "version": "1",
        "rules": [
            {"rule_id": "R1", "version": "1", "taxonomy_id": "T",
             "handler": "h1", "title": "t"},
            {"rule_id": "R1", "version": "2", "taxonomy_id": "T",
             "handler": "h1", "title": "t2"}]})
    with pytest.raises(ValueError, match="Duplicate"):
        RuleRegistry(ruleset, {"h1": lambda c: []})


def test_profile_scopes_by_line_of_business():
    profile = default_profile()
    wc = profile.required_types("workers_comp")
    assert "acord_130" in wc and "driver_roster" not in wc
    package = profile.required_types("package")
    assert "driver_roster" in package


def test_missing_profile_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_profile("nope", tmp_path)


def test_settings_validation(tmp_path):
    with pytest.raises(Exception):
        Settings(_env_file=None, spectra_log_level="CHATTY")
    s = Settings(_env_file=None, database_url="sqlite:///" + str(tmp_path / "x.db"))
    assert s.llm_available is False
    with pytest.raises(ValueError):
        Settings(_env_file=None, database_url="postgres://x").sqlite_path


def test_secret_not_serialized():
    s = Settings(_env_file=None, openai_api_key="sk-test-secret-123")
    assert "sk-test-secret-123" not in s.model_dump_json()
