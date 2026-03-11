"""Tests for the trust tier definitions."""
import pytest

from cleanline.tiers import (
    DEFAULT_TIER,
    TIER_DEFAULTS,
    TIER_ORDER,
    VALID_TIERS,
    get_tier_config,
    tier_index,
    validate_tier,
)


def test_default_tier_is_balanced() -> None:
    assert DEFAULT_TIER == "balanced"


def test_valid_tiers_has_three() -> None:
    assert VALID_TIERS == {"cautious", "balanced", "flow"}


def test_tier_order() -> None:
    assert TIER_ORDER == ("cautious", "balanced", "flow")


def test_all_tiers_have_required_keys() -> None:
    """Every tier must define the exact same set of config keys."""
    keys = set(TIER_DEFAULTS["balanced"].keys())
    for tier_name in VALID_TIERS:
        assert set(TIER_DEFAULTS[tier_name].keys()) == keys, (
            f"Tier '{tier_name}' has mismatched keys"
        )


def test_get_tier_config_valid() -> None:
    for tier_name in VALID_TIERS:
        cfg = get_tier_config(tier_name)
        assert isinstance(cfg, dict)
        assert "suggest_min_count" in cfg


def test_get_tier_config_invalid_raises() -> None:
    with pytest.raises(ValueError, match="Unknown tier"):
        get_tier_config("extreme")


def test_validate_tier() -> None:
    assert validate_tier("cautious") is True
    assert validate_tier("balanced") is True
    assert validate_tier("flow") is True
    assert validate_tier("unknown") is False
    assert validate_tier("") is False


def test_tier_index_ordering() -> None:
    assert tier_index("cautious") == 0
    assert tier_index("balanced") == 1
    assert tier_index("flow") == 2


def test_suggest_min_count_ordering() -> None:
    """Cautious requires more evidence than balanced, balanced more than flow."""
    assert TIER_DEFAULTS["cautious"]["suggest_min_count"] > TIER_DEFAULTS["balanced"]["suggest_min_count"]
    assert TIER_DEFAULTS["balanced"]["suggest_min_count"] > TIER_DEFAULTS["flow"]["suggest_min_count"]


def test_suggest_write_min_count_always_higher() -> None:
    """Write suggestions always require more evidence than general suggestions."""
    for tier_name in VALID_TIERS:
        cfg = TIER_DEFAULTS[tier_name]
        assert cfg["suggest_write_min_count"] >= cfg["suggest_min_count"], (
            f"Tier '{tier_name}': write min_count should be >= general min_count"
        )


def test_tighten_days_ordering() -> None:
    """Cautious tightens faster, flow is more lenient."""
    assert TIER_DEFAULTS["cautious"]["tighten_days"] < TIER_DEFAULTS["balanced"]["tighten_days"]
    assert TIER_DEFAULTS["balanced"]["tighten_days"] < TIER_DEFAULTS["flow"]["tighten_days"]


def test_cautious_has_no_write_paths() -> None:
    assert TIER_DEFAULTS["cautious"]["write_paths"] == []


def test_cautious_has_no_command_mappings() -> None:
    assert TIER_DEFAULTS["cautious"]["command_mappings"] == {}


def test_balanced_has_tmp_write() -> None:
    assert "/tmp/**" in TIER_DEFAULTS["balanced"]["write_paths"]


def test_flow_has_documents_write() -> None:
    assert "~/Documents/**" in TIER_DEFAULTS["flow"]["write_paths"]
    assert "~/Desktop/**" in TIER_DEFAULTS["flow"]["write_paths"]


def test_confidence_thresholds_ordering() -> None:
    """Higher confidence thresholds for cautious, lower for flow."""
    assert (
        TIER_DEFAULTS["cautious"]["suggest_confidence_high"]
        > TIER_DEFAULTS["balanced"]["suggest_confidence_high"]
        > TIER_DEFAULTS["flow"]["suggest_confidence_high"]
    )
