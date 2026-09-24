"""
The analytics write path and read path, checked against each other.

tests/test_analytics_snapshot.py covers how a snapshot is *built*, and
tests/test_web_analytics.py renders the templates against a hand-written fake
service. Between them sits everything that actually stores and retrieves the
data - the Redis keys, the sorted-set indexes, the TTLs, and every query in
storage/analytics.py - which nothing exercised: the analytics service was the
least-covered module in the project.

These tests close that loop. misc/generate_analytics_data.py writes a history
through the real RedisDataCollector, and every assertion below compares what
the service reads back against the snapshots that were written, rather than
against numbers typed out here. A change to either side that is not matched by
the other shows up as a failure.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import generate_analytics_data as generator
import pytest

from nkp_cluster_cleaner.core.config import ConfigManager
from nkp_cluster_cleaner.core.models import UNKNOWN_OWNER, DeletionReason
from nkp_cluster_cleaner.storage.analytics import RedisAnalyticsService
from nkp_cluster_cleaner.storage.collector import RedisDataCollector
from nkp_cluster_cleaner.storage.notification_history import NotificationHistory
from tests.conftest import install_fake_redis

pytestmark = pytest.mark.integration

#: A fortnight at twelve-hour intervals: enough days for the trend and
#: compliance directions to be computed (both need seven), enough snapshots per
#: day for the "latest wins" and "average across the day" rules to differ from
#: simply taking every snapshot, and small enough to generate in a moment.
DAYS = 14
INTERVAL_HOURS = 12
CLUSTERS = 60
RETENTION_DAYS = 90
SEED = 1979

#: Queried windows are a day wider than the generated history, so the oldest
#: snapshot cannot fall outside the cutoff on a rounding boundary.
WINDOW = DAYS + 1


class History:
    """A generated analytics history, and the service that reads it back."""

    def __init__(self, snapshots: list[dict], service: RedisAnalyticsService):
        self.snapshots = snapshots
        self.service = service

    @property
    def latest(self) -> dict:
        """The most recent snapshot written."""
        return self.snapshots[-1]

    def by_day(self) -> dict[str, dict]:
        """
        The last snapshot of each day, which is what the trend queries chart.

        Snapshots arrive oldest first, so each day's later entries overwrite
        its earlier ones.
        """
        return {snapshot["timestamp"][:10]: snapshot for snapshot in self.snapshots}

    def daily_compliance(self) -> dict[str, float]:
        """Each day's mean compliance rate, which is what the service reports."""
        rates: dict[str, list[float]] = {}
        for snapshot in self.snapshots:
            date = snapshot["timestamp"][:10]
            rate = snapshot["label_compliance"]["overall_compliance_rate"]
            rates.setdefault(date, []).append(rate)
        return {date: sum(v) / len(v) for date, v in rates.items()}


@pytest.fixture(scope="module")
def history(criteria_config) -> History:
    """
    A fortnight of snapshots in Redis, written the way the CronJob writes them.

    Module-scoped because generating it is the expensive part; the tests below
    only read, so they cannot disturb each other.
    """
    with pytest.MonkeyPatch.context() as monkeypatch:
        settings = install_fake_redis(monkeypatch)

        end = datetime.now(UTC)
        start = end - timedelta(days=DAYS)
        timestamps = []
        when = start
        while when <= end:
            timestamps.append(when)
            when += timedelta(hours=INTERVAL_HOURS)

        config_manager = ConfigManager(criteria_config)
        lives = generator.build_estate(random.Random(SEED), CLUSTERS, start, end)
        manager = generator.SimulatedClusterManager(lives, config_manager)
        collector = RedisDataCollector(
            config_manager=config_manager,
            redis=settings,
            cluster_manager=manager,
        )

        snapshots = generator.generate(
            collector, manager, timestamps, RETENTION_DAYS, quiet=True
        )

        # Built inside the patch context, and handed out still connected to the
        # in-memory server, so the tests read through the real service code.
        yield History(snapshots, RedisAnalyticsService(settings))


@pytest.fixture(scope="module")
def service(history) -> RedisAnalyticsService:
    """The analytics service, reading the generated history."""
    return history.service


class TestGeneratedHistory:
    """The fixture is only useful if it produced a varied estate."""

    def test_the_history_is_the_expected_shape(self, history):
        assert len(history.snapshots) == DAYS * (24 // INTERVAL_HOURS) + 1
        assert len(history.by_day()) == DAYS + 1

    def test_the_estate_is_not_uniform(self, history):
        """
        Every assertion below would pass vacuously against a flat history, so
        this pins that the generator is producing something worth querying.
        """
        totals = {s["cluster_counts"]["total"] for s in history.snapshots}
        deletions = {s["cluster_counts"]["for_deletion"] for s in history.snapshots}
        assert len(totals) > 1
        assert len(deletions) > 1
        assert min(deletions) > 0


class TestStorage:
    def test_every_snapshot_written_is_indexed(self, history, service):
        assert service.get_database_stats()["total_snapshots"] == len(history.snapshots)

    def test_the_latest_snapshot_is_the_last_one_written(self, history, service):
        assert service.get_latest_snapshot() == history.latest

    def test_database_stats_report_the_server(self, service):
        stats = service.get_database_stats()
        assert "error" not in stats
        assert stats["earliest_snapshot"] < stats["latest_snapshot"]


class TestClusterTrends:
    def test_one_point_per_day(self, history, service):
        trends = service.get_cluster_trends(WINDOW)
        assert trends["dates"] == sorted(history.by_day())

    def test_each_series_matches_the_day_it_came_from(self, history, service):
        trends = service.get_cluster_trends(WINDOW)
        by_day = history.by_day()

        for index, date in enumerate(trends["dates"]):
            counts = by_day[date]["cluster_counts"]
            assert trends["deletion_counts"][index] == counts["for_deletion"]
            assert trends["protected_counts"][index] == counts["protected"]
            assert trends["deleting_counts"][index] == counts["deleting"]
            assert trends["total_counts"][index] == counts["total"]

    def test_the_summary_describes_the_series(self, history, service):
        trends = service.get_cluster_trends(WINDOW)
        counts = trends["deletion_counts"]

        assert trends["summary"]["current_for_deletion"] == counts[-1]
        assert trends["summary"]["current_protected"] == trends["protected_counts"][-1]
        assert trends["summary"]["average_daily_deletions"] == sum(counts) / len(counts)
        assert trends["summary"]["trend_direction"] in (
            "increasing",
            "decreasing",
            "stable",
        )

    def test_a_shorter_window_returns_fewer_days(self, service):
        assert len(service.get_cluster_trends(3)["dates"]) < len(
            service.get_cluster_trends(WINDOW)["dates"]
        )


class TestCompliance:
    def test_the_trend_is_each_days_average(self, history, service):
        stats = service.get_compliance_stats(WINDOW)
        expected = history.daily_compliance()

        assert stats["dates"] == sorted(expected)
        for index, date in enumerate(stats["dates"]):
            assert stats["compliance_trend"][index] == pytest.approx(expected[date])

    def test_current_compliance_is_the_most_recent_day(self, service):
        stats = service.get_compliance_stats(WINDOW)
        assert stats["current_compliance"] == stats["compliance_trend"][-1]

    def test_a_trend_is_tracked_for_every_required_label(self, history, service):
        """
        The required labels come from config.yaml plus the implicit `expires`,
        and the chart draws one series per label. A label the config requires
        but the snapshot never records would leave a gap in the UI.
        """
        stats = service.get_compliance_stats(WINDOW)
        required = set(history.latest["label_compliance"]["required_labels"])

        assert required == {"owner", "expires"}
        assert set(stats["label_trends"]) == required
        for trend in stats["label_trends"].values():
            assert len(trend) == len(stats["dates"])

    def test_the_worst_label_is_one_of_the_labels(self, service):
        stats = service.get_compliance_stats(WINDOW)
        assert stats["summary"]["worst_performing_label"] in stats["label_trends"]


class TestDeletionActivity:
    def test_reasons_are_reported_under_their_enum_label(self, service):
        """
        These strings reach the dashboard directly. They must come from
        DeletionReason rather than from message text, which is what they used
        to be derived from.
        """
        activity = service.get_deletion_activity(WINDOW)
        known = {reason.label for reason in DeletionReason}

        assert activity["deletion_reasons"]
        assert set(activity["deletion_reasons"]) <= known

    def test_pre_1_0_reason_keys_fold_into_the_enum_label(self, monkeypatch):
        """
        Snapshots from before 1.0 keyed reasons in Title Case. Left as they
        were, they missed the donut's colour lookup and all drew in the neutral,
        and a window spanning the upgrade listed each reason twice.
        """
        monkeypatch.setattr(RedisAnalyticsService, "__init__", lambda self: None)
        legacy = RedisAnalyticsService()
        snapshots = [
            {
                "timestamp": "2026-01-01T00:00:00",
                "cluster_counts": {"for_deletion": 3},
                "deletion_reasons": {"Missing Expires Label": 2, "Cluster Expired": 1},
            },
            {
                "timestamp": "2026-01-02T00:00:00",
                "cluster_counts": {"for_deletion": 1},
                "deletion_reasons": {"Cluster expired": 1, "Other": 1},
            },
        ]
        monkeypatch.setattr(legacy, "_get_historical_data", lambda days: snapshots)

        assert legacy.get_deletion_activity(WINDOW)["deletion_reasons"] == {
            DeletionReason.MISSING_EXPIRES_LABEL.label: 2,
            DeletionReason.EXPIRED.label: 2,
            "Other": 1,
        }

    def test_the_total_counts_every_reason(self, service):
        activity = service.get_deletion_activity(WINDOW)
        # Only the top five reasons are charted, so the total may exceed their
        # sum; it must never be less.
        assert activity["summary"]["total_deletion_candidates"] >= sum(
            activity["deletion_reasons"].values()
        )

    def test_the_hourly_distribution_covers_the_whole_clock(self, service):
        activity = service.get_deletion_activity(WINDOW)
        assert len(activity["hourly_distribution"]) == 24
        assert 0 <= activity["summary"]["peak_hour"] <= 23


class TestNamespacesAndOwners:
    def test_namespaces_come_from_the_generated_estate(self, history, service):
        activity = service.get_namespace_activity(WINDOW)
        assert set(activity["top_namespaces"]) <= set(
            history.latest["clusters_by_namespace"]
        ) | set(generator.NAMESPACES)

    def test_the_busiest_namespace_leads_the_ranking(self, service):
        activity = service.get_namespace_activity(WINDOW)
        ranked = list(activity["top_namespaces"])
        assert activity["summary"]["most_active_namespace"] == ranked[0]

    def test_unlabelled_clusters_are_counted_under_the_unknown_owner(self, service):
        """
        Regression test for a figure that was always zero: the summary looked
        up "no-owner", a key nothing ever wrote, while the snapshots group
        unowned clusters under Cluster.owner - which is UNKNOWN_OWNER.
        """
        owners = service.get_owner_distribution(WINDOW)

        assert UNKNOWN_OWNER in owners["owner_stats"]
        assert (
            owners["summary"]["no_owner_clusters"]
            == owners["owner_stats"][UNKNOWN_OWNER]["total_clusters"]
        )
        assert owners["summary"]["no_owner_clusters"] > 0

    def test_owners_come_from_the_generated_estate(self, service):
        owners = service.get_owner_distribution(WINDOW)
        assert set(owners["owner_stats"]) <= set(generator.OWNERS) | {UNKNOWN_OWNER}


class TestExpiration:
    def test_the_current_distribution_is_the_latest_snapshots_buckets(
        self, history, service
    ):
        analysis = service.get_expiration_analysis(WINDOW)
        assert (
            analysis["current_distribution"]
            == history.latest["expiration_analysis"]["buckets"]
        )

    def test_common_patterns_are_expires_label_values(self, service):
        analysis = service.get_expiration_analysis(WINDOW)
        known = set(generator.EXPIRES_VALUES) | set(generator.BAD_EXPIRES_VALUES)
        assert analysis["common_expiration_patterns"]
        assert set(analysis["common_expiration_patterns"]) <= known

    def test_the_summary_reads_from_the_current_distribution(self, service):
        analysis = service.get_expiration_analysis(WINDOW)
        distribution = analysis["current_distribution"]
        assert analysis["summary"]["expired_clusters"] == distribution["expired"]
        assert (
            analysis["summary"]["clusters_without_expiration"]
            == distribution["no_expiration"]
        )


class TestDashboardSummary:
    def test_it_succeeds_with_real_data(self, service):
        """The whole method is wrapped in a try/except that hides any failure."""
        assert "error" not in service.get_dashboard_summary()

    def test_it_reports_the_estate_as_the_latest_snapshot_saw_it(
        self, history, service
    ):
        status = service.get_dashboard_summary()["current_status"]
        counts = history.latest["cluster_counts"]

        assert status["clusters_deleting"] == counts["deleting"]
        assert status["api_mode"] == "nkpcluster"
        assert status["compliance_rate"] == pytest.approx(
            service.get_compliance_stats(7)["current_compliance"]
        )


class TestPayloadShape:
    """
    Every query must return the same top-level keys whether or not it found
    data. The templates read those keys unconditionally, so a method that grows
    a key only on its populated branch renders an empty chart in production
    while tests against the empty branch - which is all
    tests/test_web_analytics.py has - stay green.
    """

    QUERIES = {
        "get_cluster_trends": (WINDOW,),
        "get_compliance_stats": (WINDOW,),
        "get_deletion_activity": (WINDOW,),
        "get_namespace_activity": (WINDOW,),
        "get_owner_distribution": (WINDOW,),
        "get_expiration_analysis": (WINDOW,),
    }

    @pytest.fixture
    def empty(self, fake_redis) -> RedisAnalyticsService:
        """The same service, against a Redis holding nothing."""
        return RedisAnalyticsService(fake_redis)

    @pytest.mark.parametrize("method", sorted(QUERIES))
    def test_populated_and_empty_results_agree(self, service, empty, method):
        args = self.QUERIES[method]
        with_data = getattr(service, method)(*args)
        without_data = getattr(empty, method)(*args)

        assert set(with_data) == set(without_data)
        assert set(with_data["summary"]) == set(without_data["summary"])


class TestRetention:
    """
    Redis TTLs expire the snapshot payloads, but the sorted-set indexes have no
    TTL of their own and have to be pruned explicitly.
    """

    def collect_over(self, collector, days: int, retention_days: int) -> list[str]:
        """Take one snapshot a day for `days` days, and return the keys written."""
        from nkp_cluster_cleaner.storage import collector as collector_module

        now = datetime.now(UTC)
        original = collector_module.now
        try:
            for age in range(days, 0, -1):
                when = now - timedelta(days=age)
                collector.cluster_manager.when = when
                collector_module.now = lambda when=when: when
                collector.collect_snapshot(retention_days)
        finally:
            collector_module.now = original

        return collector.redis_client.zrange("analytics:snapshots:index", 0, -1)

    def test_snapshots_outside_the_window_leave_no_index_entry(
        self, fake_redis, criteria_config
    ):
        config_manager = ConfigManager(criteria_config)
        lives = generator.build_estate(
            random.Random(SEED),
            5,
            datetime.now(UTC) - timedelta(days=30),
            datetime.now(UTC),
        )
        collector = RedisDataCollector(
            config_manager=config_manager,
            redis=fake_redis,
            cluster_manager=generator.SimulatedClusterManager(lives, config_manager),
        )

        keys = self.collect_over(collector, days=10, retention_days=3)

        # Ten snapshots written, one a day; only those inside the three-day
        # retention window should still be indexed.
        assert 0 < len(keys) <= 4
        assert collector.redis_client.zcard("analytics:summaries:index") == len(keys)


class TestNotificationHistory:
    """The other Redis-backed store, and the one behind the notifications page."""

    def test_a_cluster_is_only_notified_once_per_severity(self, fake_redis):
        history = NotificationHistory(fake_redis)

        assert not history.has_been_notified("demo", "team-alpha", "warning")
        history.mark_as_notified("demo", "team-alpha", "warning")

        assert history.has_been_notified("demo", "team-alpha", "warning")
        assert not history.has_been_notified("demo", "team-alpha", "critical")

    def test_severities_accumulate_on_one_record(self, fake_redis):
        history = NotificationHistory(fake_redis)
        history.mark_as_notified("demo", "team-alpha", "warning")
        history.mark_as_notified("demo", "team-alpha", "critical")

        record = history.get_all_notified_clusters()[0]
        assert record["cluster_name"] == "demo"
        assert record["namespace"] == "team-alpha"
        assert sorted(record["severities"]) == ["critical", "warning"]

    def test_records_carry_the_retention_ttl(self, fake_redis):
        history = NotificationHistory(fake_redis)
        history.mark_as_notified("demo", "team-alpha", "warning", ttl_days=2)

        assert 0 < history.get_all_notified_clusters()[0]["ttl_seconds"] <= 2 * 86400

    def test_clearing_a_cluster_removes_its_record(self, fake_redis):
        history = NotificationHistory(fake_redis)
        history.mark_as_notified("demo", "team-alpha", "warning")
        history.mark_as_notified("other", "team-beta", "critical")

        assert history.clear_cluster_history("demo", "team-alpha") is True
        assert history.get_active_notification_count() == 1
        assert not history.has_been_notified("demo", "team-alpha", "warning")

    def test_clearing_an_unknown_cluster_is_not_an_error(self, fake_redis):
        assert NotificationHistory(fake_redis).clear_cluster_history("ghost", "ns") is (
            False
        )

    def test_records_without_an_expiry_are_given_one(self, fake_redis):
        """
        A record written before TTLs were set would otherwise live forever, and
        keep suppressing alerts for a cluster that is long gone.
        """
        history = NotificationHistory(fake_redis)
        history.redis_client.sadd("notifications:cluster:team-alpha:legacy", "warning")
        assert history.redis_client.ttl("notifications:cluster:team-alpha:legacy") == -1

        history.clear_expired_notifications()

        assert history.redis_client.ttl("notifications:cluster:team-alpha:legacy") > 0
