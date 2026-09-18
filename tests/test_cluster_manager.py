"""
End-to-end tests through ClusterManager, from fake API objects to a decision.

These exercise discovery, the criteria and the deletion strategy together, so
they catch wiring mistakes the unit tests would not.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from nkp_cluster_cleaner.cluster_manager import ClusterManager
from nkp_cluster_cleaner.config import ConfigManager
from nkp_cluster_cleaner.models import NKP_PLURAL, ClusterState
from tests.factories import (
    MANAGEMENT_NAMESPACE,
    make_capi_cluster,
    make_kommander_cluster,
    make_nkp_cluster,
    ts,
)
from tests.test_discovery import fake_api


def build_manager(api, config_manager=None, grace_period=None):
    """
    Build a ClusterManager wired to a fake API.

    Only the kubeconfig loading is stubbed; the strategy is still selected for
    real by probing the fake API for the NKPCluster CRD, so the auto-detection
    is exercised rather than assumed.

    Args:
        api: The CustomObjectsApi mock to use.
        config_manager: Optional configuration.
        grace_period: Optional grace period.

    Returns:
        A ClusterManager ready to query.
    """

    def fake_load_config(self):
        self.core_v1 = MagicMock()
        self.custom_api = api

    with patch.object(ClusterManager, "_load_config", fake_load_config):
        return ClusterManager(
            config_manager=config_manager or ConfigManager(), grace_period=grace_period
        )


@pytest.fixture
def expired_estate():
    """
    A small estate: one expired workload cluster, the management cluster, an
    attached cluster, and a cluster already being torn down.
    """
    return fake_api(
        kommander=[
            make_kommander_cluster(
                name="expired", labels={"expires": "1d"}, created=ts(timedelta(days=-5))
            ),
            make_kommander_cluster(
                name="mdr-mgmt", namespace=MANAGEMENT_NAMESPACE, labels={}
            ),
            make_kommander_cluster(name="attached-thing", attached=True),
            make_kommander_cluster(
                name="tearing-down",
                labels={"expires": "1d"},
                created=ts(timedelta(days=-5)),
            ),
        ],
        nkp=[
            make_nkp_cluster(name="expired"),
            make_nkp_cluster(name="mdr-mgmt", namespace=MANAGEMENT_NAMESPACE),
            make_nkp_cluster(name="tearing-down", deleting=True),
        ],
        capi=[make_capi_cluster(name="expired")],
    )


class TestEndToEnd:
    def test_only_the_expired_cluster_is_queued(self, expired_estate):
        manager = build_manager(expired_estate)
        queued = manager.get_clusters_for_deletion()
        assert [s.cluster.name for s in queued] == ["expired"]

    def test_states_are_assigned_correctly(self, expired_estate):
        grouped = build_manager(expired_estate).group_by_state()

        assert [s.cluster.name for s in grouped[ClusterState.FOR_DELETION]] == [
            "expired"
        ]
        assert [s.cluster.name for s in grouped[ClusterState.DELETING]] == [
            "tearing-down"
        ]
        assert [s.cluster.name for s in grouped[ClusterState.MANAGEMENT]] == [
            "mdr-mgmt"
        ]

    def test_attached_cluster_never_appears(self, expired_estate):
        statuses = build_manager(expired_estate).get_cluster_statuses()
        assert "attached-thing" not in {s.cluster.name for s in statuses}

    def test_every_state_key_is_present(self, expired_estate):
        """Callers should be able to index without .get()."""
        grouped = build_manager(expired_estate).group_by_state()
        assert set(grouped) == set(ClusterState)

    def test_deleting_the_queued_cluster_targets_the_nkp_cluster(self, expired_estate):
        manager = build_manager(expired_estate)
        queued = manager.get_clusters_for_deletion()

        assert manager.delete_cluster(queued[0].cluster)

        kwargs = expired_estate.delete_namespaced_custom_object.call_args.kwargs
        assert kwargs["plural"] == NKP_PLURAL
        assert kwargs["name"] == "expired"

    def test_dry_run_deletes_nothing(self, expired_estate):
        manager = build_manager(expired_estate)
        queued = manager.get_clusters_for_deletion()

        assert manager.delete_cluster(queued[0].cluster, dry_run=True)
        expired_estate.delete_namespaced_custom_object.assert_not_called()

    def test_cluster_with_no_target_cannot_be_deleted(self):
        api = fake_api(kommander=[make_kommander_cluster()], nkp=[])
        manager = build_manager(api)
        cluster = manager.get_cluster_statuses()[0].cluster

        assert not manager.delete_cluster(cluster)
        api.delete_namespaced_custom_object.assert_not_called()

    def test_grace_period_spares_a_new_cluster(self):
        api = fake_api(
            kommander=[
                make_kommander_cluster(labels={}, created=ts(timedelta(minutes=-5)))
            ],
            nkp=[make_nkp_cluster(created=ts(timedelta(minutes=-5)))],
        )
        manager = build_manager(api, grace_period="2h")
        assert manager.get_clusters_for_deletion() == []

    def test_api_mode_is_detected_from_the_crd(self, expired_estate):
        assert build_manager(expired_estate).api_mode == "nkpcluster"


class TestLegacyEndToEnd:
    """Pre-2.18: the NKPCluster CRD is absent, detected automatically."""

    def test_missing_crd_selects_the_legacy_mode(self):
        api = fake_api(kommander=[], missing_crds={NKP_PLURAL})
        assert build_manager(api).api_mode == "capi"

    def test_expired_cluster_deletes_the_capi_cluster(self):
        api = fake_api(
            kommander=[
                make_kommander_cluster(
                    name="expired",
                    labels={"expires": "1d"},
                    created=ts(timedelta(days=-5)),
                )
            ],
            capi=[make_capi_cluster(name="expired")],
            missing_crds={NKP_PLURAL},
        )
        manager = build_manager(api)
        assert manager.api_mode == "capi"

        queued = manager.get_clusters_for_deletion()
        assert [s.cluster.name for s in queued] == ["expired"]

        manager.delete_cluster(queued[0].cluster)
        kwargs = api.delete_namespaced_custom_object.call_args.kwargs
        assert kwargs["plural"] == "clusters"
        assert kwargs["group"] == "cluster.x-k8s.io"
