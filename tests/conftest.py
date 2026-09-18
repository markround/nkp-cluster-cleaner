"""Shared fixtures for the test suite. Object builders live in factories.py."""

import pytest

from nkp_cluster_cleaner.config import ConfigManager


@pytest.fixture
def config_manager():
    """A ConfigManager with no rules loaded (the default, permissive state)."""
    return ConfigManager()


@pytest.fixture
def owner_label_config(tmp_path):
    """A ConfigManager requiring an `owner` label and protecting `*-prod-*`."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "protected_cluster_patterns:\n"
        "- .*-prod-.*\n"
        "excluded_namespace_patterns:\n"
        "- ^default$\n"
        "extra_labels:\n"
        "- name: owner\n"
        "  description: Cluster owner identifier\n"
        "- name: cost_centre\n"
        '  regex: "^([0-9]+)$"\n'
    )
    return ConfigManager(str(config_file))
