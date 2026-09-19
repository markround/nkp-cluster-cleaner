"""
Tests for the analytics snapshot builder.

This code previously derived its buckets by pattern-matching the text of the
reason message - including testing `f"~{i}d" in reason` to guess how soon a
cluster expired. These tests pin the replacement, which works off the computed
expiry time and the state enum.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from nkp_cluster_cleaner.core.config import ConfigManager
from nkp_cluster_cleaner.core.criteria import evaluate
from nkp_cluster_cleaner.core.models import ClusterStatus
from nkp_cluster_cleaner.storage.collector import RedisDataCollector
from tests.factories import make_cluster

NOW = datetime(2026, 9, 18, 12, 0, 0, tzinfo=UTC)


def status_for(config_manager=None, **cluster_kwargs) -> ClusterStatus:
    """Build a ClusterStatus by running the real criteria over a cluster."""
    config_manager = config_manager or ConfigManager()
    cluster = make_cluster(**cluster_kwargs)
    return ClusterStatus(
        cluster=cluster,
        verdict=evaluate(cluster, config_manager, current_time=NOW),
    )


@pytest.fixture
def collector():
    """A collector with Redis and Kubernetes stubbed out."""
    instance = RedisDataCollector.__new__(RedisDataCollector)
    instance.debug = False
    instance.redis_client = MagicMock()
    instance.config_manager = ConfigManager()
    instance.cluster_manager = MagicMock()
    instance.cluster_manager.get_nkp_version.return_value = "v2.18.0"
    instance.cluster_manager.api_mode = "nkpcluster"
    return instance


class TestExpirationAnalysis:
    @pytest.mark.parametrize(
        "expires,age,expected_bucket",
        [
            ("30d", timedelta(days=40), "expired"),
            ("30d", timedelta(days=29, hours=12), "expires_soon"),
            ("30d", timedelta(days=25), "expires_this_week"),
            ("90d", timedelta(days=70), "expires_this_month"),
            ("365d", timedelta(days=1), "expires_later"),
        ],
    )
    def test_clusters_land_in_the_right_bucket(
        self, collector, expires, age, expected_bucket
    ):
        status = status_for(labels={"expires": expires}, created=NOW - age)
        analysis = collector._analyze_expiration([status], NOW)
        assert analysis["buckets"][expected_bucket] == 1

    def test_cluster_without_an_expires_label(self, collector):
        status = status_for(labels={})
        analysis = collector._analyze_expiration([status], NOW)
        assert analysis["buckets"]["no_expiration"] == 1
        assert analysis["total_without_expires"] == 1

    def test_common_values_are_counted(self, collector):
        statuses = [
            status_for(name=f"c{i}", labels={"expires": "30d"}, created=NOW)
            for i in range(3)
        ] + [status_for(name="other", labels={"expires": "7d"}, created=NOW)]

        analysis = collector._analyze_expiration(statuses, NOW)
        assert analysis["common_expires_values"] == {"30d": 3, "7d": 1}


class TestDeletionReasons:
    def test_reasons_are_counted_by_enum_label(self, collector):
        statuses = [
            status_for(name="a", labels={}),
            status_for(name="b", labels={}),
            status_for(
                name="c", labels={"expires": "1d"}, created=NOW - timedelta(days=5)
            ),
        ]
        assert collector._analyze_deletion_reasons(statuses) == {
            "Missing expires label": 2,
            "Cluster expired": 1,
        }

    def test_non_deleted_clusters_are_not_counted(self, collector):
        statuses = [status_for(labels={"expires": "365d"}, created=NOW)]
        assert collector._analyze_deletion_reasons(statuses) == {}


class TestProtectionRules:
    def test_exclusions_are_counted_by_state(self, collector):
        statuses = [
            status_for(name="mgmt", labels={}, is_management=True),
            status_for(name="ok", labels={"expires": "365d"}, created=NOW),
            status_for(name="gone", labels={"expires": "365d"}, deleting=True),
        ]
        assert collector._analyze_protection_rules(statuses) == {
            "Management": 1,
            "Active": 1,
            "Deleting": 1,
        }


class TestAgeDistribution:
    @pytest.mark.parametrize(
        "age,expected",
        [
            (timedelta(hours=6), "0-1_days"),
            (timedelta(days=4), "1-7_days"),
            (timedelta(days=20), "1-4_weeks"),
            (timedelta(days=200), "1-12_months"),
            (timedelta(days=500), "over_1_year"),
        ],
    )
    def test_buckets(self, collector, age, expected):
        status = status_for(created=NOW - age)
        assert collector._calculate_age_distribution([status], NOW)[expected] == 1

    def test_missing_creation_time(self, collector):
        status = status_for(created=None)
        buckets = collector._calculate_age_distribution([status], NOW)
        assert buckets["unknown_age"] == 1


class TestSnapshot:
    def test_counts_split_deleting_out_from_protected(self, collector):
        statuses = [
            status_for(name="doomed", labels={}),
            status_for(name="gone", labels={"expires": "365d"}, deleting=True),
            status_for(name="ok", labels={"expires": "365d"}, created=NOW),
        ]
        snapshot = collector._build_snapshot_data(statuses, NOW)

        counts = snapshot["cluster_counts"]
        assert counts["total"] == 3
        assert counts["for_deletion"] == 1
        assert counts["deleting"] == 1
        # "protected" means everything not queued for deletion, as the existing
        # dashboards have always charted it.
        assert counts["protected"] == 2

    def test_api_mode_is_recorded(self, collector):
        snapshot = collector._build_snapshot_data([status_for()], NOW)
        assert snapshot["collection_metadata"]["api_mode"] == "nkpcluster"

    def test_empty_estate_does_not_divide_by_zero(self, collector):
        snapshot = collector._build_snapshot_data([], NOW)
        assert snapshot["cluster_counts"]["total"] == 0
        assert snapshot["label_compliance"]["overall_compliance_rate"] == 0

    def test_label_compliance(self, collector):
        collector.config_manager = ConfigManager()
        statuses = [
            status_for(name="a", labels={"expires": "365d"}, created=NOW),
            status_for(name="b", labels={}),
        ]
        compliance = collector._calculate_label_compliance(statuses)
        assert compliance["fully_compliant"] == 1
        assert compliance["overall_compliance_rate"] == 50.0
