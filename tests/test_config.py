"""Configuration consistency tests.

These lock two invariants that were easy to break by hand:
- the in-code ``DEFAULT_CONFIG`` fallback must stay in sync with ``config.yaml``;
- ``neighbor_timeout`` must be derived from ``beacon_max_interval`` (so a node that
  backs off to the dynamic ceiling is not aged out before it re-announces).
"""
from pathlib import Path

import yaml

from config.config_handler import ConfigHandler

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_default_config_matches_yaml():
    # The embedded fallback is only used when config.yaml is missing; if the two ever
    # drift, behaviour silently changes depending on whether config.yaml exists.
    with open(REPO_ROOT / "config.yaml") as f:
        yaml_cfg = yaml.safe_load(f)
    assert ConfigHandler.DEFAULT_CONFIG == yaml_cfg


def test_neighbor_timeout_derived_from_max_interval():
    cfg = ConfigHandler()
    expected = 3.0 * cfg.get("scheduler", "beacon_max_interval")
    assert cfg.get("scheduler", "neighbor_timeout") == expected
