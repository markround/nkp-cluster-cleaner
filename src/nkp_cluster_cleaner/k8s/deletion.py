"""
Deletion strategies for the two NKP generations.

NKP 2.18 introduced the NKPCluster CRD as the top-level object owning both the
CAPI Cluster and the KommanderCluster. On 2.18+ the NKPCluster is what must be
deleted: its finalizers tear the other resources down in the right order.
Deleting the CAPI Cluster directly leaves the NKPCluster behind and fights its
controller.

Older releases have no NKPCluster, so the CAPI Cluster remains the target there.
Which strategy applies is decided once, by probing the API for the CRD.
"""

from __future__ import annotations

import logging
from typing import Protocol

from kubernetes.client import CustomObjectsApi
from kubernetes.client.rest import ApiException

from ..core.models import NKP_GROUP, NKP_PLURAL, NKP_VERSION, Cluster, ResourceRef

logger = logging.getLogger(__name__)


class DeletionStrategy(Protocol):
    """Decides what resource represents a cluster, and deletes it."""

    #: Short identifier used in logs, /health and the api_mode metric.
    mode: str

    def target_for(self, cluster: Cluster) -> ResourceRef | None:
        """Return the resource to delete for this cluster, or None."""
        ...

    def delete(self, target: ResourceRef, dry_run: bool = False) -> bool:
        """Delete the resource. Returns True on success or in dry-run."""
        ...


class _BaseStrategy:
    """Shared deletion mechanics; subclasses only choose the target."""

    mode = "unknown"

    def __init__(self, custom_api: CustomObjectsApi):
        self.custom_api = custom_api

    def delete(self, target: ResourceRef, dry_run: bool = False) -> bool:
        """
        Delete the given resource.

        Args:
            target: The resource to delete.
            dry_run: If True, log what would happen and change nothing.

        Returns:
            True if the delete succeeded, or if this was a dry run. Also True
            if the resource was already gone, since the desired end state holds.
        """
        if dry_run:
            logger.info("[DRY RUN] Would delete %s %s", target.kind_name, target)
            return True

        try:
            self.custom_api.delete_namespaced_custom_object(
                group=target.group,
                version=target.version,
                namespace=target.namespace,
                plural=target.plural,
                name=target.name,
            )
        except ApiException as e:
            if e.status == 404:
                logger.info("%s %s was already gone", target.kind_name, target)
                return True
            logger.error("Failed to delete %s %s: %s", target.kind_name, target, e)
            return False

        logger.info("Requested deletion of %s %s", target.kind_name, target)
        return True


class NKPClusterStrategy(_BaseStrategy):
    """
    NKP 2.18+. Deletes the NKPCluster and lets its finalizers do the rest.

    Teardown is asynchronous and can take a long time, since the CAPI cluster
    and the KommanderCluster must be removed first. Callers should expect the
    cluster to remain visible, in ClusterState.DELETING, for a while afterwards.
    """

    mode = "nkpcluster"

    def target_for(self, cluster: Cluster) -> ResourceRef | None:
        return cluster.nkp


class CapiClusterStrategy(_BaseStrategy):
    """Pre-2.18 fallback. Deletes the CAPI Cluster directly."""

    mode = "capi"

    def target_for(self, cluster: Cluster) -> ResourceRef | None:
        return cluster.capi


def nkp_cluster_crd_available(custom_api: CustomObjectsApi) -> bool:
    """
    Check whether this management cluster has the NKPCluster CRD.

    Args:
        custom_api: A Kubernetes CustomObjectsApi client.

    Returns:
        True if NKPCluster resources can be listed. False on a 404, which means
        pre-2.18. Any other API error is also treated as unavailable, with a
        warning, so that a transient problem degrades to the legacy path rather
        than crashing — the legacy path is a no-op when there are no CAPI
        clusters to act on.
    """
    try:
        custom_api.list_cluster_custom_object(
            group=NKP_GROUP, version=NKP_VERSION, plural=NKP_PLURAL, limit=1
        )
        return True
    except ApiException as e:
        if e.status == 404:
            logger.info(
                "NKPCluster CRD not present; assuming NKP older than 2.18 "
                "and falling back to deleting CAPI clusters"
            )
        else:
            logger.warning("Could not check for the NKPCluster CRD: %s", e)
        return False


def select_strategy(custom_api: CustomObjectsApi) -> DeletionStrategy:
    """
    Pick the deletion strategy appropriate to this management cluster.

    Args:
        custom_api: A Kubernetes CustomObjectsApi client.

    Returns:
        NKPClusterStrategy on NKP 2.18+, CapiClusterStrategy otherwise.
    """
    if nkp_cluster_crd_available(custom_api):
        return NKPClusterStrategy(custom_api)
    return CapiClusterStrategy(custom_api)
