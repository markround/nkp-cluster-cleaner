"""
Tests for the deletion criteria.

`criteria.evaluate` is pure, so these exercise the real decision logic directly
with no API in the way.
"""

import time
from datetime import UTC, datetime, timedelta

import pytest

from nkp_cluster_cleaner.core.criteria import evaluate
from nkp_cluster_cleaner.core.models import ClusterState, DeletionReason
from tests.factories import make_cluster


def ago(**kwargs) -> datetime:
    """An aware UTC timestamp the given interval in the past."""
    return datetime.now(UTC) - timedelta(**kwargs)


class TestManagementCluster:
    def test_management_cluster_is_never_deleted(self, config_manager):
        cluster = make_cluster(name="mdr-mgmt", labels={}, is_management=True)
        verdict = evaluate(cluster, config_manager)
        assert verdict.state is ClusterState.MANAGEMENT
        assert not verdict.should_delete

    def test_management_wins_over_every_other_rule(self, owner_label_config):
        """Non-compliant, expired and past grace, but still never deleted."""
        cluster = make_cluster(
            name="mdr-mgmt",
            labels={"expires": "1h"},
            created=ago(days=400),
            is_management=True,
        )
        verdict = evaluate(cluster, owner_label_config, grace_period="1h")
        assert verdict.state is ClusterState.MANAGEMENT


class TestDeletionInProgress:
    def test_cluster_being_torn_down_is_not_re_issued(self, config_manager):
        """An expired cluster mid-teardown must not be deleted again."""
        cluster = make_cluster(
            labels={"expires": "1d"}, created=ago(days=5), deleting=True
        )
        verdict = evaluate(cluster, config_manager)
        assert verdict.state is ClusterState.DELETING
        assert not verdict.should_delete

    def test_deleting_state_is_reported_before_protection(self, owner_label_config):
        cluster = make_cluster(name="app-prod-eu", deleting=True)
        assert evaluate(cluster, owner_label_config).state is ClusterState.DELETING


class TestNoTarget:
    def test_cluster_without_a_target_is_excluded(self, config_manager):
        """Nothing left to delete, so say so rather than guess."""
        cluster = make_cluster(
            labels={"expires": "1d"}, created=ago(days=5), has_target=False
        )
        verdict = evaluate(cluster, config_manager)
        assert verdict.state is ClusterState.NO_TARGET
        assert not verdict.should_delete


class TestExpiresLabel:
    def test_missing_expires_label_marks_for_deletion(self, config_manager):
        verdict = evaluate(make_cluster(labels={"owner": "mdr"}), config_manager)
        assert verdict.should_delete
        assert verdict.reason is DeletionReason.MISSING_EXPIRES_LABEL

    def test_expired_cluster_is_marked_for_deletion(self, config_manager):
        cluster = make_cluster(labels={"expires": "1d"}, created=ago(days=5))
        verdict = evaluate(cluster, config_manager)
        assert verdict.should_delete
        assert verdict.reason is DeletionReason.EXPIRED
        assert verdict.expires_at is not None

    def test_unexpired_cluster_is_active(self, config_manager):
        created = ago(days=1)
        cluster = make_cluster(labels={"expires": "30d"}, created=created)
        # Pin "now" so the rendered remaining time is exact rather than a
        # fraction of a second short of it.
        verdict = evaluate(
            cluster, config_manager, current_time=created + timedelta(days=1)
        )
        assert verdict.state is ClusterState.ACTIVE
        assert "expires in ~29d" in verdict.detail

    def test_invalid_expires_format_marks_for_deletion(self, config_manager):
        verdict = evaluate(make_cluster(labels={"expires": "soon"}), config_manager)
        assert verdict.should_delete
        assert verdict.reason is DeletionReason.INVALID_EXPIRES_FORMAT

    def test_missing_creation_timestamp_marks_for_deletion(self, config_manager):
        cluster = make_cluster(labels={"expires": "30d"}, created=None)
        verdict = evaluate(cluster, config_manager)
        assert verdict.should_delete
        assert verdict.reason is DeletionReason.MISSING_CREATION_TIMESTAMP

    def test_expiry_boundary_deletes_at_the_moment_it_passes(self, config_manager):
        created = ago(days=1)
        cluster = make_cluster(labels={"expires": "1d"}, created=created)

        just_before = created + timedelta(days=1) - timedelta(seconds=1)
        verdict = evaluate(cluster, config_manager, current_time=just_before)
        assert not verdict.should_delete

        exactly_at = created + timedelta(days=1)
        verdict = evaluate(cluster, config_manager, current_time=exactly_at)
        assert verdict.should_delete


class TestExtraLabels:
    def test_missing_required_label_is_distinguished(self, owner_label_config):
        cluster = make_cluster(labels={"expires": "30d", "cost_centre": "1"})
        verdict = evaluate(cluster, owner_label_config)
        assert verdict.reason is DeletionReason.MISSING_REQUIRED_LABEL

    def test_pattern_mismatch_is_distinguished(self, owner_label_config):
        cluster = make_cluster(
            labels={"expires": "30d", "owner": "mdr", "cost_centre": "nope"}
        )
        verdict = evaluate(cluster, owner_label_config)
        assert verdict.reason is DeletionReason.LABEL_PATTERN_MISMATCH

    def test_every_label_error_is_retained(self, owner_label_config):
        """The verdict reports one reason but keeps the full list for display."""
        cluster = make_cluster(labels={"expires": "30d", "cost_centre": "nope"})
        verdict = evaluate(cluster, owner_label_config)
        assert len(verdict.label_errors) == 2

    def test_compliant_and_unexpired_cluster_is_active(self, owner_label_config):
        cluster = make_cluster(
            labels={"expires": "30d", "owner": "mdr", "cost_centre": "42"},
            created=ago(days=1),
        )
        assert evaluate(cluster, owner_label_config).state is ClusterState.ACTIVE

    def test_label_check_runs_before_the_expiry_check(self, owner_label_config):
        cluster = make_cluster(labels={"expires": "365d"}, created=ago(days=1))
        verdict = evaluate(cluster, owner_label_config)
        assert verdict.reason is DeletionReason.MISSING_REQUIRED_LABEL


class TestProtection:
    def test_protected_name_pattern(self, owner_label_config):
        cluster = make_cluster(name="app-prod-eu", labels={})
        assert evaluate(cluster, owner_label_config).state is ClusterState.PROTECTED

    def test_excluded_namespace_pattern(self, owner_label_config):
        cluster = make_cluster(namespace="default", labels={})
        assert evaluate(cluster, owner_label_config).state is ClusterState.PROTECTED


class TestGracePeriod:
    def test_cluster_inside_grace_period_is_spared(self, config_manager):
        """Grace beats every criteria check, including a missing expires label."""
        created = ago(hours=1)
        cluster = make_cluster(labels={}, created=created)
        verdict = evaluate(
            cluster,
            config_manager,
            grace_period="24h",
            current_time=created + timedelta(hours=1),
        )
        assert verdict.state is ClusterState.IN_GRACE
        assert "ends in ~23h" in verdict.detail

    def test_cluster_past_grace_period_is_evaluated_normally(self, config_manager):
        cluster = make_cluster(labels={}, created=ago(days=2))
        verdict = evaluate(cluster, config_manager, grace_period="24h")
        assert verdict.reason is DeletionReason.MISSING_EXPIRES_LABEL

    def test_no_grace_period_configured_skips_the_check(self, config_manager):
        cluster = make_cluster(labels={}, created=ago(minutes=1))
        assert evaluate(cluster, config_manager).should_delete

    def test_unparseable_grace_period_does_not_protect(self, config_manager):
        """A bad value must not silently protect the whole estate."""
        cluster = make_cluster(labels={}, created=ago(minutes=1))
        assert evaluate(cluster, config_manager, grace_period="nonsense").should_delete

    def test_protection_is_checked_before_grace(self, owner_label_config):
        cluster = make_cluster(name="app-prod-eu", labels={}, created=ago(minutes=1))
        verdict = evaluate(cluster, owner_label_config, grace_period="24h")
        assert verdict.state is ClusterState.PROTECTED


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="TZ manipulation is Unix-only")
class TestTimezoneHandling:
    """
    Timestamps from the Kubernetes API are UTC. Comparing them against a naive
    local `datetime.now()` — as this tool used to — skews every expiry and grace
    decision by the machine's UTC offset.

    These run under a fixed, deliberately extreme timezone so any regression is
    unambiguous rather than dependent on the developer's machine.
    """

    @pytest.fixture(autouse=True)
    def _fixed_timezone(self, monkeypatch):
        """Run each test as if the machine were in UTC+12/+13."""
        monkeypatch.setenv("TZ", "Pacific/Auckland")
        time.tzset()
        yield
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()

    def test_unexpired_cluster_is_not_reported_as_expired(self, config_manager):
        """Created 1h ago with a 2h expiry: roughly an hour left, in any timezone."""
        cluster = make_cluster(labels={"expires": "2h"}, created=ago(hours=1))
        verdict = evaluate(cluster, config_manager)
        assert not verdict.should_delete, (
            f"deleted a cluster with ~1h left: {verdict.detail}"
        )

    def test_new_cluster_is_still_inside_its_grace_period(self, config_manager):
        cluster = make_cluster(labels={}, created=ago(minutes=1))
        verdict = evaluate(cluster, config_manager, grace_period="2h")
        assert verdict.state is ClusterState.IN_GRACE

    def test_expired_cluster_is_still_detected(self, config_manager):
        """The fix must not swing the other way and stop deleting things."""
        cluster = make_cluster(labels={"expires": "1h"}, created=ago(hours=5))
        assert evaluate(cluster, config_manager).should_delete
