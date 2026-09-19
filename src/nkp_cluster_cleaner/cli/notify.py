"""
Notify command implementation for the NKP Cluster Cleaner tool.
"""

from __future__ import annotations

import click
from colorama import Fore, Style
from tabulate import tabulate

from ..core.config import ConfigManager
from ..core.settings import RedisSettings, SlackSettings
from ..core.timeparse import now
from ..notifications.manager import (
    CRITICAL,
    WARNING,
    ClusterNotification,
    NotificationManager,
)
from ..storage.notification_history import NotificationHistory

_TABLE_HEADERS = [
    "Cluster Name",
    "Namespace",
    "Owner",
    "Expires",
    "Elapsed",
    "Remaining",
]


def _validate_backend(notify_backend: str | None, slack: SlackSettings):
    """
    Check that a notification backend is usable before doing any work.

    Raises:
        click.Abort: If the backend is unknown or incompletely configured.
    """
    if not notify_backend:
        return

    if notify_backend not in NotificationManager.SUPPORTED_BACKENDS:
        click.echo(
            f"{Fore.RED}Error: Unsupported notification backend "
            f"'{notify_backend}'. Supported backends: "
            f"{', '.join(NotificationManager.SUPPORTED_BACKENDS)}{Style.RESET_ALL}"
        )
        raise click.Abort()

    if notify_backend == "slack":
        for flag, value in (
            ("--slack-token", slack.token),
            ("--slack-channel", slack.channel),
        ):
            if not value:
                click.echo(
                    f"{Fore.RED}Error: {flag} is required when using the "
                    f"slack backend{Style.RESET_ALL}"
                )
                raise click.Abort()


def _display(notifications: list[ClusterNotification], heading: str, colour: str):
    """Print a table of notifications under a coloured heading."""
    current_time = now()
    rows = []
    for notification in notifications:
        data = notification.as_dict(current_time)
        rows.append(
            [
                data["cluster_name"],
                data["namespace"],
                data["owner"],
                data["expires"],
                f"{data['elapsed_percentage']:.1f}%",
                data["time_remaining"],
            ]
        )

    click.echo(f"\n{colour}{heading}{Style.RESET_ALL}")
    click.echo(tabulate(rows, headers=_TABLE_HEADERS, tablefmt="grid"))


def _cleanup_stale_notifications(
    current: list[ClusterNotification],
    notification_history: NotificationHistory,
) -> int:
    """
    Forget notifications for clusters that are now back in compliance.

    Without this, a cluster that was alerted on for a missing label and then
    fixed would stay marked as notified, and so would never be alerted on again
    when it eventually did expire.

    Args:
        current: The clusters that presently warrant an alert.
        notification_history: Where sent notifications are recorded.

    Returns:
        How many clusters had their history cleared.
    """
    still_relevant = {(n.cluster.namespace, n.cluster.name) for n in current}

    cleaned = 0
    for record in notification_history.get_all_notified_clusters():
        key = (record["namespace"], record["cluster_name"])
        if key not in still_relevant:
            notification_history.clear_cluster_history(
                record["cluster_name"], record["namespace"]
            )
            cleaned += 1
    return cleaned


def execute_notify_command(
    kubeconfig: str | None,
    config: str | None,
    namespace: str | None,
    warning_threshold: int,
    critical_threshold: int,
    grace: str | None = None,
    notify_backend: str | None = None,
    redis: RedisSettings | None = None,
    slack: SlackSettings | None = None,
):
    """
    Execute the notify command.

    Args:
        kubeconfig: Path to kubeconfig file.
        config: Path to configuration file.
        namespace: Namespace to limit the operation to.
        warning_threshold: Percentage of lifetime elapsed for a warning.
        critical_threshold: Percentage of lifetime elapsed for a critical alert.
        grace: Grace period for newly created clusters.
        notify_backend: Backend to send alerts through. Without one, the
            command only reports what it would send.
        redis: Where notification history is stored.
        slack: Slack delivery parameters, when that backend is selected.
    """
    redis = redis or RedisSettings()
    slack = slack or SlackSettings()
    _validate_backend(notify_backend, slack)

    scope = f"namespace '{namespace}'" if namespace else "all namespaces"
    click.echo(
        f"{Fore.BLUE}Checking clusters for notification across {scope}...{Style.RESET_ALL}"
    )

    if notify_backend:
        click.echo(
            f"{Fore.CYAN}Notification backend: {notify_backend}{Style.RESET_ALL}"
        )

    notification_history = None
    if notify_backend:
        try:
            notification_history = NotificationHistory(redis)
            click.echo(
                f"{Fore.CYAN}Connected to notification history at "
                f"{redis}{Style.RESET_ALL}"
            )
        except Exception as e:
            click.echo(
                f"{Fore.RED}Failed to connect to notification history: "
                f"{e}{Style.RESET_ALL}"
            )
            raise click.Abort() from e

    if grace:
        click.echo(
            f"{Fore.CYAN}Grace period: {grace} (clusters younger than this "
            f"will not receive notifications){Style.RESET_ALL}"
        )

    try:
        config_manager = ConfigManager(config) if config else ConfigManager()
        notification_manager = NotificationManager(
            kubeconfig, config_manager, grace_period=grace
        )

        all_notifications = notification_manager.get_notifications(
            warning_threshold, critical_threshold, namespace
        )

        if notification_history:
            click.echo(
                f"{Fore.BLUE}Cleaning up stale notifications...{Style.RESET_ALL}"
            )
            cleaned = _cleanup_stale_notifications(
                all_notifications, notification_history
            )
            if cleaned:
                click.echo(
                    f"{Fore.GREEN}Cleaned up notifications for {cleaned} "
                    f"compliant clusters{Style.RESET_ALL}"
                )
            else:
                click.echo(
                    f"{Fore.GREEN}No compliant clusters with stale "
                    f"notifications found{Style.RESET_ALL}"
                )

        critical = [n for n in all_notifications if n.severity == CRITICAL]
        warning = [n for n in all_notifications if n.severity == WARNING]
        total_matched = len(all_notifications)

        if notification_history:
            critical = notification_history.filter_new_notifications(critical, CRITICAL)
            warning = notification_history.filter_new_notifications(warning, WARNING)

            filtered = total_matched - len(critical) - len(warning)
            if filtered:
                click.echo(
                    f"{Fore.CYAN}Filtered out {filtered} notifications "
                    f"(already sent){Style.RESET_ALL}"
                )

        total_new = len(critical) + len(warning)

        if not total_new:
            if not total_matched:
                click.echo(
                    f"\n{Fore.GREEN}No clusters require notifications at "
                    f"current thresholds.{Style.RESET_ALL}"
                )
            else:
                click.echo(
                    f"\n{Fore.GREEN}No new notifications to send (all "
                    f"{total_matched} clusters have already been "
                    f"notified).{Style.RESET_ALL}"
                )
            click.echo(
                f"{Fore.CYAN}Thresholds: Warning {warning_threshold}%, "
                f"Critical {critical_threshold}%{Style.RESET_ALL}"
            )
            return

        click.echo(
            f"\n{Fore.YELLOW}Found {total_new} clusters requiring "
            f"notifications:{Style.RESET_ALL}"
        )
        if notification_history and total_matched > total_new:
            click.echo(
                f"{Fore.CYAN}({total_matched} total clusters matched "
                f"thresholds, {total_new} are new notifications){Style.RESET_ALL}"
            )
        click.echo(
            f"{Fore.CYAN}Thresholds: Warning {warning_threshold}%, "
            f"Critical {critical_threshold}%{Style.RESET_ALL}"
        )

        if critical:
            _display(
                critical,
                f"🚨 CRITICAL: {len(critical)} clusters (≥{critical_threshold}% elapsed):",
                Fore.RED,
            )
        if warning:
            _display(
                warning,
                f"⚠️  WARNING: {len(warning)} clusters "
                f"({warning_threshold}%-{critical_threshold - 1}% elapsed):",
                Fore.YELLOW,
            )

        click.echo(f"\n{Fore.CYAN}Notification Summary:{Style.RESET_ALL}")
        click.echo(f"  • Critical notifications: {len(critical)}")
        click.echo(f"  • Warning notifications: {len(warning)}")
        click.echo(f"  • Total notifications: {total_new}")

        if notify_backend:
            _send_notifications(
                critical,
                warning,
                notify_backend,
                notification_manager,
                notification_history,
                slack,
                warning_threshold=warning_threshold,
                critical_threshold=critical_threshold,
            )

    except ValueError as e:
        click.echo(f"{Fore.RED}Error: {e}{Style.RESET_ALL}")
        raise click.Abort() from e
    except Exception as e:
        click.echo(f"{Fore.RED}Error: {e}{Style.RESET_ALL}")
        raise click.Abort() from e


def _send_notifications(
    critical: list[ClusterNotification],
    warning: list[ClusterNotification],
    backend: str,
    notification_manager: NotificationManager,
    notification_history: NotificationHistory | None,
    slack: SlackSettings,
    **kwargs,
):
    """
    Deliver the alerts, recording each batch as sent.

    History is updated per severity immediately after that batch is delivered,
    so a failure partway through does not mark undelivered alerts as sent.
    """
    slack_params = slack.as_backend_kwargs()
    channel = slack.channel

    click.echo(
        f"{Fore.CYAN}Sending {len(critical) + len(warning)} notifications to "
        f"Slack channel #{channel}...{Style.RESET_ALL}"
    )

    try:
        for notifications, severity, threshold in (
            (critical, CRITICAL, kwargs.get("critical_threshold", 95)),
            (warning, WARNING, kwargs.get("warning_threshold", 80)),
        ):
            if not notifications:
                continue

            notification_manager.send_expiry_notification(
                backend=backend,
                notifications=notifications,
                severity=severity,
                threshold=threshold,
                **slack_params,
            )
            if notification_history:
                notification_history.mark_clusters_as_notified(notifications, severity)

            click.echo(
                f"{Fore.GREEN}Sent {severity} notification for "
                f"{len(notifications)} clusters to #{channel}{Style.RESET_ALL}"
            )

        click.echo(
            f"{Fore.GREEN}Successfully sent notifications to Slack!{Style.RESET_ALL}"
        )

    except Exception as e:
        click.echo(f"{Fore.RED}Failed to send notifications: {e}{Style.RESET_ALL}")
        raise click.Abort() from e
