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
from .commands.notify import execute_notify_command

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
    if namespace:
        click.echo(f"{Fore.BLUE}Listing CAPI clusters for deletion in namespace '{namespace}'...{Style.RESET_ALL}")
    else:
        click.echo(f"{Fore.BLUE}Listing CAPI clusters for deletion across all namespaces...{Style.RESET_ALL}")
    
    try:
        # Initialize configuration and cluster manager
        config_manager = ConfigManager(config) if config else ConfigManager()
        cluster_manager = ClusterManager(kubeconfig, config_manager)
        
        # Get all clusters and categorize them
        clusters_to_delete, excluded_clusters = cluster_manager.get_clusters_with_exclusions(namespace)
        
        # Display clusters for deletion
        if clusters_to_delete:
            table_data = []
            for cluster_info, reason in clusters_to_delete:
                capi_cluster_name = cluster_info.get("capi_cluster_name", "unknown")
                capi_cluster_namespace = cluster_info.get("capi_cluster_namespace", "unknown")
                labels = cluster_info.get("labels", {})
                owner = labels.get("owner", "N/A")
                expires = labels.get("expires", "N/A")
                
                table_data.append([
                    capi_cluster_name,
                    capi_cluster_namespace,
                    owner,
                    expires,
                    reason
                ])
            
            headers = ["Cluster Name", "Namespace", "Owner", "Expires", "Reason"]
            click.echo(f"\n{Fore.RED}Found {len(clusters_to_delete)} clusters for deletion:{Style.RESET_ALL}")
            click.echo(tabulate(table_data, headers=headers, tablefmt="grid"))
        else:
            click.echo(f"\n{Fore.GREEN}No clusters found matching deletion criteria.{Style.RESET_ALL}")
        
        # Display excluded clusters (unless --no-exclusions is specified)
        if not no_exclusions:
            if excluded_clusters:
                excluded_table_data = []
                for cluster_info, reason in excluded_clusters:
                    capi_cluster_name = cluster_info.get("capi_cluster_name", "N/A")
                    capi_cluster_namespace = cluster_info.get("capi_cluster_namespace", "N/A")
                    labels = cluster_info.get("labels", {})
                    owner = labels.get("owner", "N/A")
                    expires = labels.get("expires", "N/A")
                    
                    excluded_table_data.append([
                        capi_cluster_name,
                        capi_cluster_namespace,
                        owner,
                        expires,
                        reason
                    ])
                
                headers = ["Cluster Name", "Namespace", "Owner", "Expires", "Exclusion Reason"]
                click.echo(f"\n{Fore.GREEN}Found {len(excluded_clusters)} clusters excluded from deletion:{Style.RESET_ALL}")
                click.echo(tabulate(excluded_table_data, headers=headers, tablefmt="grid"))
            else:
                click.echo(f"\n{Fore.CYAN}No clusters were excluded from deletion.{Style.RESET_ALL}")
        
    except Exception as e:
        click.echo(f"{Fore.RED}Error: {e}{Style.RESET_ALL}")
        raise click.Abort()

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
@click.option(
    '--notify-backend',
    envvar='NOTIFY_BACKEND',
    help='Send deletion notifications via specified backend (supported: slack)'
)
@click.option(
    '--slack-token',
    envvar='SLACK_TOKEN',
    help='Slack Bot User OAuth Token (required for slack notifications)'
)
@click.option(
    '--slack-channel',
    envvar='SLACK_CHANNEL',
    help='Slack channel to send notifications to (required for slack notifications)'
)
@click.option(
    '--slack-username',
    envvar='SLACK_USERNAME',
    default='NKP Cluster Cleaner',
    help='Username to display in Slack messages (default: NKP Cluster Cleaner)'
)
@click.option(
    '--slack-icon-emoji',
    envvar='SLACK_ICON_EMOJI',
    default=':broom:',
    help='Emoji icon for Slack messages (default: :broom:)'
)
@click.option(
    '--redis-host',
    envvar='REDIS_HOST',
    default='redis',
    help='Redis host for notification history (default: redis)'
)
@click.option(
    '--redis-port',
    envvar='REDIS_PORT',
    default=6379,
    type=int,
    help='Redis port (default: 6379)'
)
@click.option(
    '--redis-db',
    envvar='REDIS_DB',
    default=0,
    type=int,
    help='Redis database number (default: 0)'
)
def delete_clusters(kubeconfig, config, namespace, delete, notify_backend, 
                   slack_token, slack_channel, slack_username, slack_icon_emoji,
                   redis_host, redis_port, redis_db):
    """Delete CAPI clusters that match deletion criteria."""
    # Default behavior is dry-run unless --delete is specified
    dry_run = not delete
    
    # Validate notification backend if specified
    if notify_backend:
        if notify_backend not in ["slack"]:
            click.echo(f"{Fore.RED}Error: Unsupported notification backend '{notify_backend}'. Supported: slack{Style.RESET_ALL}")
            raise click.Abort()
        
        # Validate backend-specific requirements
        if notify_backend == "slack":
            if not slack_token:
                click.echo(f"{Fore.RED}Error: --slack-token is required when using slack notification backend{Style.RESET_ALL}")
                raise click.Abort()
            if not slack_channel:
                click.echo(f"{Fore.RED}Error: --slack-channel is required when using slack notification backend{Style.RESET_ALL}")
                raise click.Abort()
    
    if dry_run:
        if namespace:
            click.echo(f"{Fore.YELLOW}[DRY RUN MODE] Simulating cluster deletion in namespace '{namespace}'...{Style.RESET_ALL}")
        else:
            click.echo(f"{Fore.YELLOW}[DRY RUN MODE] Simulating cluster deletion across all namespaces...{Style.RESET_ALL}")
        click.echo(f"{Fore.CYAN}Note: Running in dry-run mode. Use --delete to actually delete clusters.{Style.RESET_ALL}")
    else:
        if namespace:
            click.echo(f"{Fore.RED}Deleting CAPI clusters in namespace '{namespace}'...{Style.RESET_ALL}")
        else:
            click.echo(f"{Fore.RED}Deleting CAPI clusters across all namespaces...{Style.RESET_ALL}")
    
    try:
        # Initialize configuration and cluster manager
        config_manager = ConfigManager(config) if config else ConfigManager()
        cluster_manager = ClusterManager(kubeconfig, config_manager)
        
        # Initialize notification manager if backend is specified
        notification_manager = None
        if notify_backend and not dry_run:
            from .notification_manager import NotificationManager
            notification_manager = NotificationManager(kubeconfig, config_manager)
        
        # Initialize notification history for clearing deleted cluster records
        notification_history = None
        if not dry_run:
            try:
                from .notification_history import NotificationHistory
                notification_history = NotificationHistory(redis_host, redis_port, redis_db)
                click.echo(f"{Fore.CYAN}Connected to notification history at {redis_host}:{redis_port} (db {redis_db}){Style.RESET_ALL}")
            except Exception as e:
                click.echo(f"{Fore.YELLOW}Warning: Could not connect to Redis for notification history: {e}{Style.RESET_ALL}")
                click.echo(f"{Fore.YELLOW}Continuing without clearing notification history...{Style.RESET_ALL}")
        
        # Get clusters that match deletion criteria
        clusters_to_delete, excluded_clusters = cluster_manager.get_clusters_with_exclusions(namespace)
        
        if not clusters_to_delete:
            if dry_run:
                click.echo(f"\n{Fore.GREEN}No clusters found matching deletion criteria (dry-run mode).{Style.RESET_ALL}")
            else:
                click.echo(f"\n{Fore.GREEN}No clusters found matching deletion criteria.{Style.RESET_ALL}")
            return
        
        # Show what will be deleted
        table_data = []
        for cluster_info, reason in clusters_to_delete:
            capi_cluster_name = cluster_info.get("capi_cluster_name", "unknown")
            capi_cluster_namespace = cluster_info.get("capi_cluster_namespace", "unknown")
            labels = cluster_info.get("labels", {})
            owner = labels.get("owner", "N/A")
            expires = labels.get("expires", "N/A")
            
            table_data.append([
                capi_cluster_name,
                capi_cluster_namespace,
                owner,
                expires,
                reason
            ])
        
        headers = ["Cluster Name", "Namespace", "Owner", "Expires", "Reason"]
        if dry_run:
            click.echo(f"\n{Fore.YELLOW}Found {len(clusters_to_delete)} clusters that would be deleted:{Style.RESET_ALL}")
        else:
            click.echo(f"\n{Fore.YELLOW}Found {len(clusters_to_delete)} clusters for deletion:{Style.RESET_ALL}")
        click.echo(tabulate(table_data, headers=headers, tablefmt="grid"))
        
        # Delete clusters (or simulate deletion)
        deleted_count = 0
        failed_count = 0
        successfully_deleted = []  # Track successfully deleted clusters for notifications
        
        for cluster_info, reason in clusters_to_delete:
            capi_cluster_name = cluster_info.get("capi_cluster_name", "unknown")
            capi_cluster_namespace = cluster_info.get("capi_cluster_namespace", "unknown")
            labels = cluster_info.get("labels", {})
            
            if dry_run:
                click.echo(f"{Fore.YELLOW}[DRY RUN] Would delete: {capi_cluster_name} in {capi_cluster_namespace} ({reason}){Style.RESET_ALL}")
                deleted_count += 1
            else:
                if cluster_manager.delete_cluster(capi_cluster_name, capi_cluster_namespace, dry_run):
                    deleted_count += 1
                    # Track successfully deleted cluster for notification
                    successfully_deleted.append({
                        "name": capi_cluster_name,
                        "namespace": capi_cluster_namespace,
                        "owner": labels.get("owner", "unknown"),
                        "reason": reason
                    })
                    
                    # Clear notification history for the deleted cluster
                    if notification_history:
                        try:
                            notification_history.clear_cluster_history(capi_cluster_name, capi_cluster_namespace)
                        except Exception as e:
                            click.echo(f"{Fore.YELLOW}Warning: Could not clear notification history for {capi_cluster_name}: {e}{Style.RESET_ALL}")
                else:
                    failed_count += 1
        
        # Send deletion notification if configured and clusters were deleted
        if notification_manager and successfully_deleted:
            click.echo(f"\n{Fore.CYAN}Sending deletion notification via {notify_backend}...{Style.RESET_ALL}")
            try:
                notification_manager.send_deletion_notification(
                    backend=notify_backend,
                    deleted_clusters=successfully_deleted,
                    severity="info",
                    token=slack_token,
                    channel=slack_channel,
                    username=slack_username,
                    icon_emoji=slack_icon_emoji
                )
                click.echo(f"{Fore.GREEN}Successfully sent deletion notification to {notify_backend}!{Style.RESET_ALL}")
            except Exception as e:
                click.echo(f"{Fore.YELLOW}Warning: Failed to send deletion notification: {e}{Style.RESET_ALL}")
                # Don't fail the entire operation if notification fails
        
        # Summary
        if dry_run:
            click.echo(f"\n{Fore.CYAN}Dry run completed. {deleted_count} clusters would be deleted.{Style.RESET_ALL}")
            click.echo(f"{Fore.CYAN}To actually delete these clusters, run the command again with --delete{Style.RESET_ALL}")
            if notify_backend:
                click.echo(f"{Fore.CYAN}Note: Deletion notifications would be sent via {notify_backend} when running with --delete{Style.RESET_ALL}")
        else:
            click.echo(f"\n{Fore.GREEN}Deletion completed. {deleted_count} clusters deleted successfully.{Style.RESET_ALL}")
            if failed_count > 0:
                click.echo(f"{Fore.RED}{failed_count} clusters failed to delete.{Style.RESET_ALL}")
        
    except Exception as e:
        click.echo(f"{Fore.RED}Error: {e}{Style.RESET_ALL}")
        raise click.Abort()

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
@click.option(
    '--notify-backend',
    envvar='NOTIFY_BACKEND',
    help='Notification backend to use (supported: slack)'
)
@click.option(
    '--slack-token',
    envvar='SLACK_TOKEN',
    help='Slack Bot User OAuth Token (required for slack backend)'
)
@click.option(
    '--slack-channel',
    envvar='SLACK_CHANNEL',
    help='Slack channel to send notifications to (required for slack backend)'
)
@click.option(
    '--slack-username',
    envvar='SLACK_USERNAME',
    default='NKP Cluster Cleaner',
    help='Username to display in Slack messages (default: NKP Cluster Cleaner)'
)
@click.option(
    '--slack-icon-emoji',
    envvar='SLACK_ICON_EMOJI',
    default=':broom:',
    help='Emoji icon for Slack messages (default: :broom:)'
)
@click.option(
    '--redis-host',
    envvar='REDIS_HOST',
    default='redis',
    help='Redis host for notification history (default: redis)'
)
@click.option(
    '--redis-port',
    envvar='REDIS_PORT',
    default=6379,
    type=int,
    help='Redis port (default: 6379)'
)
@click.option(
    '--redis-db',
    envvar='REDIS_DB',
    default=0,
    type=int,
    help='Redis database number (default: 0)'
)
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
@click.option(
    '--redis-host',
    envvar='REDIS_HOST',
    default='redis',
    help='Redis host for analytics data (default: redis)'
)
@click.option(
    '--redis-port',
    envvar='REDIS_PORT',
    default=6379,
    type=int,
    help='Redis port (default: 6379)'
)
@click.option(
    '--redis-db',
    envvar='REDIS_DB',
    default=0,
    type=int,
    help='Redis database number (default: 0)'
)
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
@click.option(
    '--redis-host',
    envvar='REDIS_HOST',
    default='redis',
    help='Redis host (default: redis)'
)
@click.option(
    '--redis-port',
    envvar='REDIS_PORT',
    default=6379,
    type=int,
    help='Redis port (default: 6379)'
)
@click.option(
    '--redis-db',
    envvar='REDIS_DB',
    default=0,
    type=int,
    help='Redis database number (default: 0)'
)
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


if __name__ == '__main__':
    cli()