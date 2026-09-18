"""
Tests for the deletion strategies.

The critical assertion here is that on NKP 2.18+ the NKPCluster is deleted and
the CAPI cluster is left alone — deleting the CAPI cluster directly fights the
NKPCluster controller's finalizers.
"""

from unittest.mock import MagicMock

import pytest
from kubernetes.client.rest import ApiException

from nkp_cluster_cleaner.deletion import (
    CapiClusterStrategy,
    NKPClusterStrategy,
    nkp_cluster_crd_available,
    select_strategy,
)
from nkp_cluster_cleaner.models import CAPI_PLURAL, NKP_PLURAL
from tests.factories import make_cluster


@pytest.fixture
def api():
    return MagicMock()


class TestTargetSelection:
    def test_nkp_strategy_targets_the_nkp_cluster(self, api):
        cluster = make_cluster()
        assert NKPClusterStrategy(api).target_for(cluster) == cluster.nkp

    def test_capi_strategy_targets_the_capi_cluster(self, api):
        cluster = make_cluster()
        assert CapiClusterStrategy(api).target_for(cluster) == cluster.capi

    def test_nkp_strategy_has_no_target_without_an_nkp_cluster(self, api):
        cluster = make_cluster(has_target=False)
        assert NKPClusterStrategy(api).target_for(cluster) is None


class TestDelete:
    def test_nkp_cluster_is_deleted_not_the_capi_cluster(self, api):
        """The whole point of the 2.18 change."""
        cluster = make_cluster()
        NKPClusterStrategy(api).delete(cluster.nkp)

        api.delete_namespaced_custom_object.assert_called_once()
        kwargs = api.delete_namespaced_custom_object.call_args.kwargs
        assert kwargs["plural"] == NKP_PLURAL
        assert kwargs["group"] == "clusters.nkp.nutanix.com"
        assert kwargs["name"] == "workload-1"

    def test_legacy_mode_deletes_the_capi_cluster(self, api):
        cluster = make_cluster()
        CapiClusterStrategy(api).delete(cluster.capi)

        kwargs = api.delete_namespaced_custom_object.call_args.kwargs
        assert kwargs["plural"] == CAPI_PLURAL
        assert kwargs["group"] == "cluster.x-k8s.io"

    def test_dry_run_issues_no_api_call(self, api):
        cluster = make_cluster()
        assert NKPClusterStrategy(api).delete(cluster.nkp, dry_run=True)
        api.delete_namespaced_custom_object.assert_not_called()

    def test_already_deleted_counts_as_success(self, api):
        """A 404 means the desired end state already holds."""
        api.delete_namespaced_custom_object.side_effect = ApiException(status=404)
        assert NKPClusterStrategy(api).delete(make_cluster().nkp)

    def test_api_failure_is_reported(self, api):
        api.delete_namespaced_custom_object.side_effect = ApiException(status=403)
        assert not NKPClusterStrategy(api).delete(make_cluster().nkp)


class TestStrategySelection:
    def test_crd_present_selects_the_nkp_strategy(self, api):
        api.list_cluster_custom_object.return_value = {"items": []}
        strategy = select_strategy(api)
        assert isinstance(strategy, NKPClusterStrategy)
        assert strategy.mode == "nkpcluster"

    def test_crd_absent_falls_back_to_capi(self, api):
        api.list_cluster_custom_object.side_effect = ApiException(status=404)
        strategy = select_strategy(api)
        assert isinstance(strategy, CapiClusterStrategy)
        assert strategy.mode == "capi"

    def test_other_api_errors_fall_back_rather_than_crash(self, api):
        """
        Degrading to the legacy path is safe: without CAPI clusters to act on
        it is a no-op, whereas crashing takes the web UI down with it.
        """
        api.list_cluster_custom_object.side_effect = ApiException(status=500)
        assert not nkp_cluster_crd_available(api)
