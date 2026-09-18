"""
Smoke tests for the web UI.

These render the real templates against a faked Kubernetes API, which is what
catches a template still referencing a field the model no longer has.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from nkp_cluster_cleaner.cluster_manager import ClusterManager
from nkp_cluster_cleaner.web_server import create_app
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

    def fake_load_config(self):
        self.core_v1 = MagicMock()
        self.custom_api = api

    with patch.object(ClusterManager, "_load_config", fake_load_config):
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
        def fake_load_config(self):
            self.core_v1 = MagicMock()
            self.custom_api = api

        with patch.object(ClusterManager, "_load_config", fake_load_config):
            app = create_app(url_prefix="/foo", no_redis=True)
            app.config["TESTING"] = True
            with app.test_client() as client:
                assert client.get("/foo/clusters").status_code == 200
                assert client.get("/clusters").status_code == 404
