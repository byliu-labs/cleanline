"""Tests for profile schema validation."""
from claude_hooks.schema import MAX_ALIASES, MAX_DOMAINS, MAX_MAPPINGS, validate_profile


def test_valid_profile_passes(sample_profile: dict) -> None:
    errors, warnings = validate_profile(sample_profile)
    assert errors == []


def test_missing_name_fails() -> None:
    profile = {"version": "1.0"}
    errors, _ = validate_profile(profile)
    assert any("name" in e for e in errors)


def test_missing_version_fails() -> None:
    profile = {"name": "test"}
    errors, _ = validate_profile(profile)
    assert any("version" in e for e in errors)


def test_empty_name_fails() -> None:
    profile = {"name": "", "version": "1.0"}
    errors, _ = validate_profile(profile)
    assert any("name" in e for e in errors)


def test_over_cap_aliases_rejects() -> None:
    profile = {
        "name": "test",
        "version": "1.0",
        "bashAliases": {f"alias{i}": "cmd" for i in range(MAX_ALIASES + 1)},
    }
    errors, _ = validate_profile(profile)
    assert any("bashAliases" in e and "max" in e for e in errors)


def test_over_cap_mappings_rejects() -> None:
    profile = {
        "name": "test",
        "version": "1.0",
        "commandMappings": {f"cmd{i}": [f"alias{i}"] for i in range(MAX_MAPPINGS + 1)},
    }
    errors, _ = validate_profile(profile)
    assert any("commandMappings" in e and "max" in e for e in errors)


def test_over_cap_domains_rejects() -> None:
    profile = {
        "name": "test",
        "version": "1.0",
        "webfetch": {"extraDomains": [f"*.d{i}.com" for i in range(MAX_DOMAINS + 1)]},
    }
    errors, _ = validate_profile(profile)
    assert any("extraDomains" in e and "max" in e for e in errors)


def test_warning_at_half_cap() -> None:
    # Just over 50% of MAX_ALIASES
    count = int(MAX_ALIASES * 0.5) + 1
    profile = {
        "name": "test",
        "version": "1.0",
        "bashAliases": {f"alias{i}": "cmd" for i in range(count)},
    }
    errors, warnings = validate_profile(profile)
    assert errors == []
    assert any("bashAliases" in w for w in warnings)


def test_invalid_aliases_type() -> None:
    profile = {"name": "test", "version": "1.0", "bashAliases": "not-a-dict"}
    errors, _ = validate_profile(profile)
    assert any("bashAliases" in e and "object" in e for e in errors)


def test_invalid_mappings_value_type() -> None:
    profile = {
        "name": "test",
        "version": "1.0",
        "commandMappings": {"npm test": "not-a-list"},
    }
    errors, _ = validate_profile(profile)
    assert any("must be a list" in e for e in errors)


def test_minimal_valid_profile() -> None:
    profile = {"name": "minimal", "version": "0.1"}
    errors, warnings = validate_profile(profile)
    assert errors == []
    assert warnings == []
