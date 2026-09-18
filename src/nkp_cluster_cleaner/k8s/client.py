"""
Kubernetes API access.

One place that knows how to authenticate and hand out API clients. Previously
both ClusterManager and CronJobManager carried their own near-identical copy of
this, and neither supported running with a service account.
"""

from __future__ import annotations

import logging
from functools import cached_property

from kubernetes import client, config

logger = logging.getLogger(__name__)


class KubernetesClient:
    """
    Authenticated access to the Kubernetes API.

    The individual API clients are built lazily, so a command that only needs
    CronJobs does not pay for the rest.
    """

    def __init__(self, kubeconfig_path: str | None = None, load: bool = True):
        """
        Args:
            kubeconfig_path: Path to a kubeconfig file. If None, the default
                locations are tried, then in-cluster credentials.
            load: Set False to skip authentication entirely. Only useful in
                tests, where the API clients are replaced wholesale.

        Raises:
            Exception: If no usable credentials could be found.
        """
        self.kubeconfig_path = kubeconfig_path
        if load:
            self._load()

    def _load(self):
        """
        Authenticate against the cluster.

        Note this configures the Kubernetes client library's global state, so
        the last client constructed wins if several are built with different
        kubeconfigs. Every entry point uses a single kubeconfig, so that is not
        a situation the tool creates.
        """
        try:
            if self.kubeconfig_path:
                config.load_kube_config(config_file=self.kubeconfig_path)
                return

            try:
                config.load_kube_config()
            except Exception:
                # No kubeconfig: running in a pod with a service account.
                config.load_incluster_config()
                logger.debug("Using in-cluster credentials")
        except Exception as e:
            raise Exception(f"Failed to load kubeconfig: {e}") from e

    @cached_property
    def core_v1(self) -> client.CoreV1Api:
        """Client for core resources: namespaces, pods, logs."""
        return client.CoreV1Api()

    @cached_property
    def custom_objects(self) -> client.CustomObjectsApi:
        """Client for custom resources: NKPCluster, KommanderCluster, CAPI."""
        return client.CustomObjectsApi()

    @cached_property
    def batch_v1(self) -> client.BatchV1Api:
        """Client for Jobs and CronJobs."""
        return client.BatchV1Api()
