"""
The tool driven against a real Kubernetes API, over a real socket.

Everywhere else the suite fakes the CustomObjectsApi, which means it never
exercises the kubeconfig, the HTTP transport, or the API coordinates in
core.models. tests/test_discovery.py's fake dispatches on `plural` alone, so a
wrong `group` or `version` would pass every test there and still fail against
an actual cluster. These tests close that gap by running the real client
against misc/mock_k8s_api.py, in-process on an ephemeral port.

That mock's FIXTURES carry the state the tool should reach for each cluster, so
this file is also the assertion that the fixture estate — the thing a developer
eyeballs when running the mock by hand — means what it says it does.
"""

from __future__ import annotations

import mock_k8s_api
import pytest

from nkp_cluster_cleaner.core.config import ConfigManager
from nkp_cluster_cleaner.core.models import (
    CAPI_PLURAL,
    NKP_PLURAL,
    ClusterState,
)
from nkp_cluster_cleaner.k8s.clusters import ClusterManager

pytestmark = pytest.mark.integration

#: Fixtures the tool should discover, and those it should skip entirely.
LISTED = [f for f in mock_k8s_api.FIXTURES if f["state"]]
UNLISTED = [f for f in mock_k8s_api.FIXTURES if not f["state"]]

#: Fixture names, for readable parametrise IDs.
LISTED_IDS = [f["name"] for f in LISTED]
UNLISTED_IDS = [f["name"] for f in UNLISTED]


def manager_for(cluster, config_path, **kwargs) -> ClusterManager:
    """Build a ClusterManager talking to a mock cluster over HTTP."""
    return ClusterManager(cluster.kubeconfig, ConfigManager(config_path), **kwargs)


@pytest.fixture(scope="module")
def manager(mock_api, criteria_config) -> ClusterManager:
    """A manager wired to the NKP 2.18+ mock, built once for the module."""
    return manager_for(mock_api, criteria_config)


@pytest.fixture(scope="module")
def statuses(manager) -> dict:
    """Every discovered cluster's status, keyed by name."""
    return {s.cluster.name: s for s in manager.get_cluster_statuses()}


@pytest.fixture(scope="module")
def legacy(legacy_mock_api, criteria_config) -> ClusterManager:
    """A manager wired to the pre-2.18 mock, where NKPCluster 404s."""
    return manager_for(legacy_mock_api, criteria_config)


class TestFixtureOracle:
    """The mock's declared expectations, checked against what the tool does."""

    @pytest.mark.parametrize("fixture", LISTED, ids=LISTED_IDS)
    def test_cluster_reaches_its_expected_state(self, statuses, fixture):
        assert fixture["name"] in statuses, "cluster was not discovered at all"
        assert statuses[fixture["name"]].state.value == fixture["state"]

    @pytest.mark.parametrize(
        "fixture",
        [f for f in LISTED if f.get("reason")],
        ids=[f["name"] for f in LISTED if f.get("reason")],
    )
    def test_deletion_reason_is_the_expected_one(self, statuses, fixture):
        assert statuses[fixture["name"]].verdict.reason.value == fixture["reason"]

    @pytest.mark.parametrize("fixture", UNLISTED, ids=UNLISTED_IDS)
    def test_attached_cluster_is_not_discovered(self, statuses, fixture):
        assert fixture["name"] not in statuses

    def test_nothing_beyond_the_fixtures_is_discovered(self, statuses):
        assert set(statuses) == {f["name"] for f in LISTED}

    def test_the_fixtures_cover_every_state(self):
        """
        Guards the oracle itself. Without this, a state could quietly stop
        being exercised — by a fixture being edited, or by a new state being
        added — and every test above would still pass.

        IN_GRACE only exists under a grace period, so it comes from the
        `grace_1h` overrides rather than from the default states.
        """
        covered = {f["state"] for f in LISTED}
        covered |= {f["grace_1h"] for f in LISTED if f.get("grace_1h")}
        assert covered == {state.value for state in ClusterState}

    def test_grace_period_changes_only_what_it_should(self, mock_api, criteria_config):
        graced = manager_for(mock_api, criteria_config, grace_period="1h")
        states = {s.cluster.name: s.state.value for s in graced.get_cluster_statuses()}

        expected = {f["name"]: f.get("grace_1h", f["state"]) for f in LISTED}
        assert states == expected


class TestTheWire:
    """What the tool actually puts on the socket."""

    #: The resource paths discovery must hit, spelled out here rather than
    #: derived from core.models so that a typo there is caught rather than
    #: copied into the expectation.
    EXPECTED_PATHS = [
        "/apis/kommander.mesosphere.io/v1beta1/kommanderclusters",
        "/apis/clusters.nkp.nutanix.com/v1alpha1/nkpclusters",
        "/apis/cluster.x-k8s.io/v1beta1/clusters",
    ]

    @pytest.mark.parametrize("path", EXPECTED_PATHS)
    def test_resource_coordinates_reach_the_server(
        self, recorded, criteria_config, path
    ):
        manager_for(recorded, criteria_config).get_cluster_statuses()

        requested = {p.split("?")[0] for p in recorded.paths("GET")}
        assert path in requested

    def test_namespace_filter_is_a_namespaced_request(self, recorded, criteria_config):
        """
        A client-side filter would produce the same results from a cluster-wide
        list, so this checks the request, not just the answer.
        """
        manager = manager_for(recorded, criteria_config)
        recorded.clear_requests()

        names = {s.cluster.name for s in manager.get_cluster_statuses("team-beta")}

        assert names == {"beta-sandbox", "workload-1", "production-api"}
        assert (
            "/apis/kommander.mesosphere.io/v1beta1/namespaces/team-beta"
            "/kommanderclusters" in {p.split("?")[0] for p in recorded.paths("GET")}
        )

    def test_reading_never_writes(self, recorded, criteria_config):
        manager_for(recorded, criteria_config).get_cluster_statuses()

        mutations = [(verb, path) for verb, path in recorded.requests if verb != "GET"]
        assert mutations == []


class TestManagementClusterFacts:
    def test_api_mode_is_detected_from_the_crd(self, manager):
        assert manager.api_mode == "nkpcluster"

    def test_nkp_version_is_read_from_kommandercore(self, manager):
        assert manager.get_nkp_version() == "v2.18.0"

    def test_kommander_crd_check_passes(self, manager):
        assert manager.check_kommander_crds() is True


class TestDeletionTargets:
    def test_deletable_clusters_target_their_nkp_cluster(self, manager):
        for status in manager.get_clusters_for_deletion():
            assert status.cluster.target.plural == NKP_PLURAL

    def test_target_uses_the_nkp_clusters_own_name(self, statuses):
        """
        alpha-renamed's NKPCluster has a different name, reachable only through
        the ownerReference. Deleting by the KommanderCluster's name would 404.
        """
        assert statuses["alpha-renamed"].cluster.nkp.name == "alpha-renamed-h7k2p"

    def test_delete_is_refused_by_the_read_only_mock(self, recorded, criteria_config):
        """
        The mock 403s every write, which is what makes the dry-run assertions
        elsewhere meaningful: a leaked delete would be visible here.
        """
        manager = manager_for(recorded, criteria_config)
        cluster = manager.get_clusters_for_deletion()[0].cluster
        recorded.clear_requests()

        assert manager.delete_cluster(cluster) is False
        assert recorded.paths("DELETE") == [
            f"/apis/clusters.nkp.nutanix.com/v1alpha1/namespaces"
            f"/{cluster.namespace}/nkpclusters/{cluster.name}"
        ]

    def test_dry_run_puts_nothing_on_the_wire(self, recorded, criteria_config):
        manager = manager_for(recorded, criteria_config)
        cluster = manager.get_clusters_for_deletion()[0].cluster
        recorded.clear_requests()

        assert manager.delete_cluster(cluster, dry_run=True) is True
        assert recorded.requests == []


class TestLegacyCluster:
    """Pre-2.18, where the NKPCluster CRD 404s and CAPI is the target."""

    def test_missing_crd_selects_the_capi_strategy(self, legacy):
        assert legacy.api_mode == "capi"

    def test_deletable_clusters_target_their_capi_cluster(self, legacy):
        targets = {s.cluster.target.plural for s in legacy.get_clusters_for_deletion()}
        assert targets == {CAPI_PLURAL}

    def test_labels_held_only_on_the_nkp_cluster_become_invisible(self, legacy):
        """
        alpha-renamed carries its labels on the NKPCluster alone. Without that
        object there is no `expires` label, so a cluster that is Active on 2.18+
        is deletable here — the clearest evidence the join really is gone.
        """
        states = {s.cluster.name: s.state for s in legacy.get_cluster_statuses()}
        assert states["alpha-renamed"] is ClusterState.FOR_DELETION

    def test_everything_else_reaches_the_same_state(self, legacy):
        states = {s.cluster.name: s.state.value for s in legacy.get_cluster_statuses()}
        expected = {f["name"]: f["state"] for f in LISTED}
        expected["alpha-renamed"] = "for_deletion"
        assert states == expected
