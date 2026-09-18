"""
Smoke tests for the web UI.

These render the real templates against a faked Kubernetes API, which is what
catches a template still referencing a field the model no longer has.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from nkp_cluster_cleaner.k8s.client import KubernetesClient
from nkp_cluster_cleaner.web.app import create_app
from tests.factories import (
    MANAGEMENT_NAMESPACE,
    make_capi_cluster,
    make_kommander_cluster,
    make_nkp_cluster,
    ts,
)
from tests.test_discovery import fake_api


@pytest.fixture
def api():
    """An estate covering every state the cluster pages render."""
    return fake_api(
        kommander=[
            make_kommander_cluster(
                name="expired", labels={"expires": "1d"}, created=ts(timedelta(days=-5))
            ),
            make_kommander_cluster(
                name="healthy",
                labels={"expires": "365d", "owner": "mdr"},
                created=ts(timedelta(days=-1)),
            ),
            make_kommander_cluster(
                name="tearing-down",
                labels={"expires": "1d"},
                created=ts(timedelta(days=-5)),
            ),
            make_kommander_cluster(
                name="mdr-mgmt", namespace=MANAGEMENT_NAMESPACE, labels={}
            ),
        ],
        nkp=[
            make_nkp_cluster(name="expired"),
            make_nkp_cluster(name="healthy"),
            make_nkp_cluster(name="tearing-down", deleting=True),
            make_nkp_cluster(name="mdr-mgmt", namespace=MANAGEMENT_NAMESPACE),
        ],
        capi=[make_capi_cluster(name="expired"), make_capi_cluster(name="healthy")],
    )


@pytest.fixture
def client(api):
    """A Flask test client whose ClusterManager talks to the fake API."""

    with (
        patch.object(KubernetesClient, "_load", lambda self: None),
        patch.object(KubernetesClient, "custom_objects", api),
        patch.object(KubernetesClient, "core_v1", MagicMock()),
    ):
        app = create_app(no_redis=True)
        app.config["TESTING"] = True
        with app.test_client() as client:
            yield client


class TestPages:
    def test_dashboard_renders(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert b"NKP Cluster Cleaner" in response.data

    def test_dashboard_reports_the_api_mode(self, client):
        response = client.get("/")
        assert b"NKPCluster" in response.data

    def test_clusters_page_lists_each_group(self, client):
        response = client.get("/clusters")
        assert response.status_code == 200

        body = response.data.decode()
        assert "expired" in body
        assert "Deletion in Progress" in body
        assert "tearing-down" in body
        # The management cluster must show up as excluded, never as deletable.
        assert "mdr-mgmt" in body

    def test_clusters_page_names_the_deletion_target(self, client):
        """Operators need to see whether an NKPCluster or a CAPI Cluster goes."""
        body = client.get("/clusters").data.decode()
        assert "NKPCluster" in body

    def test_rules_page_renders(self, client):
        assert client.get("/rules").status_code == 200

    def test_health_reports_the_api_mode(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.get_json()["api_mode"] == "nkpcluster"

    def test_metrics_endpoint_renders(self, client):
        response = client.get("/metrics")
        assert response.status_code == 200
        assert b"nkp_cluster_cleaner_info" in response.data

    def test_analytics_is_disabled_without_redis(self, client):
        response = client.get("/analytics")
        assert response.status_code == 200
        assert b"disabled" in response.data.lower()


class TestUrlPrefix:
    def test_routes_are_served_under_the_prefix(self, api):
        with (
            patch.object(KubernetesClient, "_load", lambda self: None),
            patch.object(KubernetesClient, "custom_objects", api),
            patch.object(KubernetesClient, "core_v1", MagicMock()),
        ):
            app = create_app(url_prefix="/foo", no_redis=True)
            app.config["TESTING"] = True
            with app.test_client() as client:
                assert client.get("/foo/clusters").status_code == 200
                assert client.get("/clusters").status_code == 404


class TestApiRoutes:
    """
    The API blueprint mounts at /api, with the configured prefix in front. The
    page JavaScript builds these URLs, so the paths are part of the contract.
    """

    def test_job_logs_requires_a_job_name(self, client):
        response = client.get("/api/job-logs")
        assert response.status_code == 400
        assert "job_name" in response.get_json()["error"]

    def test_trigger_cronjob_requires_a_name(self, client):
        response = client.post("/api/trigger-cronjob", json={})
        assert response.status_code == 400
        assert "cronjob_name" in response.get_json()["error"]

    def test_trigger_cronjob_tolerates_a_missing_body(self, client):
        """A POST with no JSON at all must 400, not 500."""
        assert client.post("/api/trigger-cronjob").status_code == 400

    def test_delete_notification_requires_both_fields(self, client):
        response = client.post("/api/delete-notification", json={"cluster_name": "a"})
        assert response.status_code == 400

    def test_delete_notification_is_unavailable_without_redis(self, client):
        response = client.post(
            "/api/delete-notification",
            json={"cluster_name": "a", "namespace": "b"},
        )
        assert response.status_code == 400
        assert "Redis" in response.get_json()["error"]


class TestRouteInventory:
    """
    Every route the Helm chart, the templates or an operator might hit. A
    missing entry here means the restructure silently dropped an endpoint.
    """

    EXPECTED = {
        "/",
        "/clusters",
        "/rules",
        "/scheduled-tasks",
        "/analytics",
        "/notifications",
        "/metrics",
        "/health",
        "/api/job-logs",
        "/api/trigger-cronjob",
        "/api/delete-notification",
    }

    def test_all_routes_are_registered(self, api):
        with (
            patch.object(KubernetesClient, "_load", lambda self: None),
            patch.object(KubernetesClient, "custom_objects", api),
            patch.object(KubernetesClient, "core_v1", MagicMock()),
        ):
            app = create_app(no_redis=True)

        registered = {str(rule) for rule in app.url_map.iter_rules()}
        assert self.EXPECTED <= registered

    def test_all_routes_move_under_the_prefix(self, api):
        with (
            patch.object(KubernetesClient, "_load", lambda self: None),
            patch.object(KubernetesClient, "custom_objects", api),
            patch.object(KubernetesClient, "core_v1", MagicMock()),
        ):
            app = create_app(url_prefix="/foo", no_redis=True)

        registered = {str(rule) for rule in app.url_map.iter_rules()}
        # The index becomes "/foo/", which is what the pre-blueprint code
        # registered too; Flask redirects "/foo" to it.
        expected = {f"/foo{path}" for path in self.EXPECTED}
        assert expected <= registered

    def test_prefixed_index_is_reachable(self, api):
        with (
            patch.object(KubernetesClient, "_load", lambda self: None),
            patch.object(KubernetesClient, "custom_objects", api),
            patch.object(KubernetesClient, "core_v1", MagicMock()),
        ):
            app = create_app(url_prefix="/foo", no_redis=True)
            app.config["TESTING"] = True
            with app.test_client() as client:
                assert client.get("/foo/").status_code == 200
                # Flask redirects the un-slashed form rather than 404ing.
                assert client.get("/foo").status_code in (200, 308)


class TestMetrics:
    def test_api_mode_is_reported_without_redis(self, client):
        """
        The deletion API in use describes the cluster, not the stored analytics,
        so it must be scrapeable even when Redis is disabled.
        """
        body = client.get("/metrics").data.decode()
        assert 'nkp_cluster_cleaner_api_mode{mode="nkpcluster"} 1' in body

    def test_analytics_is_reported_as_disabled(self, client):
        body = client.get("/metrics").data.decode()
        assert "nkp_cluster_cleaner_analytics_enabled 0" in body
