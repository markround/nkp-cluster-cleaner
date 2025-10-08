"""
Notify command implementation for the NKP Cluster Cleaner tool.
"""

import click
from colorama import Fore, Style
from tabulate import tabulate
from typing import Optional, List, Tuple
from ..config import ConfigManager
from ..notification_manager import NotificationManager
from ..notification_history import NotificationHistory


def _send_notifications(
    critical_clusters: List[Tuple],
    warning_clusters: List[Tuple],
    backend: str,
    notification_manager: NotificationManager,
    notification_history: Optional[NotificationHistory],
    **kwargs,
):
    """
    Send actual notifications using the specified backend.

    Args:
        critical_clusters: List of critical cluster data
        warning_clusters: List of warning cluster data
        backend: Notification backend to use
        notification_manager: NotificationManager instance
        notification_history: NotificationHistory instance for tracking sent notifications
        **kwargs: Backend-specific parameters
    """
    if backend == "slack":
        _send_slack_expiry_notifications(
            critical_clusters,
            warning_clusters,
            notification_manager,
            notification_history,
            **kwargs,
        )
    else:
        click.echo(
            f"{Fore.RED}Error: Unknown notification backend '{backend}'{Style.RESET_ALL}"
        )
        raise click.Abort()


def _send_slack_expiry_notifications(
    critical_clusters: List[Tuple],
    warning_clusters: List[Tuple],
    notification_manager: NotificationManager,
    notification_history: Optional[NotificationHistory],
    **kwargs,
):
    """
    Send expiry notifications to Slack using the new generic notification methods.

    Args:
        critical_clusters: List of critical cluster data
        warning_clusters: List of warning cluster data
        notification_manager: NotificationManager instance
        notification_history: NotificationHistory instance for tracking sent notifications
        **kwargs: Slack-specific parameters (slack_token, slack_channel, etc.)
    """
    slack_token = kwargs.get("slack_token")
    slack_channel = kwargs.get("slack_channel")
    slack_username = kwargs.get("slack_username", "NKP Cluster Cleaner")
    slack_icon_emoji = kwargs.get("slack_icon_emoji", ":broom:")
    warning_threshold = kwargs.get("warning_threshold", 80)
    critical_threshold = kwargs.get("critical_threshold", 95)

    total_notifications = len(critical_clusters) + len(warning_clusters)

    if total_notifications == 0:
        click.echo(
            f"{Fore.GREEN}No new notifications to send to Slack.{Style.RESET_ALL}"
        )
        return

    click.echo(
        f"{Fore.CYAN}Sending {total_notifications} notifications to Slack channel #{slack_channel}...{Style.RESET_ALL}"
    )

    try:
        # Send critical notifications
        if critical_clusters:
            notification_manager.send_expiry_notification(
                backend="slack",
                clusters=critical_clusters,
                severity="critical",
                threshold=critical_threshold,
                token=slack_token,
                channel=slack_channel,
                username=slack_username,
                icon_emoji=slack_icon_emoji,
            )

            # Mark as notified
            if notification_history:
                notification_history.mark_clusters_as_notified(
                    critical_clusters, "critical"
                )

            click.echo(
                f"{Fore.GREEN}Sent critical notification for {len(critical_clusters)} clusters to #{slack_channel}{Style.RESET_ALL}"
            )

        # Send warning notifications
        if warning_clusters:
            notification_manager.send_expiry_notification(
                backend="slack",
                clusters=warning_clusters,
                severity="warning",
                threshold=warning_threshold,
                token=slack_token,
                channel=slack_channel,
                username=slack_username,
                icon_emoji=slack_icon_emoji,
            )

            # Mark as notified
            if notification_history:
                notification_history.mark_clusters_as_notified(
                    warning_clusters, "warning"
                )

            click.echo(
                f"{Fore.GREEN}Sent warning notification for {len(warning_clusters)} clusters to #{slack_channel}{Style.RESET_ALL}"
            )

        click.echo(
            f"{Fore.GREEN}Successfully sent notifications to Slack!{Style.RESET_ALL}"
        )

    except Exception as e:
        click.echo(
            f"{Fore.RED}Failed to send Slack notifications: {e}{Style.RESET_ALL}"
        )
        raise click.Abort()


def _cleanup_stale_notifications(
    notification_manager: NotificationManager,
    notification_history: NotificationHistory,
    warning_threshold: int,
    critical_threshold: int,
    namespace: Optional[str] = None,
) -> int:
    """
    Clean up notifications for clusters that are currently in compliance.

    Args:
        notification_manager: NotificationManager instance
        notification_history: NotificationHistory instance
        warning_threshold: Warning threshold percentage
        critical_threshold: Critical threshold percentage
        namespace: Namespace filter

    Returns:
        Number of clusters with notifications cleaned up
    """
    # Get all clusters that currently require notifications
    current_critical, current_warning = (
        notification_manager.get_clusters_for_notification(
            warning_threshold, critical_threshold, namespace
        )
    )

    # Create set of cluster keys that currently need any notifications
    clusters_needing_notifications = set()

    for cluster_info, _, _ in current_critical + current_warning:
        cluster_name = cluster_info.get("capi_cluster_name", "unknown")
        cluster_namespace = cluster_info.get("capi_cluster_namespace", "unknown")
        key = f"{cluster_namespace}:{cluster_name}"
        clusters_needing_notifications.add(key)

    # Get all clusters with notification history
    all_notified_clusters = notification_history.get_all_notified_clusters()

    cleaned_count = 0

    for cluster_info in all_notified_clusters:
        cluster_name = cluster_info["cluster_name"]
        cluster_namespace = cluster_info["namespace"]
        key = f"{cluster_namespace}:{cluster_name}"

        # If cluster doesn't need any notifications, clear all its notification history
        if key not in clusters_needing_notifications:
            notification_history.clear_cluster_history(cluster_name, cluster_namespace)
            cleaned_count += 1

    return cleaned_count


def execute_notify_command(
    kubeconfig: Optional[str],
    config: Optional[str],
    namespace: Optional[str],
    warning_threshold: int,
    critical_threshold: int,
    grace: Optional[str] = None,
    notify_backend: Optional[str] = None,
    redis_host: str = "redis",
    redis_port: int = 6379,
    redis_db: int = 0,
    redis_username: Optional[str] = None,
    redis_password: Optional[str] = None,
    **kwargs,
):
    """
    Execute the notify command with the given parameters.

    Args:
        kubeconfig: Path to kubeconfig file
        config: Path to configuration file
        namespace: Namespace to limit operation to
        warning_threshold: Warning threshold percentage
        critical_threshold: Critical threshold percentage
        grace: Grace period for newly created clusters
        notify_backend: Notification backend to use (slack, etc.)
        redis_host: Redis host for notification history
        redis_port: Redis port
        redis_db: Redis database number
        redis_username: Redis username for authentication
        redis_password: Redis password for authentication
        **kwargs: Backend-specific parameters (e.g. slack_token, slack_channel for slack backend)
    """
    # Validate notification backend
    if notify_backend and notify_backend not in NotificationManager.SUPPORTED_BACKENDS:
        click.echo(
            f"{Fore.RED}Error: Unsupported notification backend '{notify_backend}'. Supported backends: {', '.join(NotificationManager.SUPPORTED_BACKENDS)}{Style.RESET_ALL}"
        )
        raise click.Abort()

    # Validate backend-specific requirements
    if notify_backend == "slack":
        if not kwargs.get("slack_token"):
            click.echo(
                f"{Fore.RED}Error: slack_token is required when using slack backend{Style.RESET_ALL}"
            )
            raise click.Abort()
        if not kwargs.get("slack_channel"):
            click.echo(
                f"{Fore.RED}Error: slack_channel is required when using slack backend{Style.RESET_ALL}"
            )
            raise click.Abort()

    if namespace:
        click.echo(
            f"{Fore.BLUE}Checking clusters for notification in namespace '{namespace}'...{Style.RESET_ALL}"
        )
    else:
        click.echo(
            f"{Fore.BLUE}Checking clusters for notification across all namespaces...{Style.RESET_ALL}"
        )

    if notify_backend:
        click.echo(
            f"{Fore.CYAN}Notification backend: {notify_backend}{Style.RESET_ALL}"
        )

    # Initialize notification history if using a backend
    notification_history = None
    if notify_backend:
        try:
            notification_history = NotificationHistory(
                redis_host, redis_port, redis_db, redis_username, redis_password
            )
            click.echo(
                f"{Fore.CYAN}Connected to notification history at {redis_host}:{redis_port} (db {redis_db}){Style.RESET_ALL}"
            )
        except Exception as e:
            click.echo(
                f"{Fore.RED}Failed to connect to notification history: {e}{Style.RESET_ALL}"
            )
            raise click.Abort()

    if grace:
        click.echo(
            f"{Fore.CYAN}Grace period: {grace} (clusters younger than this will not receive notifications){Style.RESET_ALL}"
        )

    try:
        # Initialize configuration and notification manager
        config_manager = ConfigManager(config) if config else ConfigManager()
        notification_manager = NotificationManager(
            kubeconfig, config_manager, grace_period=grace
        )

        # Clean up stale notifications first
        # This can happen if e.g. a cluster was missing tags, a notification got sent, and the user then
        # later added tags. This ensures they will still get notifications when the cluster does expire.
        if notification_history:
            click.echo(
                f"{Fore.BLUE}Cleaning up stale notifications...{Style.RESET_ALL}"
            )
            cleaned_count = _cleanup_stale_notifications(
                notification_manager,
                notification_history,
                warning_threshold,
                critical_threshold,
                namespace,
            )

            if cleaned_count > 0:
                click.echo(
                    f"{Fore.GREEN}Cleaned up notifications for {cleaned_count} compliant clusters{Style.RESET_ALL}"
                )
            else:
                click.echo(
                    f"{Fore.GREEN}No compliant clusters with stale notifications found{Style.RESET_ALL}"
                )

        # Get clusters requiring notifications
        critical_clusters, warning_clusters = (
            notification_manager.get_clusters_for_notification(
                warning_threshold, critical_threshold, namespace
            )
        )

        # Filter out already notified clusters if using a backend
        original_critical_count = len(critical_clusters)
        original_warning_count = len(warning_clusters)

        if notification_history:
            critical_clusters = notification_history.filter_new_notifications(
                critical_clusters, "critical"
            )
            warning_clusters = notification_history.filter_new_notifications(
                warning_clusters, "warning"
            )

            # Show filtering results
            filtered_critical = original_critical_count - len(critical_clusters)
            filtered_warning = original_warning_count - len(warning_clusters)

            if filtered_critical > 0 or filtered_warning > 0:
                click.echo(
                    f"{Fore.CYAN}Filtered out {filtered_critical} critical and {filtered_warning} warning notifications (already sent){Style.RESET_ALL}"
                )

        # Display results
        total_notifications = len(warning_clusters) + len(critical_clusters)
        total_original = original_critical_count + original_warning_count

        if total_notifications == 0:
            if total_original == 0:
                click.echo(
                    f"\n{Fore.GREEN}No clusters require notifications at current thresholds.{Style.RESET_ALL}"
                )
            else:
                click.echo(
                    f"\n{Fore.GREEN}No new notifications to send (all {total_original} clusters have already been notified).{Style.RESET_ALL}"
                )
            click.echo(
                f"{Fore.CYAN}Thresholds: Warning {warning_threshold}%, Critical {critical_threshold}%{Style.RESET_ALL}"
            )
            return

        click.echo(
            f"\n{Fore.YELLOW}Found {total_notifications} clusters requiring notifications:{Style.RESET_ALL}"
        )
        if notification_history and total_original > total_notifications:
            click.echo(
                f"{Fore.CYAN}({total_original} total clusters matched thresholds, {total_notifications} are new notifications){Style.RESET_ALL}"
            )
        click.echo(
            f"{Fore.CYAN}Thresholds: Warning {warning_threshold}%, Critical {critical_threshold}%{Style.RESET_ALL}"
        )

        # Display critical clusters
        if critical_clusters:
            _display_critical_clusters(
                critical_clusters, critical_threshold, notification_manager
            )

        # Display warning clusters
        if warning_clusters:
            _display_warning_clusters(
                warning_clusters,
                warning_threshold,
                critical_threshold,
                notification_manager,
            )

        # Display summary
        _display_summary(critical_clusters, warning_clusters)

        # Send notifications if backend is specified
        if notify_backend:
            _send_notifications(
                critical_clusters,
                warning_clusters,
                notify_backend,
                notification_manager,
                notification_history,
                warning_threshold=warning_threshold,
                critical_threshold=critical_threshold,
                **kwargs,
            )

    except ValueError as e:
        click.echo(f"{Fore.RED}Error: {e}{Style.RESET_ALL}")
        raise click.Abort()
    except Exception as e:
        click.echo(f"{Fore.RED}Error: {e}{Style.RESET_ALL}")
        raise click.Abort()


def _display_critical_clusters(
    critical_clusters,
    critical_threshold: int,
    notification_manager: NotificationManager,
):
    """Display critical clusters in a formatted table."""
    critical_table_data = []
    for cluster_info, elapsed_percentage, expiry_time in critical_clusters:
        cluster_data = notification_manager.get_cluster_notification_data(
            cluster_info, elapsed_percentage, expiry_time
        )

        critical_table_data.append(
            [
                cluster_data["cluster_name"],
                cluster_data["namespace"],
                cluster_data["owner"],
                cluster_data["expires"],
                f"{cluster_data['elapsed_percentage']:.1f}%",
                cluster_data["time_remaining"],
            ]
        )

    headers = ["Cluster Name", "Namespace", "Owner", "Expires", "Elapsed", "Remaining"]
    click.echo(
        f"\n{Fore.RED}🚨 CRITICAL: {len(critical_clusters)} clusters (≥{critical_threshold}% elapsed):{Style.RESET_ALL}"
    )
    click.echo(tabulate(critical_table_data, headers=headers, tablefmt="grid"))


def _display_warning_clusters(
    warning_clusters,
    warning_threshold: int,
    critical_threshold: int,
    notification_manager: NotificationManager,
):
    """Display warning clusters in a formatted table."""
    warning_table_data = []
    for cluster_info, elapsed_percentage, expiry_time in warning_clusters:
        cluster_data = notification_manager.get_cluster_notification_data(
            cluster_info, elapsed_percentage, expiry_time
        )

        warning_table_data.append(
            [
                cluster_data["cluster_name"],
                cluster_data["namespace"],
                cluster_data["owner"],
                cluster_data["expires"],
                f"{cluster_data['elapsed_percentage']:.1f}%",
                cluster_data["time_remaining"],
            ]
        )

    headers = ["Cluster Name", "Namespace", "Owner", "Expires", "Elapsed", "Remaining"]
    click.echo(
        f"\n{Fore.YELLOW}⚠️  WARNING: {len(warning_clusters)} clusters ({warning_threshold}%-{critical_threshold - 1}% elapsed):{Style.RESET_ALL}"
    )
    click.echo(tabulate(warning_table_data, headers=headers, tablefmt="grid"))


def _display_summary(critical_clusters, warning_clusters):
    """Display notification summary."""
    click.echo(f"\n{Fore.CYAN}Notification Summary:{Style.RESET_ALL}")
    click.echo(f"  • Critical notifications: {len(critical_clusters)}")
    click.echo(f"  • Warning notifications: {len(warning_clusters)}")
    click.echo(
        f"  • Total notifications: {len(critical_clusters) + len(warning_clusters)}"
    )
