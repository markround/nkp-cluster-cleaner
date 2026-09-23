"""
Notification Manager - decides which clusters warrant an alert, and sends it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import requests

from ..core.config import ConfigManager
from ..core.models import ClusterState, ClusterStatus
from ..core.timeparse import format_duration, now
from ..k8s.clusters import ClusterManager

#: Severity levels, in increasing order of urgency.
WARNING = "warning"
CRITICAL = "critical"

_SLACK_STYLES = {
    CRITICAL: ("#ff0000", "🚨"),
    WARNING: ("#ff9900", "⚠️"),
    "info": ("#0099ff", "ℹ️"),
}
_DEFAULT_SLACK_STYLE = ("#808080", "📢")


@dataclass
class ClusterNotification:
    """A cluster that warrants an alert, and why."""

    status: ClusterStatus
    severity: str
    elapsed_percentage: float

    @property
    def cluster(self):
        """The underlying cluster."""
        return self.status.cluster

    @property
    def expires_at(self) -> datetime | None:
        """When the cluster expires, if known."""
        return self.status.verdict.expires_at

    def time_remaining(self, current_time: datetime) -> str:
        """
        How long is left, as a short human-readable string.

        Returns "IMMEDIATE" for clusters that fail the criteria outright, since
        those are deleted on the next run rather than on a timer.
        """
        if self.expires_at is None:
            return "IMMEDIATE"
        if current_time >= self.expires_at:
            return "EXPIRED"
        return format_duration(self.expires_at - current_time)

    def as_dict(self, current_time: datetime | None = None) -> dict:
        """
        Flatten into the shape templates and tables consume.

        Args:
            current_time: Override for "now", for testing.

        Returns:
            A dict of display-ready values.
        """
        current_time = current_time or now()
        return {
            "cluster_name": self.cluster.name,
            "namespace": self.cluster.namespace,
            "owner": self.cluster.owner,
            "expires": (
                self.expires_at.strftime("%Y-%m-%d %H:%M:%S")
                if self.expires_at
                else "immediately"
            ),
            "elapsed_percentage": self.elapsed_percentage,
            "time_remaining": self.time_remaining(current_time),
            "reason": self.status.verdict.detail,
            "severity": self.severity,
        }


class NotificationManager:
    """Works out which clusters need alerting about, and delivers the alerts."""

    SUPPORTED_BACKENDS = ["slack"]

    def __init__(
        self,
        kubeconfig_path: str | None = None,
        config_manager: ConfigManager | None = None,
        grace_period: str | None = None,
        cluster_manager: ClusterManager | None = None,
    ):
        """
        Args:
            kubeconfig_path: Path to a kubeconfig file.
            config_manager: Supplies protection rules and required labels.
            grace_period: Clusters younger than this are never notified about.
            cluster_manager: An existing cluster manager to reuse. Passing one
                avoids a second kubeconfig load and CRD probe.
        """
        self.kubeconfig_path = kubeconfig_path
        self.config_manager = config_manager or ConfigManager()
        self.cluster_manager = cluster_manager or ClusterManager(
            kubeconfig_path, self.config_manager, grace_period=grace_period
        )

    def get_notifications(
        self,
        warning_threshold: int,
        critical_threshold: int,
        namespace: str | None = None,
    ) -> list[ClusterNotification]:
        """
        Find every cluster that warrants an alert.

        A cluster is critical if it already matches the deletion criteria, or if
        it has burned through `critical_threshold` percent of its lifetime; it
        is a warning past `warning_threshold`.

        Clusters already being torn down are deliberately excluded - NKPCluster
        deletion is slow, and alerting on every run during teardown is noise.

        Args:
            warning_threshold: Percentage of lifetime elapsed, 0-100.
            critical_threshold: Percentage of lifetime elapsed, 0-100.
            namespace: If given, only examine clusters in this namespace.

        Returns:
            Notifications, most urgent first.

        Raises:
            ValueError: If the thresholds are out of range or inverted.
        """
        self._validate_thresholds(warning_threshold, critical_threshold)

        current_time = now()
        notifications = []

        for status in self.cluster_manager.get_cluster_statuses(namespace):
            severity = self._severity_for(
                status, warning_threshold, critical_threshold, current_time
            )
            if severity:
                notifications.append(
                    ClusterNotification(
                        status=status,
                        severity=severity,
                        elapsed_percentage=status.elapsed_percentage(current_time),
                    )
                )

        notifications.sort(
            key=lambda n: (n.severity != CRITICAL, -n.elapsed_percentage)
        )
        return notifications

    @staticmethod
    def _severity_for(
        status: ClusterStatus,
        warning_threshold: int,
        critical_threshold: int,
        current_time: datetime,
    ) -> str | None:
        """
        Decide the severity for one cluster, or None if it needs no alert.

        Note this branches on ClusterState, not on message text.
        """
        if status.should_delete:
            # Due for deletion now, through expiry or non-compliance.
            return CRITICAL

        if status.state is not ClusterState.ACTIVE:
            # Management, protected, in-grace, deleting and no-target clusters
            # are all cases where an alert would be noise.
            return None

        elapsed = status.elapsed_percentage(current_time)
        if elapsed >= critical_threshold:
            return CRITICAL
        if elapsed >= warning_threshold:
            return WARNING
        return None

    @staticmethod
    def _validate_thresholds(warning_threshold: int, critical_threshold: int):
        """Reject threshold values that cannot produce sensible alerts."""
        if not 0 <= warning_threshold <= 100:
            raise ValueError("Warning threshold must be between 0 and 100")
        if not 0 <= critical_threshold <= 100:
            raise ValueError("Critical threshold must be between 0 and 100")
        if warning_threshold >= critical_threshold:
            raise ValueError("Warning threshold must be less than critical threshold")

    #
    # Delivery
    #
    def send_notification(
        self, backend: str, title: str, text: str, severity: str = "info", **kwargs
    ):
        """
        Send a notification through the named backend.

        Args:
            backend: Backend name; currently only "slack".
            title: Notification title.
            text: Message body.
            severity: One of "info", "warning" or "critical".
            **kwargs: Backend-specific parameters.

        Raises:
            ValueError: If the backend is not supported.
        """
        if backend != "slack":
            raise ValueError(f"Unsupported notification backend: {backend}")
        self._send_slack_notification(title, text, severity, **kwargs)

    def _send_slack_notification(self, title: str, text: str, severity: str, **kwargs):
        """
        Post a message to Slack.

        Args:
            title: Message title.
            text: Message body.
            severity: Drives the attachment colour and emoji.
            **kwargs: token, channel, username and icon_emoji.

        Raises:
            ValueError: If the token or channel is missing.
            Exception: If Slack rejects the message.
        """
        token = kwargs.get("token")
        channel = kwargs.get("channel")

        if not token or not channel:
            raise ValueError("Slack token and channel are required")

        color, emoji = _SLACK_STYLES.get(severity, _DEFAULT_SLACK_STYLE)

        message = {
            "channel": channel,
            "username": kwargs.get("username", "NKP Cluster Cleaner"),
            "icon_emoji": kwargs.get("icon_emoji", ":broom:"),
            "attachments": [
                {
                    "color": color,
                    "title": title,
                    "text": f"{emoji} {text}",
                    "footer": "NKP Cluster Cleaner",
                    "ts": int(now().timestamp()),
                }
            ],
        }

        response = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            data=json.dumps(message),
            timeout=30,
        )

        if response.status_code != 200:
            raise Exception(f"HTTP {response.status_code}: {response.text}")

        result = response.json()
        if not result.get("ok"):
            raise Exception(f"Slack API error: {result.get('error', 'Unknown error')}")

    def send_expiry_notification(
        self,
        backend: str,
        notifications: list[ClusterNotification],
        severity: str,
        threshold: int,
        **kwargs,
    ):
        """
        Send a grouped alert about clusters approaching or past expiry.

        Args:
            backend: Backend name.
            notifications: The clusters to report. No-op if empty.
            severity: "warning" or "critical".
            threshold: The percentage that triggered this alert, for the text.
            **kwargs: Backend-specific parameters.
        """
        if not notifications:
            return

        count = len(notifications)
        if severity == CRITICAL:
            title = f"CRITICAL: {count} clusters will be deleted soon"
            intro = (
                f"These clusters have exceeded {threshold}% of their lifetime "
                "or have immediate deletion issues:"
            )
        elif severity == WARNING:
            title = f"WARNING: {count} clusters will be deleted soon"
            intro = f"These clusters have exceeded {threshold}% of their lifetime:"
        else:
            title = f"INFO: {count} clusters notification"
            intro = "These clusters require attention:"

        current_time = now()
        lines = []
        for notification in notifications:
            data = notification.as_dict(current_time)
            lines.append(
                f"• *{data['cluster_name']}* "
                f"(ns: `{data['namespace']}`, "
                f"owner: `{data['owner']}`, "
                f"expires: `{data['expires']}`, "
                f"consumed: `{data['elapsed_percentage']:.1f}%`, "
                f"remaining: `{data['time_remaining']}`)"
            )

        self.send_notification(
            backend, title, f"{intro}\n\n" + "\n".join(lines), severity, **kwargs
        )

    def send_deletion_notification(
        self,
        backend: str,
        deleted_clusters: list[dict],
        severity: str = "info",
        **kwargs,
    ):
        """
        Report clusters that have just been deleted.

        Args:
            backend: Backend name.
            deleted_clusters: Dicts with name, namespace, owner and reason keys.
            severity: Notification severity.
            **kwargs: Backend-specific parameters.
        """
        if not deleted_clusters:
            return

        count = len(deleted_clusters)
        if count == 1:
            title = "1 cluster has been deleted"
            intro = "The following cluster has been deleted:"
        else:
            title = f"{count} clusters have been deleted"
            intro = "The following clusters have been deleted:"

        lines = [
            f"• *{cluster.get('name', 'unknown')}* "
            f"(ns: `{cluster.get('namespace', 'unknown')}`, "
            f"owner: `{cluster.get('owner', 'unknown')}`, "
            f"reason: `{cluster.get('reason', 'unknown reason')}`)"
            for cluster in deleted_clusters
        ]

        self.send_notification(
            backend, title, f"{intro}\n\n" + "\n".join(lines), severity, **kwargs
        )
