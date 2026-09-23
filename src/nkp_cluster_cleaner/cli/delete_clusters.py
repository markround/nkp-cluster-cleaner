"""
Delete clusters command implementation for the NKP Cluster Cleaner tool.
"""

from __future__ import annotations

import click
from colorama import Fore, Style
from tabulate import tabulate

from ..core.config import ConfigManager
from ..core.models import ClusterState
from ..core.settings import SlackSettings
from ..k8s.clusters import ClusterManager
from ..notifications.manager import NotificationManager


def _validate_backend(notify_backend: str | None, slack: SlackSettings):
    """
    Check that a notification backend is usable before doing any work.

    Args:
        notify_backend: The requested backend, or None.
        slack: Slack parameters, checked when that backend is selected.

    Raises:
        click.Abort: If the backend is unknown or incompletely configured.
    """
    if not notify_backend:
        return

    if notify_backend not in NotificationManager.SUPPORTED_BACKENDS:
        click.echo(
            f"{Fore.RED}Error: Unsupported notification backend "
            f"'{notify_backend}'. Supported: "
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
                    f"{Fore.RED}Error: {flag} is required when using the slack "
                    f"notification backend{Style.RESET_ALL}"
                )
                raise click.Abort()


def execute_delete_clusters_command(
    kubeconfig: str | None,
    config: str | None,
    namespace: str | None,
    delete: bool,
    grace: str | None = None,
    notify_backend: str | None = None,
    slack: SlackSettings | None = None,
):
    """
    Execute the delete-clusters command.

    Args:
        kubeconfig: Path to kubeconfig file.
        config: Path to configuration file.
        namespace: Namespace to limit the operation to.
        delete: Actually delete. Without this the command is a dry run.
        grace: Grace period for newly created clusters.
        notify_backend: Notification backend to report deletions through.
        slack: Slack delivery parameters, when that backend is selected.
    """
    dry_run = not delete
    slack = slack or SlackSettings()
    _validate_backend(notify_backend, slack)

    scope = f"namespace '{namespace}'" if namespace else "all namespaces"
    if dry_run:
        click.echo(
            f"{Fore.YELLOW}[DRY RUN MODE] Simulating cluster deletion across "
            f"{scope}...{Style.RESET_ALL}"
        )
        click.echo(
            f"{Fore.CYAN}Note: Running in dry-run mode. Use --delete to "
            f"actually delete clusters.{Style.RESET_ALL}"
        )
    else:
        click.echo(f"{Fore.RED}Deleting clusters across {scope}...{Style.RESET_ALL}")

    if grace:
        click.echo(
            f"{Fore.CYAN}Grace period: {grace} (clusters younger than this "
            f"will not be deleted){Style.RESET_ALL}"
        )

    notification_manager = None
    if notify_backend:
        try:
            config_manager = ConfigManager(config) if config else ConfigManager()
            notification_manager = NotificationManager(
                kubeconfig, config_manager, grace_period=grace
            )
            click.echo(
                f"{Fore.CYAN}Notification backend: {notify_backend}{Style.RESET_ALL}"
            )
        except Exception as e:
            click.echo(
                f"{Fore.RED}Failed to initialize notification system: "
                f"{e}{Style.RESET_ALL}"
            )
            raise click.Abort() from e

    try:
        config_manager = ConfigManager(config) if config else ConfigManager()
        cluster_manager = ClusterManager(kubeconfig, config_manager, grace_period=grace)

        click.echo(
            f"{Fore.CYAN}Deletion API: {cluster_manager.api_mode}{Style.RESET_ALL}"
        )

        grouped = cluster_manager.group_by_state(namespace)
        to_delete = grouped[ClusterState.FOR_DELETION]
        in_flight = grouped[ClusterState.DELETING]

        if in_flight:
            click.echo(
                f"{Fore.CYAN}Skipping {len(in_flight)} clusters already being "
                f"deleted{Style.RESET_ALL}"
            )

        if not to_delete:
            suffix = " (dry-run mode)" if dry_run else ""
            click.echo(
                f"\n{Fore.GREEN}No clusters found matching deletion "
                f"criteria{suffix}.{Style.RESET_ALL}"
            )
            return

        verb = "would be deleted" if dry_run else "for deletion"
        click.echo(
            f"\n{Fore.YELLOW}Found {len(to_delete)} clusters {verb}:{Style.RESET_ALL}"
        )
        click.echo(
            tabulate(
                [
                    [
                        status.cluster.name,
                        status.cluster.namespace,
                        status.cluster.labels.get("owner", "N/A"),
                        status.cluster.labels.get("expires", "N/A"),
                        # Show what will actually be deleted, so the operator can
                        # see whether this is an NKPCluster or a CAPI Cluster.
                        status.cluster.target.kind_name
                        if status.cluster.target
                        else "none",
                        status.verdict.detail,
                    ]
                    for status in to_delete
                ],
                headers=[
                    "Cluster Name",
                    "Namespace",
                    "Owner",
                    "Expires",
                    "Target",
                    "Reason",
                ],
                tablefmt="grid",
            )
        )

        deleted_count = 0
        failed_count = 0
        successfully_deleted = []

        for status in to_delete:
            cluster = status.cluster
            if cluster_manager.delete_cluster(cluster, dry_run):
                deleted_count += 1
                successfully_deleted.append(
                    {
                        "name": cluster.name,
                        "namespace": cluster.namespace,
                        "owner": cluster.owner,
                        "reason": status.verdict.detail,
                    }
                )
            else:
                failed_count += 1

        if notification_manager and successfully_deleted and not dry_run:
            _send_deletion_notification(
                notification_manager, notify_backend, successfully_deleted, slack
            )

        if dry_run:
            click.echo(
                f"\n{Fore.CYAN}Dry run completed. {deleted_count} clusters "
                f"would be deleted.{Style.RESET_ALL}"
            )
            click.echo(
                f"{Fore.CYAN}To actually delete these clusters, run the "
                f"command again with --delete{Style.RESET_ALL}"
            )
            if notify_backend:
                click.echo(
                    f"{Fore.CYAN}Note: Deletion notifications would be sent via "
                    f"{notify_backend} when running with --delete{Style.RESET_ALL}"
                )
        else:
            click.echo(
                f"\n{Fore.GREEN}Deletion requested for {deleted_count} "
                f"clusters.{Style.RESET_ALL}"
            )
            if cluster_manager.api_mode == "nkpcluster":
                click.echo(
                    f"{Fore.CYAN}Teardown runs asynchronously and can take some "
                    f"time; these clusters will report as 'Deleting' until it "
                    f"completes.{Style.RESET_ALL}"
                )
            if failed_count:
                click.echo(
                    f"{Fore.RED}{failed_count} clusters failed to "
                    f"delete.{Style.RESET_ALL}"
                )

    except Exception as e:
        click.echo(f"{Fore.RED}Error: {e}{Style.RESET_ALL}")
        raise click.Abort() from e


def _send_deletion_notification(
    notification_manager: NotificationManager,
    notify_backend: str,
    deleted_clusters: list[dict],
    slack: SlackSettings,
):
    """
    Report the deletions, without letting a delivery failure fail the command.

    The clusters are already gone by this point, so an unreachable Slack is not
    a reason to exit non-zero.
    """
    click.echo(
        f"\n{Fore.CYAN}Sending deletion notification via "
        f"{notify_backend}...{Style.RESET_ALL}"
    )
    try:
        notification_manager.send_deletion_notification(
            backend=notify_backend,
            deleted_clusters=deleted_clusters,
            severity="info",
            **slack.as_backend_kwargs(),
        )
        click.echo(
            f"{Fore.GREEN}Successfully sent deletion notification to "
            f"{notify_backend}!{Style.RESET_ALL}"
        )
    except Exception as e:
        click.echo(
            f"{Fore.YELLOW}Warning: Failed to send deletion notification: "
            f"{e}{Style.RESET_ALL}"
        )
