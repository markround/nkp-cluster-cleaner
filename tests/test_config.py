"""Tests for configuration parsing and rule evaluation."""

import pytest

from nkp_cluster_cleaner.config import ConfigManager, ExtraLabel


class TestExtraLabel:
    def test_no_regex_accepts_anything(self):
        label = ExtraLabel(name="owner")
        assert label.validate_value("anything at all")
        assert label.validate_value("")

    def test_regex_is_enforced(self):
        label = ExtraLabel(name="cost_centre", regex="^([0-9]+)$")
        assert label.validate_value("12345")
        assert not label.validate_value("abc")
        assert not label.validate_value("123a")

    def test_invalid_regex_falls_back_to_accepting(self):
        """A broken pattern in config must not hard-fail the whole run."""
        label = ExtraLabel(name="broken", regex="[unclosed")
        assert label.validate_value("anything")

    def test_regex_is_anchored_at_the_start_only(self):
        """re.match semantics: the pattern anchors at the start, not the end."""
        label = ExtraLabel(name="env", regex="^(dev|prod)")
        assert label.validate_value("prod-extra")


class TestProtection:
    def test_no_rules_protects_nothing(self, config_manager):
        assert not config_manager.is_cluster_protected("anything", "anywhere")

    def test_cluster_name_pattern(self, owner_label_config):
        assert owner_label_config.is_cluster_protected("app-prod-eu", "ns")
        assert not owner_label_config.is_cluster_protected("app-dev-eu", "ns")

    def test_namespace_pattern(self, owner_label_config):
        assert owner_label_config.is_cluster_protected("anything", "default")
        assert not owner_label_config.is_cluster_protected("anything", "default-ish-x")


class TestExtraLabelValidation:
    def test_no_required_labels_means_no_errors(self, config_manager):
        assert config_manager.validate_extra_labels({}) == []

    def test_missing_required_label_is_reported(self, owner_label_config):
        errors = owner_label_config.validate_extra_labels({"cost_centre": "1"})
        assert errors == ["Missing required label 'owner'"]

    def test_pattern_mismatch_is_reported(self, owner_label_config):
        errors = owner_label_config.validate_extra_labels(
            {"owner": "mdr", "cost_centre": "not-a-number"}
        )
        assert len(errors) == 1
        assert "does not match pattern" in errors[0]

    def test_all_present_and_valid(self, owner_label_config):
        assert (
            owner_label_config.validate_extra_labels(
                {"owner": "mdr", "cost_centre": "42"}
            )
            == []
        )

    def test_every_problem_is_reported(self, owner_label_config):
        errors = owner_label_config.validate_extra_labels({"cost_centre": "nope"})
        assert len(errors) == 2


class TestConfigLoading:
    def test_missing_file_raises(self):
        with pytest.raises(Exception, match="Failed to load config file"):
            ConfigManager("/nonexistent/config.yaml")

    def test_empty_sections_default_to_empty_lists(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("extra_labels: []\n")
        criteria = ConfigManager(str(config_file)).get_criteria()
        assert criteria.protected_cluster_patterns == []
        assert criteria.excluded_namespace_patterns == []
        assert criteria.extra_labels == []

    def test_extra_label_entries_without_a_name_are_skipped(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("extra_labels:\n- description: no name here\n")
        assert ConfigManager(str(config_file)).get_criteria().extra_labels == []

    def test_generated_example_config_is_loadable(self, tmp_path):
        """The example we hand users must actually parse."""
        output = tmp_path / "example.yaml"
        ConfigManager().save_example_config(str(output))

        criteria = ConfigManager(str(output)).get_criteria()
        assert criteria.protected_cluster_patterns
        assert {label.name for label in criteria.extra_labels} == {
            "owner",
            "cost_centre",
            "project",
            "environment",
        }
