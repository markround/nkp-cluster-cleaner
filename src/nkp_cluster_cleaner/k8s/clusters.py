"""
Cluster Manager — the entry point for everything that talks to a cluster.

Ties together the three pieces it delegates to: `discovery` finds clusters,
`criteria` decides what should happen to them, and `deletion` carries it out.
"""

from __future__ import annotations

import logging

from kubernetes.client.rest import ApiException

from ..core.config import ConfigManager
from ..core.criteria import evaluate
from ..core.models import (
    KOMMANDER_GROUP,
    KOMMANDER_PLURAL,
    KOMMANDER_VERSION,
    Cluster,
    ClusterState,
    ClusterStatus,
)
from ..core.timeparse import now
from .client import KubernetesClient
from .deletion import select_strategy
from .discovery import ClusterDiscovery

logger = logging.getLogger(__name__)


class ClusterManager:
    """Finds, evaluates and deletes NKP clusters."""

    def __init__(
        self,
        kubeconfig_path: str | None = None,
        config_manager: ConfigManager | None = None,
        grace_period: str | None = None,
        client: KubernetesClient | None = None,
    ):
        """
        Initialize the cluster manager.

        Args:
            kubeconfig_path: Path to a kubeconfig file. If None, falls back to
                the default locations and then to in-cluster credentials.
            config_manager: Supplies protection rules and required labels.
            grace_period: Duration such as "1d" or "4h". Clusters younger than
                this are never deleted.
            client: A pre-built Kubernetes client, which takes precedence over
                `kubeconfig_path`. Mainly for tests.
        """
        self.config_manager = config_manager or ConfigManager()
        self.grace_period = grace_period
        self.client = client or KubernetesClient(kubeconfig_path)

        # Which deletion API to use is settled once, at construction, by probing
        # for the NKPCluster CRD.
        self.strategy = select_strategy(self.client.custom_objects)
        self.discovery = ClusterDiscovery(
            self.client.custom_objects, self.client.core_v1, self.strategy
        )

    @property
    def custom_api(self):
        """The custom-objects API client."""
        return self.client.custom_objects

    @property
    def api_mode(self) -> str:
        """
        Which deletion API this management cluster uses.

        "nkpcluster" on NKP 2.18+, "capi" on older releases. Surfaced in the UI,
        /health and the metrics endpoint so the active path is never a guess.
        """
        return self.strategy.mode

    def get_cluster_statuses(self, namespace: str | None = None) -> list[ClusterStatus]:
        """
        Find every NKP-provisioned cluster and decide what to do with it.

        Args:
            namespace: If given, only examine clusters in this namespace.

        Returns:
            One ClusterStatus per cluster, in namespace/name order.
        """
        current_time = now()
        return [
            ClusterStatus(
                cluster=cluster,
                verdict=evaluate(
                    cluster, self.config_manager, self.grace_period, current_time
                ),
            )
            for cluster in self.discovery.discover(namespace)
        ]

    def get_clusters_for_deletion(
        self, namespace: str | None = None
    ) -> list[ClusterStatus]:
        """
        Find the clusters that should be deleted right now.

        Excludes clusters already being torn down, so a slow NKPCluster deletion
        is not repeatedly re-issued.

        Args:
            namespace: If given, only examine clusters in this namespace.

        Returns:
            The clusters matching the deletion criteria.
        """
        return [s for s in self.get_cluster_statuses(namespace) if s.should_delete]

    def group_by_state(
        self, namespace: str | None = None
    ) -> dict[ClusterState, list[ClusterStatus]]:
        """
        Find every cluster, grouped by state.

        Args:
            namespace: If given, only examine clusters in this namespace.

        Returns:
            A dict keyed by ClusterState. Every state is present, possibly
            mapping to an empty list, so callers need not use .get().
        """
        grouped: dict[ClusterState, list[ClusterStatus]] = {
            state: [] for state in ClusterState
        }
        for status in self.get_cluster_statuses(namespace):
            grouped[status.state].append(status)
        return grouped

    def delete_cluster(self, cluster: Cluster, dry_run: bool = False) -> bool:
        """
        Delete a cluster.

        On NKP 2.18+ this removes the NKPCluster and its finalizers tear down
        the CAPI cluster and KommanderCluster in turn, which takes a while. The
        cluster stays visible in ClusterState.DELETING until that completes.

        Args:
            cluster: The cluster to delete.
            dry_run: If True, log what would happen and change nothing.

        Returns:
            True if the deletion was requested successfully, or if this was a
            dry run. False if there was nothing to delete or the call failed.
        """
        if cluster.target is None:
            logger.error("No deletion target for cluster %s", cluster)
            return False

        return self.strategy.delete(cluster.target, dry_run)

    def get_nkp_version(self) -> str | None:
        """
        Read the NKP version from the KommanderCore resource.

        Returns:
            A version string such as "v2.18.0", or None if it could not be read.
        """
        try:
            cores = self.custom_api.list_cluster_custom_object(
                group="dkp.d2iq.io", version="v1alpha1", plural="kommandercores"
            )
        except ApiException as e:
            if e.status == 404:
                logger.warning("KommanderCore CRDs not found")
            else:
                logger.warning("Could not retrieve the NKP version: %s", e)
            return None
        except Exception as e:
            logger.warning("Could not retrieve the NKP version: %s", e)
            return None

        for core in cores.get("items", []):
            version = core.get("status", {}).get("version")
            if version:
                return version

        return None

    def check_kommander_crds(self) -> bool:
        """
        Check that the KommanderCluster CRD is installed.

        Used as a connectivity and sanity test by the health endpoint.

        Returns:
            True if KommanderCluster resources can be listed.
        """
        try:
            self.custom_api.list_cluster_custom_object(
                group=KOMMANDER_GROUP,
                version=KOMMANDER_VERSION,
                plural=KOMMANDER_PLURAL,
                limit=1,
            )
            return True
        except ApiException as e:
            if e.status == 404:
                logger.warning(
                    "KommanderCluster CRDs not found. Is Kommander installed?"
                )
            else:
                logger.warning("Could not check the KommanderCluster CRDs: %s", e)
            return False
