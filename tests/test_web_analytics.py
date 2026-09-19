"""
Rendering tests for the Redis-backed pages.

The analytics and notifications templates are the most intricate in the app and
are skipped entirely by the no-Redis smoke tests, so they get their own fakes
here. These render the real templates against realistic payloads, which is what
catches a chart card referencing a key the analytics service does not return.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from nkp_cluster_cleaner.k8s.client import KubernetesClient
from nkp_cluster_cleaner.web.app import create_app
from nkp_cluster_cleaner.web.services import Services
from tests.factories import make_kommander_cluster, make_nkp_cluster, ts
from tests.test_discovery import fake_api

DATES = ["2026-09-16", "2026-09-17", "2026-09-18"]


def analytics_payload():
    """A fake RedisAnalyticsService returning fully-populated payloads."""
    service = MagicMock()

    service.get_cluster_trends.return_value = {
        "dates": DATES,
        "deletion_counts": [2, 1, 3],
        "protected_counts": [8, 9, 9],
        "deleting_counts": [0, 1, 0],
        "total_counts": [10, 11, 12],
        "summary": {
            "current_for_deletion": 3,
            "current_protected": 9,
            "average_daily_deletions": 2.0,
            "trend_direction": "increasing",
        },
    }
    service.get_compliance_stats.return_value = {
        "dates": DATES,
        "compliance_trend": [80.0, 85.0, 90.0],
        "current_compliance": 90.0,
        "summary": {
            "average_compliance": 85.0,
            "compliance_direction": "improving",
            "worst_performing_label": "owner",
        },
    }
    service.get_deletion_activity.return_value = {
        "deletion_reasons": {"Missing expires label": 4, "Cluster expired": 2},
        "summary": {"total_deletion_candidates": 6, "peak_hour": 9},
    }
    service.get_namespace_activity.return_value = {
        "top_namespaces": {
            "kommander-default-workspace": {"total_clusters": 7},
            "kasten-demo": {"total_clusters": 2},
        },
        "summary": {"total_namespaces": 2, "average_clusters_per_namespace": 4.5},
    }
    service.get_owner_distribution.return_value = {
        "top_owners": {"mdr": {"total_clusters": 6}, "ops": {"total_clusters": 3}},
        "summary": {"total_owners": 2, "average_clusters_per_owner": 4.5},
    }
    service.get_expiration_analysis.return_value = {
        "current_distribution": {
            "expired": 2,
            "expires_soon": 1,
            "expires_this_week": 3,
            "expires_this_month": 4,
            "expires_later": 1,
            "no_expiration": 2,
        },
        "summary": {"clusters_without_expiration": 2, "expired_clusters": 2},
    }
    service.get_dashboard_summary.return_value = {
        "current_status": {
            "clusters_for_deletion": 3,
            "clusters_protected": 9,
            "clusters_deleting": 1,
            "api_mode": "nkpcluster",
            "compliance_rate": 90.0,
            "trend_direction": "increasing",
        },
        "week_summary": {"average_deletions": 2.0, "trend_direction": "increasing"},
        "month_summary": {
            "average_deletions": 1.8,
            "compliance_direction": "improving",
        },
    }
    service.get_database_stats.return_value = {
        "total_snapshots": 42,
        "redis_version": "7.2.0",
        "redis_memory_used": "1.5M",
        "redis_connected_clients": 2,
        "redis_uptime_days": 5,
        "earliest_snapshot": "2026-09-16T00:00:00",
        "latest_snapshot": "2026-09-18T12:00:00",
    }
    return service


def history_payload():
    """A fake NotificationHistory with one stored record."""
    history = MagicMock()
    history.get_active_notification_count.return_value = 1
    history.get_all_notified_clusters.return_value = [
        {
            "cluster_name": "expired",
            "namespace": "kommander-default-workspace",
            "severities": ["critical"],
            "ttl_seconds": 86400 * 3,
        }
    ]
    return history


@pytest.fixture
def client():
    """A client with Redis enabled and both storage services faked."""
    api = fake_api(
        kommander=[
            make_kommander_cluster(
                name="expired", labels={"expires": "1d"}, created=ts(timedelta(days=-5))
            ),
            # Deliberately close to expiry, so it lands in the notifications
            # page's warning band rather than being filtered out.
            make_kommander_cluster(
                name="nearly",
                labels={"expires": "10d", "owner": "mdr"},
                created=ts(timedelta(days=-9, hours=-18)),
            ),
        ],
        nkp=[make_nkp_cluster(name="expired"), make_nkp_cluster(name="nearly")],
    )

    with (
        patch.object(KubernetesClient, "_load", lambda self: None),
        patch.object(KubernetesClient, "custom_objects", api),
        patch.object(KubernetesClient, "core_v1", MagicMock()),
        patch.object(Services, "analytics", lambda self: analytics_payload()),
        patch.object(Services, "notification_history", lambda self: history_payload()),
    ):
        app = create_app(no_redis=False)
        app.config["TESTING"] = True
        with app.test_client() as test_client:
            yield test_client


class TestAnalyticsPage:
    def test_renders_with_data(self, client):
        response = client.get("/analytics")
        assert response.status_code == 200
        assert b"Something went wrong" not in response.data

    def test_charts_are_present(self, client):
        body = client.get("/analytics").data.decode()
        for canvas_id in (
            "chart-trends",
            "chart-compliance",
            "chart-reasons",
            "chart-expiration",
            "chart-namespaces",
            "chart-owners",
        ):
            assert f'id="{canvas_id}"' in body, f"missing chart {canvas_id}"

    def test_every_chart_has_a_table_alternative(self, client):
        """
        The palette's contrast check requires relief, and the table is it.
        A chart without one would leave identity resting on colour.
        """
        body = client.get("/analytics").data.decode()
        assert body.count("data-table-toggle=") == 6

    def test_assets_are_served_locally(self, client):
        """Air-gapped clusters cannot reach a CDN."""
        body = client.get("/analytics").data.decode()
        assert "/static/vendor/chart.umd.min.js" in body
        assert "cdnjs" not in body
        assert "cdn.jsdelivr" not in body

    def test_stat_tiles_include_the_deleting_count(self, client):
        body = client.get("/analytics").data.decode()
        assert "Deleting" in body

    def test_table_rows_render_from_the_payload(self, client):
        body = client.get("/analytics").data.decode()
        # dict_rows sorts largest first, so this reason leads its table.
        assert "Missing expires label" in body
        assert "kommander-default-workspace" in body

    def test_deletion_reasons_render_as_a_donut(self, client):
        """The ring's legend spells out every slice, which is what makes the
        sub-3:1 slice colours legal."""
        body = client.get("/analytics").data.decode()
        assert body.count("donut__row") == 2  # the fake carries two reasons
        assert "Cluster expired" in body
        assert "Missing expires label" in body


class TestReasonSlices:
    """
    The donut's colours are keyed to the reason, never to its rank: a reason
    that slips from first to third keeps its colour, and the ring keeps its
    order. The palette was validated all-pairs on that promise.
    """

    @staticmethod
    def slices(client, mapping):
        return client.application.jinja_env.filters["reason_slices"](mapping)

    def test_order_follows_the_enum_not_the_counts(self, client):
        rows = self.slices(client, {"Missing required label": 9, "Cluster expired": 1})
        assert [row["label"] for row in rows] == [
            "Cluster expired",
            "Missing required label",
        ]

    def test_colour_survives_a_change_of_rank(self, client):
        busy = self.slices(client, {"Cluster expired": 40, "Label pattern mismatch": 1})
        quiet = self.slices(
            client, {"Cluster expired": 1, "Label pattern mismatch": 40}
        )
        assert {row["label"]: row["color"] for row in busy} == {
            row["label"]: row["color"] for row in quiet
        }

    def test_colours_are_distinct(self, client):
        rows = self.slices(
            client,
            {
                "Cluster expired": 1,
                "Missing expires label": 1,
                "Missing required label": 1,
                "Label pattern mismatch": 1,
                "Invalid expires format": 1,
                "Missing creation timestamp": 1,
            },
        )
        assert len({row["color"] for row in rows}) == 6

    def test_shares_are_percentages_of_the_whole(self, client):
        rows = self.slices(client, {"Cluster expired": 3, "Missing expires label": 1})
        assert [row["share"] for row in rows] == [75, 25]

    def test_zero_counts_are_not_drawn(self, client):
        """A zero-width arc is not a slice."""
        rows = self.slices(client, {"Cluster expired": 2, "Missing expires label": 0})
        assert [row["label"] for row in rows] == ["Cluster expired"]

    def test_unknown_reasons_still_appear(self, client):
        """A reason the enum no longer carries is drawn neutral, not dropped."""
        rows = self.slices(client, {"Some retired reason": 5})
        assert [row["label"] for row in rows] == ["Some retired reason"]
        assert rows[0]["color"] == "#5c6b7a"

    def test_no_data_is_no_slices(self, client):
        assert self.slices(client, {}) == []
        assert self.slices(client, None) == []


class TestNotificationsPage:
    def test_renders_with_data(self, client):
        response = client.get("/notifications")
        assert response.status_code == 200
        assert b"Something went wrong" not in response.data

    def test_history_records_are_listed(self, client):
        body = client.get("/notifications").data.decode()
        assert "expired" in body
        assert "js-clear" in body


class TestStaticAssets:
    def test_stylesheet_is_served(self, client):
        response = client.get("/static/css/app.css")
        assert response.status_code == 200
        assert b"--sidebar-bg" in response.data

    def test_vendored_chart_library_is_served(self, client):
        response = client.get("/static/vendor/chart.umd.min.js")
        assert response.status_code == 200
        assert len(response.data) > 100_000

    def test_chart_helpers_are_served(self, client):
        response = client.get("/static/js/charts.js")
        assert response.status_code == 200
        assert b"NkpCharts" in response.data


class TestPayloadContract:
    """
    The fakes above are only useful if they match what the real service
    returns. A drifted key would otherwise let a chart render empty in
    production while the tests stayed green — which is exactly what happened
    with `compliance_trend`.
    """

    PAYLOADS = {
        "get_cluster_trends": (7,),
        "get_compliance_stats": (30,),
        "get_deletion_activity": (14,),
        "get_namespace_activity": (30,),
        "get_owner_distribution": (30,),
        "get_expiration_analysis": (30,),
    }

    def test_fake_keys_match_the_real_service(self):
        """Every top-level key the fake returns must exist on the real one."""
        from unittest.mock import MagicMock as MM

        from nkp_cluster_cleaner.storage.analytics import RedisAnalyticsService

        real = RedisAnalyticsService.__new__(RedisAnalyticsService)
        # No snapshots stored: exercises each method's empty-data branch, which
        # is where the key set is declared.
        real.redis_client = MM()
        real.redis_client.zrangebyscore.return_value = []
        real.redis_client.zrange.return_value = []
        real.redis_client.get.return_value = None

        fake = analytics_payload()

        for method, args in self.PAYLOADS.items():
            real_keys = set(getattr(real, method)(*args))
            fake_keys = set(getattr(fake, method)(*args))
            unknown = fake_keys - real_keys
            assert not unknown, (
                f"{method}: the fake returns {sorted(unknown)}, which the real "
                f"service does not. Real keys: {sorted(real_keys)}"
            )
