"""
List clusters command implementation for the NKP Cluster Cleaner tool.
"""

from __future__ import annotations

import click
from colorama import Fore, Style
from tabulate import tabulate

from ..cluster_manager import ClusterManager
from ..config import ConfigManager
from ..models import ClusterState, ClusterStatus

#: Which states appear under the "excluded" heading, and in what order.
EXCLUDED_STATES = [
    ClusterState.MANAGEMENT,
    ClusterState.PROTECTED,
    ClusterState.IN_GRACE,
    ClusterState.ACTIVE,
    ClusterState.NO_TARGET,
]


def _rows(
    statuses: list[ClusterStatus],
    include_state: bool = False,
    include_target: bool = False,
) -> list[list[str]]:
    """
    Build table rows for a group of clusters.

    Args:
        statuses: The clusters to render.
        include_state: Add a State column. Useful when a table mixes states.
        include_target: Add a Target column naming the resource a delete would
            act on — an NKPCluster on NKP 2.18+, a CAPI Cluster before that.

    Returns:
        One row per cluster.
    """
    rows = []
    for status in statuses:
        cluster = status.cluster
        row = [
            cluster.name,
            cluster.namespace,
            cluster.labels.get("owner", "N/A"),
            cluster.labels.get("expires", "N/A"),
        ]
        if include_state:
            row.append(status.state.label)
        if include_target:
            row.append(cluster.target.kind_name if cluster.target else "none")
        row.append(status.verdict.detail)
        rows.append(row)
    return rows


def execute_list_clusters_command(
    kubeconfig: str | None,
    config: str | None,
    namespace: str | None,
    no_exclusions: bool,
    grace: str | None = None,
):
    """
    Execute the list-clusters command.

    Args:
        kubeconfig: Path to kubeconfig file.
        config: Path to configuration file.
        namespace: Namespace to limit the operation to.
        no_exclusions: Skip showing excluded clusters.
        grace: Grace period for newly created clusters.
    """
    scope = f"namespace '{namespace}'" if namespace else "all namespaces"
    click.echo(f"{Fore.BLUE}Listing clusters across {scope}...{Style.RESET_ALL}")

    if grace:
        click.echo(
            f"{Fore.CYAN}Grace period: {grace} (clusters younger than this "
            f"will be excluded){Style.RESET_ALL}"
        )

    try:
        config_manager = ConfigManager(config) if config else ConfigManager()
        cluster_manager = ClusterManager(kubeconfig, config_manager, grace_period=grace)

        click.echo(
            f"{Fore.CYAN}Deletion API: {cluster_manager.api_mode}{Style.RESET_ALL}"
        )

        grouped = cluster_manager.group_by_state(namespace)
        for_deletion = grouped[ClusterState.FOR_DELETION]

        if for_deletion:
            click.echo(
                f"\n{Fore.RED}Found {len(for_deletion)} clusters for "
                f"deletion:{Style.RESET_ALL}"
            )
            click.echo(
                tabulate(
                    _rows(for_deletion, include_target=True),
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
        else:
            click.echo(
                f"\n{Fore.GREEN}No clusters found matching deletion "
                f"criteria.{Style.RESET_ALL}"
            )

        # Deletions in flight are worth calling out separately: they are neither
        # a pending action nor a steady state.
        deleting = grouped[ClusterState.DELETING]
        if deleting:
            click.echo(
                f"\n{Fore.YELLOW}{len(deleting)} clusters are currently being "
                f"deleted:{Style.RESET_ALL}"
            )
            click.echo(
                tabulate(
                    _rows(deleting),
                    headers=["Cluster Name", "Namespace", "Owner", "Expires", "Status"],
                    tablefmt="grid",
                )
            )

        if no_exclusions:
            return

        excluded = [status for state in EXCLUDED_STATES for status in grouped[state]]
        if excluded:
            click.echo(
                f"\n{Fore.CYAN}Found {len(excluded)} excluded clusters:{Style.RESET_ALL}"
            )
            click.echo(
                tabulate(
                    _rows(excluded, include_state=True, include_target=True),
                    headers=[
                        "Cluster Name",
                        "Namespace",
                        "Owner",
                        "Expires",
                        "State",
                        "Target",
                        "Exclusion Reason",
                    ],
                    tablefmt="grid",
                )
            )

    except Exception as e:
        click.echo(f"{Fore.RED}Error: {e}{Style.RESET_ALL}")
        raise click.Abort() from e
