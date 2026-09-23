"""
Long-lived objects shared across requests.

The previous code rebuilt a ClusterManager on every request, which meant
re-reading the kubeconfig and re-probing for the NKPCluster CRD each time a
page was loaded. These are built once and cached.

Construction is lazy, and failures are deliberately not cached: if the cluster
is unreachable at startup the server still comes up and shows the error on the
page, then recovers by itself once the cluster returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from flask import current_app

from ..core.config import ConfigManager
from ..core.settings import RedisSettings
from ..k8s.client import KubernetesClient
from ..k8s.clusters import ClusterManager
from ..k8s.cronjobs import CronJobManager
from ..notifications.manager import NotificationManager
from ..storage.analytics import RedisAnalyticsService
from ..storage.notification_history import NotificationHistory


@dataclass
class WebSettings:
    """Everything the web server was configured with."""

    kubeconfig_path: str | None = None
    config_path: str | None = None
    url_prefix: str = ""
    grace_period: str | None = None
    redis: RedisSettings = field(default_factory=RedisSettings)
    no_redis: bool = False

    @property
    def kubeconfig_display(self) -> str:
        """How to describe the kubeconfig in the UI."""
        return self.kubeconfig_path or "default"

    @property
    def config_display(self) -> str:
        """How to describe the config file in the UI."""
        return self.config_path or "none"


class Services:
    """Lazily-built, cached access to everything the routes need."""

    def __init__(self, settings: WebSettings):
        self.settings = settings
        self._k8s: KubernetesClient | None = None
        self._clusters: ClusterManager | None = None
        self._cronjobs: CronJobManager | None = None
        self._notifications: NotificationManager | None = None

    @property
    def config_manager(self) -> ConfigManager:
        """Deletion criteria configuration."""
        path = self.settings.config_path
        return ConfigManager(path) if path else ConfigManager()

    @property
    def k8s(self) -> KubernetesClient:
        """Authenticated Kubernetes API access."""
        if self._k8s is None:
            self._k8s = KubernetesClient(self.settings.kubeconfig_path)
        return self._k8s

    @property
    def clusters(self) -> ClusterManager:
        """Cluster discovery and evaluation."""
        if self._clusters is None:
            self._clusters = ClusterManager(
                config_manager=self.config_manager,
                grace_period=self.settings.grace_period,
                client=self.k8s,
            )
        return self._clusters

    @property
    def cronjobs(self) -> CronJobManager:
        """Scheduled task monitoring."""
        if self._cronjobs is None:
            self._cronjobs = CronJobManager(client=self.k8s)
        return self._cronjobs

    @property
    def notifications(self) -> NotificationManager:
        """Notification evaluation."""
        if self._notifications is None:
            self._notifications = NotificationManager(
                config_manager=self.config_manager,
                grace_period=self.settings.grace_period,
                cluster_manager=self.clusters,
            )
        return self._notifications

    def analytics(self) -> RedisAnalyticsService:
        """
        Analytics query service.

        Built per call rather than cached, so that a Redis outage does not leave
        a dead client pinned for the lifetime of the process.
        """
        return RedisAnalyticsService(self.settings.redis)

    def notification_history(self) -> NotificationHistory:
        """Notification history store. Built per call, as for `analytics`."""
        return NotificationHistory(self.settings.redis)


def services() -> Services:
    """The Services instance attached to the running app."""
    return current_app.extensions["nkp_cluster_cleaner"]


def settings() -> WebSettings:
    """The settings the running app was configured with."""
    return services().settings
