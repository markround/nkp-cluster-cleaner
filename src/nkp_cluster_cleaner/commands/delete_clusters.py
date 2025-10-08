"""
Delete clusters command implementation for the NKP Cluster Cleaner tool.
"""

import click
from colorama import Fore, Style
from tabulate import tabulate
from typing import Optional
from ..cluster_manager import ClusterManager
from ..config import ConfigManager
from ..notification_manager import NotificationManager


def execute_delete_clusters_command(
    kubeconfig: Optional[str],
    config: Optional[str],
    namespace: Optional[str],
    delete: bool,
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
    Execute the delete-clusters command with the given parameters.

    Args:
        kubeconfig: Path to kubeconfig file
        config: Path to configuration file
        namespace: Namespace to limit operation to
        delete: Whether to actually delete clusters (False = dry-run)
        grace: Grace period for newly created clusters
        notify_backend: Notification backend to use (slack, etc.)
        redis_host: Redis host for notification history
        redis_port: Redis port
        redis_db: Redis database number
        redis_username: Redis username for authentication
        redis_password: Redis password for authentication
        **kwargs: Backend-specific parameters (e.g. slack_token, slack_channel for slack backend)
    """
    # Default behavior is dry-run unless --delete is specified
    dry_run = not delete

    # Validate notification backend if specified
    if notify_backend:
        if notify_backend not in NotificationManager.SUPPORTED_BACKENDS:
            click.echo(
                f"{Fore.RED}Error: Unsupported notification backend '{notify_backend}'. Supported: slack{Style.RESET_ALL}"
            )
            raise click.Abort()

        # Validate backend-specific requirements
        if notify_backend == "slack":
            if not kwargs.get("slack_token"):
                click.echo(
                    f"{Fore.RED}Error: --slack-token is required when using slack notification backend{Style.RESET_ALL}"
                )
                raise click.Abort()
            if not kwargs.get("slack_channel"):
                click.echo(
                    f"{Fore.RED}Error: --slack-channel is required when using slack notification backend{Style.RESET_ALL}"
                )
                raise click.Abort()

    if dry_run:
        if namespace:
            click.echo(
                f"{Fore.YELLOW}[DRY RUN MODE] Simulating cluster deletion in namespace '{namespace}'...{Style.RESET_ALL}"
            )
        else:
            click.echo(
                f"{Fore.YELLOW}[DRY RUN MODE] Simulating cluster deletion across all namespaces...{Style.RESET_ALL}"
            )
        click.echo(
            f"{Fore.CYAN}Note: Running in dry-run mode. Use --delete to actually delete clusters.{Style.RESET_ALL}"
        )
    else:
        if namespace:
            click.echo(
                f"{Fore.RED}Deleting CAPI clusters in namespace '{namespace}'...{Style.RESET_ALL}"
            )
        else:
            click.echo(
                f"{Fore.RED}Deleting CAPI clusters across all namespaces...{Style.RESET_ALL}"
            )

    if grace:
        click.echo(
            f"{Fore.CYAN}Grace period: {grace} (clusters younger than this will not be deleted){Style.RESET_ALL}"
        )

    # Initialize notification components if backend is specified
    notification_manager = None

    if notify_backend:
        try:
            config_manager = ConfigManager(config) if config else ConfigManager()
            notification_manager = NotificationManager(kubeconfig, config_manager, grace_period=grace)
            click.echo(
                f"{Fore.CYAN}Notification backend: {notify_backend}{Style.RESET_ALL}"
            )

        except Exception as e:
            click.echo(
                f"{Fore.RED}Failed to initialize notification system: {e}{Style.RESET_ALL}"
            )
            raise click.Abort()

    try:
        # Initialize configuration and cluster manager
        config_manager = ConfigManager(config) if config else ConfigManager()
        cluster_manager = ClusterManager(kubeconfig, config_manager, grace_period=grace)

        # Get clusters that match deletion criteria
        clusters_to_delete, excluded_clusters = (
            cluster_manager.get_clusters_with_exclusions(namespace)
        )

        if not clusters_to_delete:
            if dry_run:
                click.echo(
                    f"\n{Fore.GREEN}No clusters found matching deletion criteria (dry-run mode).{Style.RESET_ALL}"
                )
            else:
                click.echo(
                    f"\n{Fore.GREEN}No clusters found matching deletion criteria.{Style.RESET_ALL}"
                )
            return

        # Show what will be deleted
        table_data = []
        for cluster_info, reason in clusters_to_delete:
            capi_cluster_name = cluster_info.get("capi_cluster_name", "unknown")
            capi_cluster_namespace = cluster_info.get(
                "capi_cluster_namespace", "unknown"
            )
            labels = cluster_info.get("labels", {})
            owner = labels.get("owner", "N/A")
            expires = labels.get("expires", "N/A")

            table_data.append(
                [capi_cluster_name, capi_cluster_namespace, owner, expires, reason]
            )

        headers = ["Cluster Name", "Namespace", "Owner", "Expires", "Reason"]
        if dry_run:
            click.echo(
                f"\n{Fore.YELLOW}Found {len(clusters_to_delete)} clusters that would be deleted:{Style.RESET_ALL}"
            )
        else:
            click.echo(
                f"\n{Fore.YELLOW}Found {len(clusters_to_delete)} clusters for deletion:{Style.RESET_ALL}"
            )
        click.echo(tabulate(table_data, headers=headers, tablefmt="grid"))

        # Delete clusters (or simulate deletion)
        deleted_count = 0
        failed_count = 0
        successfully_deleted = []  # Track successfully deleted clusters for notifications

        for cluster_info, reason in clusters_to_delete:
            capi_cluster_name = cluster_info.get("capi_cluster_name", "unknown")
            capi_cluster_namespace = cluster_info.get(
                "capi_cluster_namespace", "unknown"
            )
            labels = cluster_info.get("labels", {})

            if dry_run:
                click.echo(
                    f"{Fore.YELLOW}[DRY RUN] Would delete: {capi_cluster_name} in {capi_cluster_namespace} ({reason}){Style.RESET_ALL}"
                )
                deleted_count += 1
            else:
                if cluster_manager.delete_cluster(
                    capi_cluster_name, capi_cluster_namespace, dry_run
                ):
                    deleted_count += 1
                    # Track successfully deleted cluster for notification
                    successfully_deleted.append(
                        {
                            "name": capi_cluster_name,
                            "namespace": capi_cluster_namespace,
                            "owner": labels.get("owner", "unknown"),
                            "reason": reason,
                        }
                    )

                else:
                    failed_count += 1

        # Send deletion notification if configured and clusters were deleted
        if notification_manager and successfully_deleted:
            click.echo(
                f"\n{Fore.CYAN}Sending deletion notification via {notify_backend}...{Style.RESET_ALL}"
            )
            try:
                # Extract backend-specific parameters from kwargs
                backend_params = {
                    "token": kwargs.get("slack_token"),
                    "channel": kwargs.get("slack_channel"),
                    "username": kwargs.get("slack_username", "NKP Cluster Cleaner"),
                    "icon_emoji": kwargs.get("slack_icon_emoji", ":broom:"),
                }

                notification_manager.send_deletion_notification(
                    backend=notify_backend,
                    deleted_clusters=successfully_deleted,
                    severity="info",
                    **backend_params,
                )
                click.echo(
                    f"{Fore.GREEN}Successfully sent deletion notification to {notify_backend}!{Style.RESET_ALL}"
                )
            except Exception as e:
                click.echo(
                    f"{Fore.YELLOW}Warning: Failed to send deletion notification: {e}{Style.RESET_ALL}"
                )
                # Don't fail the entire operation if notification fails

        # Summary
        if dry_run:
            click.echo(
                f"\n{Fore.CYAN}Dry run completed. {deleted_count} clusters would be deleted.{Style.RESET_ALL}"
            )
            click.echo(
                f"{Fore.CYAN}To actually delete these clusters, run the command again with --delete{Style.RESET_ALL}"
            )
            if notify_backend:
                click.echo(
                    f"{Fore.CYAN}Note: Deletion notifications would be sent via {notify_backend} when running with --delete{Style.RESET_ALL}"
                )
        else:
            click.echo(
                f"\n{Fore.GREEN}Deletion completed. {deleted_count} clusters deleted successfully.{Style.RESET_ALL}"
            )
            if failed_count > 0:
                click.echo(
                    f"{Fore.RED}{failed_count} clusters failed to delete.{Style.RESET_ALL}"
                )

    except Exception as e:
        click.echo(f"{Fore.RED}Error: {e}{Style.RESET_ALL}")
        raise click.Abort()
