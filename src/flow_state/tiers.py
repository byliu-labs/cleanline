"""Trust tier definitions for Flow State.

Three tiers control the defaults that `flowstate setup` generates.
Every threshold that varies by tier lives here — nowhere else.

Tiers are metadata, not enforcement. They shape config content;
actual security comes from the config itself and the hardcoded deny list.
"""
from __future__ import annotations

VALID_TIERS = frozenset({"cautious", "balanced", "flow"})
DEFAULT_TIER = "balanced"

# Ordered from most restrictive to least restrictive
TIER_ORDER = ("cautious", "balanced", "flow")

# ============================================================================
# TIER POLICY TABLE
# ============================================================================

TIER_DEFAULTS: dict[str, dict] = {
    "cautious": {
        # Domain layer: "cautious" = only known_domains.json (8 docs domains)
        "domain_set": "cautious",
        # File access
        "read_paths": ["~/.claude/**", "~/.config/**"],
        "write_paths": [],
        # Command mappings
        "command_mappings": {},
        # suggest thresholds
        "suggest_min_count": 5,
        "suggest_write_min_count": 10,
        "suggest_confidence_high": 15,
        "suggest_confidence_medium": 8,
        # tighten threshold
        "tighten_days": 14,
    },
    "balanced": {
        "domain_set": "balanced",
        "read_paths": ["~/.claude/**", "~/.config/**", "/tmp/**"],
        "write_paths": ["/tmp/**"],
        "command_mappings": {
            "pip install": ["pip3 install", "uv pip install"],
            "pytest": ["python -m pytest", "python3 -m pytest"],
            "npm test": ["npx jest"],
        },
        "suggest_min_count": 3,
        "suggest_write_min_count": 5,
        "suggest_confidence_high": 10,
        "suggest_confidence_medium": 5,
        "tighten_days": 30,
    },
    "flow": {
        "domain_set": "flow",
        "read_paths": [
            "~/.claude/**",
            "~/.config/**",
            "/tmp/**",
            "~/Documents/**",
            "~/Desktop/**",
        ],
        "write_paths": ["/tmp/**", "~/Documents/**", "~/Desktop/**"],
        "command_mappings": {
            "pip install": ["pip3 install", "uv pip install"],
            "pytest": ["python -m pytest", "python3 -m pytest"],
            "npm test": ["npx jest"],
            "cargo build": ["cargo build --release"],
            "cargo test": ["cargo test --all"],
            "docker compose up": ["docker-compose up"],
        },
        "suggest_min_count": 2,
        "suggest_write_min_count": 3,
        "suggest_confidence_high": 7,
        "suggest_confidence_medium": 3,
        "tighten_days": 60,
    },
}

# All tier configs must have these keys
_REQUIRED_KEYS = frozenset(TIER_DEFAULTS["balanced"].keys())


def get_tier_config(tier: str) -> dict:
    """Return the policy dict for a tier. Raises ValueError for unknown tiers."""
    if tier not in VALID_TIERS:
        raise ValueError(f"Unknown tier '{tier}'. Valid: {sorted(VALID_TIERS)}")
    return TIER_DEFAULTS[tier]


def validate_tier(tier: str) -> bool:
    """Return True if tier is a valid tier name."""
    return tier in VALID_TIERS


def tier_index(tier: str) -> int:
    """Return ordinal position (0=cautious, 1=balanced, 2=flow)."""
    return TIER_ORDER.index(tier)
