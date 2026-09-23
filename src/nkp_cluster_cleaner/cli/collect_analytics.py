"""
Collect analytics command implementation for the NKP Cluster Cleaner tool.
"""

from __future__ import annotations

import click
from colorama import Fore, Style

from ..core.config import ConfigManager
from ..storage.collector import RedisDataCollector
from .options import RedisSettings


def execute_collect_analytics_command(
    kubeconfig: str | None,
    config: str | None,
    keep_days: int,
    debug: bool,
    redis: RedisSettings,
):
    """
    Execute the collect-analytics command.

    Args:
        kubeconfig: Path to kubeconfig file.
        config: Path to configuration file.
        keep_days: How many days of snapshots to retain.
        debug: Emit progress output during collection.
        redis: Where to store the snapshot.
    """
    try:
        config_manager = ConfigManager(config) if config else ConfigManager()
        collector = RedisDataCollector(
            kubeconfig_path=kubeconfig,
            config_manager=config_manager,
            redis=redis,
            debug=debug,
        )

        click.echo(f"{Fore.BLUE}Collecting analytics snapshot...{Style.RESET_ALL}")
        snapshot = collector.collect_snapshot(retention_days=keep_days)
        db_stats = collector.get_database_stats()

        counts = snapshot["cluster_counts"]
        metadata = snapshot["collection_metadata"]

        click.echo(
            f"{Fore.GREEN}Analytics snapshot collected successfully!{Style.RESET_ALL}"
        )
        click.echo(f"{Fore.CYAN}Summary:{Style.RESET_ALL}")
        click.echo(f"  • Total clusters found: {metadata['total_clusters_found']}")
        click.echo(f"  • Clusters for deletion: {counts['for_deletion']}")
        click.echo(f"  • Clusters being deleted: {counts['deleting']}")
        click.echo(f"  • Protected clusters: {counts['protected']}")
        click.echo(f"  • Namespaces scanned: {metadata['namespaces_scanned']}")
        click.echo(f"  • Deletion API: {metadata['api_mode']}")
        click.echo(f"  • Retention period: {keep_days} days")
        click.echo(f"  • Redis: {redis}")

        if "error" not in db_stats:
            click.echo(f"  • Total snapshots in Redis: {db_stats['total_snapshots']}")
            if db_stats.get("redis_memory_used"):
                click.echo(f"  • Redis memory usage: {db_stats['redis_memory_used']}")

        if snapshot["label_compliance"]["required_labels"]:
            rate = snapshot["label_compliance"]["overall_compliance_rate"]
            click.echo(f"  • Label compliance: {rate:.1f}%")

    except Exception as e:
        click.echo(f"{Fore.RED}Error collecting analytics: {e}{Style.RESET_ALL}")
        raise click.Abort() from e
