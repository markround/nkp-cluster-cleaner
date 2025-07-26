#!/usr/bin/env python3
"""
Main entry point for the NKP Cluster Cleaner tool.
"""

import click
from colorama import init, Fore, Style
from tabulate import tabulate
from .cluster_manager import ClusterManager
from .config import ConfigManager
from .redis_data_collector import RedisDataCollector
# These commands were making this file too long and hard to read
from .commands.notify import execute_notify_command
from .commands.list_clusters import execute_list_clusters_command
from .commands.delete_clusters import execute_delete_clusters_command

# Initialize colorama
init()

# Common options that are used across multiple commands
def common_options(f):
    """Decorator to add common kubeconfig and config options."""
    f = click.option(
        '--kubeconfig',
        envvar='KUBECONFIG',
        type=click.Path(exists=True),
        help='Path to kubeconfig file (default: ~/.kube/config or $KUBECONFIG)'
    )(f)
    f = click.option(
        '--config',
        envvar='CONFIG',
        type=click.Path(exists=True),
        help='Path to configuration file for protection rules'
    )(f)
    return f

def namespace_option(f):
    """Decorator to add namespace option."""
    return click.option(
        '--namespace',
        envvar='NAMESPACE',
        help='Limit operation to specific namespace (default: examine all namespaces)'
    )(f)

def redis_options(f):
    """Decorator to add Redis connection options."""
    f = click.option(
        '--redis-host',
        envvar='REDIS_HOST',
        default='redis',
        help='Redis host (default: redis)'
    )(f)
    f = click.option(
        '--redis-port',
        envvar='REDIS_PORT',
        default=6379,
        type=int,
        help='Redis port (default: 6379)'
    )(f)
    f = click.option(
        '--redis-db',
        envvar='REDIS_DB',
        default=0,
        type=int,
        help='Redis database number (default: 0)'
    )(f)
    return f

def slack_options(f):
    """Decorator to add Slack notification options."""
    f = click.option(
        '--slack-token',
        envvar='SLACK_TOKEN',
        help='Slack Bot User OAuth Token (required for slack backend)'
    )(f)
    f = click.option(
        '--slack-channel',
        envvar='SLACK_CHANNEL',
        help='Slack channel to send notifications to (required for slack backend)'
    )(f)
    f = click.option(
        '--slack-username',
        envvar='SLACK_USERNAME',
        default='NKP Cluster Cleaner',
        help='Username to display in Slack messages (default: NKP Cluster Cleaner)'
    )(f)
    f = click.option(
        '--slack-icon-emoji',
        envvar='SLACK_ICON_EMOJI',
        default=':broom:',
        help='Emoji icon for Slack messages (default: :broom:)'
    )(f)
    return f

def notification_backend_option(f):
    """Decorator to add notification backend option."""
    return click.option(
        '--notify-backend',
        envvar='NOTIFY_BACKEND',
        help='Notification backend to use (supported: slack)'
    )(f)

@click.group()
# Built-in function with click!
@click.version_option()
def cli():
    """NKP Cluster Cleaner - Delete CAPI clusters based on label criteria."""
    pass

#
# List
#
@cli.command()
@common_options
@namespace_option
@click.option(
    '--no-exclusions',
    envvar='NO_EXCLUSIONS',
    is_flag=True,
    help='Skip showing excluded clusters (only show clusters for deletion)'
)
def list_clusters(kubeconfig, config, namespace, no_exclusions):
    """List CAPI clusters that match deletion criteria."""
    execute_list_clusters_command(
        kubeconfig=kubeconfig,
        config=config,
        namespace=namespace,
        no_exclusions=no_exclusions
    )

#
# Delete
#
@cli.command()
@common_options
@namespace_option
@click.option(
    '--delete',
    envvar='DELETE',
    is_flag=True,
    help='Actually delete clusters (default: dry-run mode)'
)
@notification_backend_option
@slack_options
@redis_options
def delete_clusters(kubeconfig, config, namespace, delete, notify_backend,
                   redis_host, redis_port, redis_db, **kwargs):
    """Delete CAPI clusters that match deletion criteria."""
    # Filter out None values from kwargs to only pass relevant backend parameters
    backend_params = {k: v for k, v in kwargs.items() if v is not None}
    
    execute_delete_clusters_command(
        kubeconfig=kubeconfig,
        config=config,
        namespace=namespace,
        delete=delete,
        notify_backend=notify_backend,
        redis_host=redis_host,
        redis_port=redis_port,
        redis_db=redis_db,
        **backend_params
    )

#
# Notify
#
@cli.command()
@common_options
@namespace_option
@click.option(
    '--warning-threshold',
    envvar='WARNING_THRESHOLD',
    default=80,
    type=int,
    help='Warning threshold percentage (0-100) of time elapsed (default: 80)'
)
@click.option(
    '--critical-threshold',
    envvar='CRITICAL_THRESHOLD',
    default=95,
    type=int,
    help='Critical threshold percentage (0-100) of time elapsed (default: 95)'
)
@notification_backend_option
@slack_options
@redis_options
def notify(kubeconfig, config, namespace, warning_threshold, critical_threshold, 
          notify_backend, redis_host, redis_port, redis_db, **kwargs):
    """Send notifications for clusters approaching deletion."""
    # Filter out None values from kwargs to only pass relevant backend parameters
    backend_params = {k: v for k, v in kwargs.items() if v is not None}
    
    execute_notify_command(
        kubeconfig=kubeconfig,
        config=config, 
        namespace=namespace,
        warning_threshold=warning_threshold,
        critical_threshold=critical_threshold,
        notify_backend=notify_backend,
        redis_host=redis_host,
        redis_port=redis_port,
        redis_db=redis_db,
        **backend_params
    )

#
# Example config
#
@cli.command()
@click.argument('output_file', type=click.Path())
def generate_config(output_file):
    """Generate an example configuration file."""
    config_manager = ConfigManager()
    config_manager.save_example_config(output_file)
    click.echo(f"{Fore.GREEN}Example configuration saved to {output_file}{Style.RESET_ALL}")

#
# Web server
#
@cli.command()
@common_options
@click.option(
    '--host',
    envvar='HOST',
    default='127.0.0.1',
    help='Host to bind to (default: 127.0.0.1)'
)
@click.option(
    '--port',
    envvar='PORT',
    default=8080,
    help='Port to bind to (default: 8080)'
)
@click.option(
    '--debug',
    envvar='DEBUG',
    is_flag=True,
    help='Enable debug mode'
)
@click.option(
    '--prefix',
    envvar='PREFIX',
    default='',
    help='URL prefix for all routes (e.g., /foo for /foo/clusters)'
)
@redis_options
@click.option(
    '--no-analytics',
    envvar='NO_ANALYTICS',
    is_flag=True,
    help='Disable analytics and do not connect to Redis'
)
def serve(kubeconfig, config, host, port, debug, prefix, redis_host, redis_port, redis_db, no_analytics):
    """Start the web server for the cluster cleaner UI."""
    try:
        from .web_server import run_server
        run_server(
            host=host,
            port=port,
            debug=debug,
            kubeconfig_path=kubeconfig,
            config_path=config,
            url_prefix=prefix,
            redis_host=redis_host,
            redis_port=redis_port,
            redis_db=redis_db,
            no_analytics=no_analytics
        )
    except KeyboardInterrupt:
        click.echo(f"\n{Fore.YELLOW}Server stopped by user.{Style.RESET_ALL}")
    except Exception as e:
        click.echo(f"{Fore.RED}Error starting server: {e}{Style.RESET_ALL}")
        raise click.Abort()

#
# Analytics
#
@cli.command()
@common_options
@click.option(
    '--keep-days',
    envvar='KEEP_DAYS',
    default=90,
    type=int,
    help='Number of days of analytics data to retain (default: 90)'
)
@click.option(
    '--debug',
    envvar='DEBUG',
    is_flag=True,
    help='Enable debug output during collection'
)
@redis_options
def collect_analytics(kubeconfig, config, keep_days, debug, redis_host, redis_port, redis_db):
    """Collect analytics snapshot for historical tracking and reporting."""
    try:
        # Initialize configuration and data collector
        config_manager = ConfigManager(config) if config else ConfigManager()
        data_collector = RedisDataCollector(
            kubeconfig_path=kubeconfig,
            config_manager=config_manager,
            redis_host=redis_host,
            redis_port=redis_port,
            redis_db=redis_db,
            debug=debug
        )
        
        # Collect snapshot
        click.echo(f"{Fore.BLUE}Collecting analytics snapshot...{Style.RESET_ALL}")
        snapshot = data_collector.collect_snapshot(retention_days=keep_days)
        
        # Get database stats
        db_stats = data_collector.get_database_stats()
        
        # Display summary
        click.echo(f"{Fore.GREEN}Analytics snapshot collected successfully!{Style.RESET_ALL}")
        click.echo(f"{Fore.CYAN}Summary:{Style.RESET_ALL}")
        click.echo(f"  • Total clusters found: {snapshot['collection_metadata']['total_clusters_found']}")
        click.echo(f"  • Clusters for deletion: {snapshot['cluster_counts']['for_deletion']}")
        click.echo(f"  • Protected clusters: {snapshot['cluster_counts']['protected']}")
        click.echo(f"  • Namespaces scanned: {snapshot['collection_metadata']['namespaces_scanned']}")
        click.echo(f"  • Retention period: {keep_days} days")
        click.echo(f"  • Redis: {redis_host}:{redis_port} (db {redis_db})")
        
        if 'error' not in db_stats:
            click.echo(f"  • Total snapshots in Redis: {db_stats['total_snapshots']}")
            if db_stats.get('redis_memory_used'):
                click.echo(f"  • Redis memory usage: {db_stats['redis_memory_used']}")
        
        # Show compliance summary if configured
        if snapshot['label_compliance']['required_labels']:
            compliance_rate = snapshot['label_compliance']['overall_compliance_rate']
            click.echo(f"  • Label compliance: {compliance_rate:.1f}%")
        
    except Exception as e:
        click.echo(f"{Fore.RED}Error collecting analytics: {e}{Style.RESET_ALL}")
        raise click.Abort()

if __name__ == "__main__":
    cli()