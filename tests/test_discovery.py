"""
Tests for cluster discovery against a faked Kubernetes API.

Mocking happens at the CustomObjectsApi boundary so the real discovery, join
and filtering logic runs.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from kubernetes.client.rest import ApiException

from nkp_cluster_cleaner.core.models import (
    CAPI_PLURAL,
    KOMMANDER_PLURAL,
    NKP_PLURAL,
)
from nkp_cluster_cleaner.k8s.client import KubernetesClient
from nkp_cluster_cleaner.k8s.deletion import CapiClusterStrategy, NKPClusterStrategy
from nkp_cluster_cleaner.k8s.discovery import (
    ClusterDiscovery,
    is_attached,
    is_management,
)
from tests.factories import (
    MANAGEMENT_NAMESPACE,
    WORKSPACE_NAMESPACE,
    make_capi_cluster,
    make_kommander_cluster,
    make_nkp_cluster,
)


def fake_api(kommander=(), nkp=(), capi=(), missing_crds=()):
    """
    Build a CustomObjectsApi mock backed by the given resource lists.

    Args:
        kommander: KommanderCluster objects to serve.
        nkp: NKPCluster objects to serve.
        capi: CAPI Cluster objects to serve.
        missing_crds: Plurals that should raise a 404, simulating a CRD that is
            not installed.

    Returns:
        A configured MagicMock.
    """
    by_plural = {
        KOMMANDER_PLURAL: list(kommander),
        NKP_PLURAL: list(nkp),
        CAPI_PLURAL: list(capi),
    }

    def _list_cluster(*_args, plural=None, **_kwargs):
        if plural in missing_crds:
            raise ApiException(status=404)
        return {"items": by_plural.get(plural, [])}

    def _list_namespaced(*_args, plural=None, namespace=None, **_kwargs):
        if plural in missing_crds:
            raise ApiException(status=404)
        return {
            "items": [
                item
                for item in by_plural.get(plural, [])
                if item["metadata"]["namespace"] == namespace
            ]
        }

    api = MagicMock()
    api.list_cluster_custom_object.side_effect = _list_cluster
    api.list_namespaced_custom_object.side_effect = _list_namespaced
    return api


def fake_client(api):
    """
    Build a KubernetesClient backed by the given CustomObjectsApi mock.

    Authentication is skipped and the API clients are substituted directly, so
    nothing touches a real cluster or a kubeconfig.
    """
    client = KubernetesClient(load=False)
    # cached_property values can simply be written into the instance dict.
    client.__dict__["custom_objects"] = api
    client.__dict__["core_v1"] = MagicMock()
    client.__dict__["batch_v1"] = MagicMock()
    return client


def discovery_for(api, legacy=False):
    """Build a ClusterDiscovery using the NKPCluster or legacy CAPI strategy."""
    strategy = CapiClusterStrategy(api) if legacy else NKPClusterStrategy(api)
    return ClusterDiscovery(api, MagicMock(), strategy)


class TestIsAttached:
    def test_managed_cluster_has_a_capi_cluster_ref(self):
        assert not is_attached(make_kommander_cluster())

    def test_attached_cluster_has_no_capi_cluster_ref(self):
        assert is_attached(make_kommander_cluster(attached=True))

    def test_attached_cluster_with_an_nkp_owner_is_still_attached(self):
        """
        NKP 2.18 wraps attached clusters in an NKPCluster too, so the owner
        reference alone does not make a cluster ours to delete.
        """
        kc = make_kommander_cluster(attached=True, owned_by_nkp=True)
        assert is_attached(kc)

    def test_null_capi_cluster_ref_counts_as_attached(self):
        kc = make_kommander_cluster()
        kc["spec"]["clusterRef"]["capiCluster"] = None
        assert is_attached(kc)


class TestIsManagement:
    def test_detected_by_the_host_label(self):
        kc = make_kommander_cluster(namespace="somewhere-else", management=True)
        assert is_management(kc)

    def test_detected_by_the_kommander_namespace(self):
        """NKP 2.18 no longer guarantees the name, but the namespace holds."""
        kc = make_kommander_cluster(name="mdr-mgmt", namespace=MANAGEMENT_NAMESPACE)
        assert is_management(kc)

    def test_detected_by_the_legacy_name(self):
        kc = make_kommander_cluster(name="host-cluster", namespace="odd-namespace")
        assert is_management(kc)

    def test_ordinary_workload_cluster_is_not_management(self):
        assert not is_management(make_kommander_cluster())


class TestDiscovery:
    def test_attached_clusters_are_excluded(self):
        api = fake_api(
            kommander=[
                make_kommander_cluster(name="managed"),
                make_kommander_cluster(name="attached", attached=True),
            ],
            nkp=[make_nkp_cluster(name="managed")],
            capi=[make_capi_cluster(name="managed")],
        )
        found = discovery_for(api).discover()
        assert [c.name for c in found] == ["managed"]

    def test_nkp_cluster_is_joined_via_the_owner_reference(self):
        """The ownerReference is a direct pointer and is tried first."""
        api = fake_api(
            kommander=[make_kommander_cluster(name="workload-1")],
            nkp=[make_nkp_cluster(name="workload-1")],
        )
        cluster = discovery_for(api).discover()[0]
        assert cluster.nkp is not None
        assert cluster.nkp.name == "workload-1"
        assert cluster.target == cluster.nkp

    def test_nkp_cluster_is_joined_by_name_without_an_owner_reference(self):
        """Covers a cluster whose ownerReference has not been written yet."""
        api = fake_api(
            kommander=[make_kommander_cluster(owned_by_nkp=False)],
            nkp=[make_nkp_cluster()],
        )
        cluster = discovery_for(api).discover()[0]
        assert cluster.nkp is not None

    def test_missing_nkp_cluster_leaves_no_target(self):
        api = fake_api(kommander=[make_kommander_cluster()], nkp=[])
        cluster = discovery_for(api).discover()[0]
        assert cluster.nkp is None
        assert cluster.target is None

    def test_labels_come_from_the_kommander_cluster(self):
        api = fake_api(
            kommander=[make_kommander_cluster(labels={"expires": "7d", "owner": "kc"})],
            nkp=[make_nkp_cluster(labels={"expires": "365d", "owner": "nkp"})],
        )
        cluster = discovery_for(api).discover()[0]
        assert cluster.labels["expires"] == "7d"
        assert cluster.owner == "kc"

    def test_labels_only_on_the_nkp_cluster_are_still_seen(self):
        """NKP propagates labels downwards, but not necessarily instantly."""
        api = fake_api(
            kommander=[make_kommander_cluster(labels={})],
            nkp=[make_nkp_cluster(labels={"expires": "365d", "owner": "mdr"})],
        )
        cluster = discovery_for(api).discover()[0]
        assert cluster.labels["expires"] == "365d"

    def test_creation_time_prefers_the_nkp_cluster(self):
        """The NKPCluster is created first and is the cluster's true birth."""
        api = fake_api(
            kommander=[make_kommander_cluster(created="2026-09-11T15:22:47Z")],
            nkp=[make_nkp_cluster(created="2026-09-11T15:20:08Z")],
        )
        cluster = discovery_for(api).discover()[0]
        assert cluster.created_at == datetime(2026, 9, 11, 15, 20, 8, tzinfo=UTC)

    def test_creation_time_falls_back_to_the_kommander_cluster(self):
        api = fake_api(
            kommander=[make_kommander_cluster(created="2026-09-11T15:22:47Z")], nkp=[]
        )
        cluster = discovery_for(api).discover()[0]
        assert cluster.created_at == datetime(2026, 9, 11, 15, 22, 47, tzinfo=UTC)

    def test_unparseable_creation_time_becomes_none(self):
        """A bad timestamp must not crash the scan."""
        api = fake_api(
            kommander=[make_kommander_cluster(created="not-a-timestamp")], nkp=[]
        )
        assert discovery_for(api).discover()[0].created_at is None

    def test_deletion_in_progress_is_detected(self):
        api = fake_api(
            kommander=[make_kommander_cluster()],
            nkp=[make_nkp_cluster(deleting=True)],
        )
        assert discovery_for(api).discover()[0].deleting

    def test_cluster_not_being_deleted_is_not_flagged(self):
        api = fake_api(kommander=[make_kommander_cluster()], nkp=[make_nkp_cluster()])
        assert not discovery_for(api).discover()[0].deleting

    def test_management_cluster_is_flagged(self):
        api = fake_api(
            kommander=[
                make_kommander_cluster(name="mdr-mgmt", namespace=MANAGEMENT_NAMESPACE)
            ],
            nkp=[make_nkp_cluster(name="mdr-mgmt", namespace=MANAGEMENT_NAMESPACE)],
        )
        assert discovery_for(api).discover()[0].is_management

    def test_namespace_filter_limits_the_search(self):
        api = fake_api(
            kommander=[
                make_kommander_cluster(name="mgmt", namespace=MANAGEMENT_NAMESPACE),
                make_kommander_cluster(name="workload"),
            ],
            nkp=[
                make_nkp_cluster(name="mgmt", namespace=MANAGEMENT_NAMESPACE),
                make_nkp_cluster(name="workload"),
            ],
        )
        found = discovery_for(api).discover(namespace=WORKSPACE_NAMESPACE)
        assert [c.name for c in found] == ["workload"]

    def test_results_are_ordered_stably(self):
        api = fake_api(
            kommander=[
                make_kommander_cluster(name="zeta"),
                make_kommander_cluster(name="alpha"),
            ],
            nkp=[make_nkp_cluster(name="zeta"), make_nkp_cluster(name="alpha")],
        )
        assert [c.name for c in discovery_for(api).discover()] == ["alpha", "zeta"]

    def test_missing_kommander_crd_yields_nothing(self):
        api = fake_api(missing_crds={KOMMANDER_PLURAL, NKP_PLURAL, CAPI_PLURAL})
        assert discovery_for(api).discover() == []

    def test_non_404_api_errors_are_not_swallowed(self):
        """A permissions problem must surface, not look like an empty estate."""
        api = MagicMock()
        api.list_cluster_custom_object.side_effect = ApiException(status=403)
        with pytest.raises(ApiException):
            discovery_for(api).discover()


class TestLegacyMode:
    """Pre-2.18: no NKPCluster CRD, so the CAPI Cluster is the target."""

    def test_capi_cluster_is_the_target(self):
        api = fake_api(
            kommander=[make_kommander_cluster()],
            capi=[make_capi_cluster()],
            missing_crds={NKP_PLURAL},
        )
        cluster = discovery_for(api, legacy=True).discover()[0]
        assert cluster.target == cluster.capi
        assert cluster.nkp is None

    def test_missing_capi_cluster_leaves_no_target(self):
        """The referenced CAPI cluster is gone, so there is nothing to delete."""
        api = fake_api(
            kommander=[make_kommander_cluster()], capi=[], missing_crds={NKP_PLURAL}
        )
        cluster = discovery_for(api, legacy=True).discover()[0]
        assert cluster.target is None

    def test_capi_deletion_in_progress_is_detected(self):
        api = fake_api(
            kommander=[make_kommander_cluster()],
            capi=[make_capi_cluster(deleting=True)],
            missing_crds={NKP_PLURAL},
        )
        assert discovery_for(api, legacy=True).discover()[0].deleting

    def test_nkp_clusters_are_not_listed_in_legacy_mode(self):
        api = fake_api(kommander=[make_kommander_cluster()], capi=[make_capi_cluster()])
        discovery_for(api, legacy=True).discover()

        listed = {
            call.kwargs.get("plural")
            for call in api.list_cluster_custom_object.call_args_list
        }
        assert NKP_PLURAL not in listed
