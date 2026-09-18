"""
Discovery of clusters from the Kubernetes API.

Enumeration starts from KommanderCluster rather than NKPCluster for two
reasons: it is the resource that exists on every NKP release, and it is where
attached clusters are distinguishable (they have no spec.clusterRef.capiCluster).
Each KommanderCluster is then joined to the NKPCluster that owns it, when one
exists.
"""

from __future__ import annotations

import logging

from kubernetes.client import CoreV1Api, CustomObjectsApi
from kubernetes.client.rest import ApiException

from .deletion import DeletionStrategy
from .models import (
    CAPI_GROUP,
    CAPI_PLURAL,
    CAPI_VERSION,
    KOMMANDER_GROUP,
    KOMMANDER_PLURAL,
    KOMMANDER_VERSION,
    LEGACY_MANAGEMENT_NAME,
    MANAGEMENT_LABEL,
    MANAGEMENT_NAMESPACE,
    NKP_GROUP,
    NKP_PLURAL,
    NKP_VERSION,
    Cluster,
    capi_ref,
    kommander_ref,
    nkp_ref,
)
from .timeparse import parse_timestamp

logger = logging.getLogger(__name__)


def is_attached(kommander_cluster: dict) -> bool:
    """
    Check whether a KommanderCluster represents an attached cluster.

    Attached clusters are registered with NKP but not provisioned by it, so
    there is no CAPI cluster behind them and they are not ours to delete. On
    NKP 2.18 they still get an NKPCluster wrapper object, so the absence of
    spec.clusterRef.capiCluster remains the only reliable signal.

    Args:
        kommander_cluster: A KommanderCluster object from the API.

    Returns:
        True if the cluster is attached rather than NKP-provisioned.
    """
    cluster_ref = kommander_cluster.get("spec", {}).get("clusterRef") or {}
    return not isinstance(cluster_ref.get("capiCluster"), dict)


def is_management(kommander_cluster: dict) -> bool:
    """
    Check whether a KommanderCluster is the NKP management cluster.

    Three signals, any of which is conclusive:

    - The `kommander.d2iq.io/host` label, which NKP sets explicitly.
    - Living in the `kommander` namespace, where only the management cluster is.
    - Being named `host-cluster`, which was guaranteed before NKP 2.18.

    Deleting the management cluster would take down NKP itself, so this errs
    firmly towards over-protecting.

    Args:
        kommander_cluster: A KommanderCluster object from the API.

    Returns:
        True if this is the management cluster.
    """
    metadata = kommander_cluster.get("metadata", {})
    labels = metadata.get("labels") or {}

    return (
        labels.get(MANAGEMENT_LABEL) == "true"
        or metadata.get("namespace") == MANAGEMENT_NAMESPACE
        or metadata.get("name") == LEGACY_MANAGEMENT_NAME
    )


def _owning_nkp_cluster_name(kommander_cluster: dict) -> str | None:
    """
    Find the name of the NKPCluster that owns this KommanderCluster.

    NKP 2.18 sets an ownerReference on every KommanderCluster it creates, which
    is a direct and unambiguous pointer. Owner references are always within the
    same namespace, so only the name is needed.

    Args:
        kommander_cluster: A KommanderCluster object from the API.

    Returns:
        The owning NKPCluster's name, or None if there is no such owner.
    """
    owners = kommander_cluster.get("metadata", {}).get("ownerReferences") or []
    for owner in owners:
        if owner.get("kind") == "NKPCluster":
            return owner.get("name")
    return None


def _created_at(*objects: dict | None):
    """
    Return the first usable creationTimestamp from the given objects, as UTC.

    Args:
        *objects: API objects, in order of preference.

    Returns:
        A timezone-aware datetime, or None if none of them carried a valid one.
    """
    for obj in objects:
        if not obj:
            continue
        raw = obj.get("metadata", {}).get("creationTimestamp")
        if not raw:
            continue
        try:
            return parse_timestamp(raw)
        except ValueError:
            name = obj.get("metadata", {}).get("name", "unknown")
            logger.warning("Unparseable creationTimestamp on %s: %r", name, raw)
    return None


class ClusterDiscovery:
    """Builds Cluster objects by joining the resources that describe them."""

    def __init__(
        self,
        custom_api: CustomObjectsApi,
        core_api: CoreV1Api,
        strategy: DeletionStrategy,
    ):
        """
        Args:
            custom_api: Client for the custom resources.
            core_api: Client for core resources. Retained for callers that need
                namespace enumeration.
            strategy: Decides which resource is a cluster's deletion target.
        """
        self.custom_api = custom_api
        self.core_api = core_api
        self.strategy = strategy

    def discover(self, namespace: str | None = None) -> list[Cluster]:
        """
        Find every NKP-provisioned cluster.

        Attached clusters are excluded: they are not provisioned by NKP and
        deleting them is not this tool's business.

        Args:
            namespace: If given, only look in this namespace.

        Returns:
            The clusters found, in a stable namespace/name order.
        """
        kommander_clusters = self._list_kommander_clusters(namespace)
        nkp_clusters = self._index_nkp_clusters()
        capi_clusters = self._index_capi_clusters()

        clusters = []
        for kc in kommander_clusters:
            if is_attached(kc):
                logger.debug(
                    "Skipping attached cluster %s",
                    kc.get("metadata", {}).get("name", "unknown"),
                )
                continue
            clusters.append(self._build(kc, nkp_clusters, capi_clusters))

        clusters.sort(key=lambda c: (c.namespace, c.name))
        return clusters

    def _build(
        self,
        kommander_cluster: dict,
        nkp_clusters: dict[tuple[str, str], dict],
        capi_clusters: dict[tuple[str, str], dict],
    ) -> Cluster:
        """Assemble a single Cluster from its constituent API objects."""
        metadata = kommander_cluster["metadata"]
        name = metadata["name"]
        kc_namespace = metadata["namespace"]

        nkp_object = self._find_nkp_cluster(kommander_cluster, nkp_clusters)
        capi_name, capi_namespace = self._capi_coordinates(kommander_cluster)

        capi_object = capi_clusters.get((capi_namespace, capi_name))

        # The KommanderCluster's labels win: NKP propagates labels from the
        # NKPCluster down to it, so this reflects any local override while
        # still picking up labels that only exist upstream.
        labels = {
            **((nkp_object or {}).get("metadata", {}).get("labels") or {}),
            **(metadata.get("labels") or {}),
        }

        cluster = Cluster(
            name=name,
            namespace=kc_namespace,
            labels=labels,
            # The NKPCluster is created first and is the cluster's true birth;
            # the KommanderCluster appears a couple of minutes later.
            created_at=_created_at(nkp_object, kommander_cluster),
            kommander=kommander_ref(name, kc_namespace),
            nkp=(
                nkp_ref(
                    nkp_object["metadata"]["name"],
                    nkp_object["metadata"]["namespace"],
                )
                if nkp_object
                else None
            ),
            capi=(
                capi_ref(capi_name, capi_namespace)
                if capi_object and capi_name and capi_namespace
                else None
            ),
            is_management=is_management(kommander_cluster),
        )

        cluster.target = self.strategy.target_for(cluster)
        cluster.deleting = self._is_deleting(
            cluster, nkp_object=nkp_object, capi_object=capi_object
        )
        return cluster

    @staticmethod
    def _capi_coordinates(
        kommander_cluster: dict,
    ) -> tuple[str | None, str | None]:
        """Extract the CAPI cluster name and namespace from spec.clusterRef."""
        capi = (
            kommander_cluster.get("spec", {}).get("clusterRef", {}).get("capiCluster")
            or {}
        )
        return capi.get("name"), capi.get("namespace")

    @staticmethod
    def _is_deleting(
        cluster: Cluster, nkp_object: dict | None, capi_object: dict | None
    ) -> bool:
        """
        Check whether the cluster's deletion target is already being removed.

        NKPCluster teardown is slow, so without this the cluster would be
        re-issued for deletion and re-notified on every run until it finally
        disappeared.
        """
        if cluster.target is None:
            return False

        target_object = nkp_object if cluster.target == cluster.nkp else capi_object
        if not target_object:
            return False

        return bool(target_object.get("metadata", {}).get("deletionTimestamp"))

    def _find_nkp_cluster(
        self, kommander_cluster: dict, nkp_clusters: dict[tuple[str, str], dict]
    ) -> dict | None:
        """
        Locate the NKPCluster backing a KommanderCluster.

        Tries, in order: the ownerReference NKP sets on the KommanderCluster;
        a match on the same name and namespace, which holds in practice and
        covers a cluster whose ownerReference has not been written yet.

        Args:
            kommander_cluster: The KommanderCluster to resolve.
            nkp_clusters: NKPClusters indexed by (namespace, name).

        Returns:
            The matching NKPCluster, or None.
        """
        if not nkp_clusters:
            return None

        metadata = kommander_cluster["metadata"]
        namespace = metadata["namespace"]

        owner_name = _owning_nkp_cluster_name(kommander_cluster)
        if owner_name:
            found = nkp_clusters.get((namespace, owner_name))
            if found:
                return found
            logger.warning(
                "KommanderCluster %s/%s names NKPCluster %r as its owner, but no "
                "such NKPCluster was found",
                namespace,
                metadata["name"],
                owner_name,
            )

        return nkp_clusters.get((namespace, metadata["name"]))

    def _list_kommander_clusters(self, namespace: str | None) -> list[dict]:
        """List KommanderClusters, cluster-wide or in one namespace."""
        items = self._list(
            KOMMANDER_GROUP,
            KOMMANDER_VERSION,
            KOMMANDER_PLURAL,
            namespace,
            "KommanderCluster CRDs not found. Is Kommander installed?",
        )
        return items

    def _index_nkp_clusters(self) -> dict[tuple[str, str], dict]:
        """Index every NKPCluster by (namespace, name)."""
        if self.strategy.mode != "nkpcluster":
            return {}
        items = self._list(NKP_GROUP, NKP_VERSION, NKP_PLURAL, None, None)
        return {
            (item["metadata"]["namespace"], item["metadata"]["name"]): item
            for item in items
        }

    def _index_capi_clusters(self) -> dict[tuple[str, str], dict]:
        """Index every CAPI Cluster by (namespace, name)."""
        items = self._list(
            CAPI_GROUP,
            CAPI_VERSION,
            CAPI_PLURAL,
            None,
            "CAPI Cluster CRDs not found.",
        )
        return {
            (item["metadata"]["namespace"], item["metadata"]["name"]): item
            for item in items
        }

    def _list(
        self,
        group: str,
        version: str,
        plural: str,
        namespace: str | None,
        missing_crd_warning: str | None,
    ) -> list[dict]:
        """
        List a custom resource, tolerating the CRD not being installed.

        A single cluster-wide call replaces the per-namespace loop this tool
        used to do, which cost one API request per namespace.
        """
        try:
            if namespace:
                response = self.custom_api.list_namespaced_custom_object(
                    group=group, version=version, namespace=namespace, plural=plural
                )
            else:
                response = self.custom_api.list_cluster_custom_object(
                    group=group, version=version, plural=plural
                )
            return response.get("items", [])
        except ApiException as e:
            if e.status == 404:
                if missing_crd_warning:
                    logger.warning(missing_crd_warning)
                return []
            raise
