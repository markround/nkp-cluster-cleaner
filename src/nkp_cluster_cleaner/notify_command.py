"""
Notify command implementation for the NKP Cluster Cleaner tool.
"""

import click
import redis
from datetime import datetime, timedelta
from colorama import Fore, Style
from tabulate import tabulate
from typing import Optional, List, Tuple, Dict, Any, Set
from .config import ConfigManager
from .notification_manager import NotificationManager


# Supported notification backends
SUPPORTED_BACKENDS = ["slack"]


class NotificationHistory:
    """Manages notification history using Redis to prevent duplicate alerts."""
    
    def __init__(self, redis_host: str = 'redis', redis_port: int = 6379, redis_db: int = 0):
        """
        Initialize notification history manager.
        
        Args:
            redis_host: Redis host
            redis_port: Redis port  
            redis_db: Redis database number
        """
        self.redis_client = redis.Redis(
            host=redis_host,
            port=redis_port,
            db=redis_db,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True
        )
        
        # Test connection
        try:
            self.redis_client.ping()
        except redis.ConnectionError as e:
            raise Exception(f"Failed to connect to Redis at {redis_host}:{redis_port}: {e}")
    
    def _get_cluster_key(self, cluster_name: str, namespace: str) -> str:
        """Generate Redis key for cluster notification history."""
        return f"notifications:cluster:{namespace}:{cluster_name}"
    
    def has_been_notified(self, cluster_name: str, namespace: str, severity: str) -> bool:
        """
        Check if a cluster has already been notified at this severity level.
        
        Args:
            cluster_name: Name of the cluster
            namespace: Namespace of the cluster
            severity: "warning" or "critical"
            
        Returns:
            True if already notified at this severity level
        """
        key = self._get_cluster_key(cluster_name, namespace)
        return self.redis_client.sismember(key, severity)
    
    def mark_as_notified(self, cluster_name: str, namespace: str, severity: str, ttl_days: int = 30):
        """
        Mark a cluster as notified at the given severity level.
        
        Args:
            cluster_name: Name of the cluster
            namespace: Namespace of the cluster
            severity: "warning" or "critical"
            ttl_days: Number of days to keep the notification history
        """
        key = self._get_cluster_key(cluster_name, namespace)
        pipe = self.redis_client.pipeline()
        pipe.sadd(key, severity)
        pipe.expire(key, ttl_days * 24 * 60 * 60)  # Convert days to seconds
        pipe.execute()
    
    def filter_new_notifications(self, clusters: List[Tuple], severity: str) -> List[Tuple]:
        """
        Filter out clusters that have already been notified at this severity level.
        
        Args:
            clusters: List of (cluster_info, elapsed_percentage, expiry_time) tuples
            severity: "warning" or "critical"
            
        Returns:
            Filtered list of clusters that haven't been notified yet
        """
        new_clusters = []
        for cluster_info, elapsed_percentage, expiry_time in clusters:
            cluster_name = cluster_info.get("capi_cluster_name", "unknown")
            namespace = cluster_info.get("capi_cluster_namespace", "unknown")
            
            if not self.has_been_notified(cluster_name, namespace, severity):
                new_clusters.append((cluster_info, elapsed_percentage, expiry_time))
        
        return new_clusters
    
    def mark_clusters_as_notified(self, clusters: List[Tuple], severity: str):
        """
        Mark multiple clusters as notified at the given severity level.
        
        Args:
            clusters: List of (cluster_info, elapsed_percentage, expiry_time) tuples
            severity: "warning" or "critical"
        """
        for cluster_info, elapsed_percentage, expiry_time in clusters:
            cluster_name = cluster_info.get("capi_cluster_name", "unknown")
            namespace = cluster_info.get("capi_cluster_namespace", "unknown")
            self.mark_as_notified(cluster_name, namespace, severity)


def execute_notify_command(kubeconfig: Optional[str], config: Optional[str], namespace: Optional[str], 
                          warning_threshold: int, critical_threshold: int, notify_backend: Optional[str] = None,
                          slack_token: Optional[str] = None, slack_channel: Optional[str] = None,
                          slack_username: str = "NKP Cluster Cleaner", slack_icon_emoji: str = ":warning:",
                          redis_host: str = 'redis', redis_port: int = 6379, redis_db: int = 0):
    """
    Execute the notify command with the given parameters.
    
    Args:
        kubeconfig: Path to kubeconfig file
        config: Path to configuration file
        namespace: Namespace to limit operation to
        warning_threshold: Warning threshold percentage
        critical_threshold: Critical threshold percentage
        notify_backend: Notification backend to use (slack, etc.)
        slack_token: Slack Bot User OAuth Token
        slack_channel: Slack channel to send notifications to
        slack_username: Username to display in Slack messages
        slack_icon_emoji: Emoji icon for Slack messages
        redis_host: Redis host for notification history
        redis_port: Redis port
        redis_db: Redis database number
    """
    # Validate notification backend
    if notify_backend and notify_backend not in SUPPORTED_BACKENDS:
        click.echo(f"{Fore.RED}Error: Unsupported notification backend '{notify_backend}'. Supported backends: {', '.join(SUPPORTED_BACKENDS)}{Style.RESET_ALL}")
        raise click.Abort()
    
    # Validate backend-specific requirements
    if notify_backend == "slack":
        if not slack_token:
            click.echo(f"{Fore.RED}Error: --slack-token is required when using slack backend{Style.RESET_ALL}")
            raise click.Abort()
        if not slack_channel:
            click.echo(f"{Fore.RED}Error: --slack-channel is required when using slack backend{Style.RESET_ALL}")
            raise click.Abort()
    
    if namespace:
        click.echo(f"{Fore.BLUE}Checking clusters for notification in namespace '{namespace}'...{Style.RESET_ALL}")
    else:
        click.echo(f"{Fore.BLUE}Checking clusters for notification across all namespaces...{Style.RESET_ALL}")
    
    if notify_backend:
        click.echo(f"{Fore.CYAN}Notification backend: {notify_backend}{Style.RESET_ALL}")
    
    # Initialize notification history if using a backend
    notification_history = None
    if notify_backend:
        try:
            notification_history = NotificationHistory(redis_host, redis_port, redis_db)
            click.echo(f"{Fore.CYAN}Connected to notification history at {redis_host}:{redis_port} (db {redis_db}){Style.RESET_ALL}")
        except Exception as e:
            click.echo(f"{Fore.RED}Failed to connect to notification history: {e}{Style.RESET_ALL}")
            raise click.Abort()
    
    try:
        # Initialize configuration and notification manager
        config_manager = ConfigManager(config) if config else ConfigManager()
        notification_manager = NotificationManager(kubeconfig, config_manager)
        
        # Get clusters requiring notifications
        critical_clusters, warning_clusters = notification_manager.get_clusters_for_notification(
            warning_threshold, critical_threshold, namespace
        )
        
        # Filter out already notified clusters if using a backend
        original_critical_count = len(critical_clusters)
        original_warning_count = len(warning_clusters)
        
        if notification_history:
            critical_clusters = notification_history.filter_new_notifications(critical_clusters, "critical")
            warning_clusters = notification_history.filter_new_notifications(warning_clusters, "warning")
            
            # Show filtering results
            filtered_critical = original_critical_count - len(critical_clusters)
            filtered_warning = original_warning_count - len(warning_clusters)
            
            if filtered_critical > 0 or filtered_warning > 0:
                click.echo(f"{Fore.CYAN}Filtered out {filtered_critical} critical and {filtered_warning} warning notifications (already sent){Style.RESET_ALL}")
        
        # Display results
        total_notifications = len(warning_clusters) + len(critical_clusters)
        total_original = original_critical_count + original_warning_count
        
        if total_notifications == 0:
            if total_original == 0:
                click.echo(f"\n{Fore.GREEN}No clusters require notifications at current thresholds.{Style.RESET_ALL}")
            else:
                click.echo(f"\n{Fore.GREEN}No new notifications to send (all {total_original} clusters have already been notified).{Style.RESET_ALL}")
            click.echo(f"{Fore.CYAN}Thresholds: Warning {warning_threshold}%, Critical {critical_threshold}%{Style.RESET_ALL}")
            return
        
        click.echo(f"\n{Fore.YELLOW}Found {total_notifications} clusters requiring notifications:{Style.RESET_ALL}")
        if notification_history and total_original > total_notifications:
            click.echo(f"{Fore.CYAN}({total_original} total clusters matched thresholds, {total_notifications} are new notifications){Style.RESET_ALL}")
        click.echo(f"{Fore.CYAN}Thresholds: Warning {warning_threshold}%, Critical {critical_threshold}%{Style.RESET_ALL}")
        
        # Display critical clusters
        if critical_clusters:
            _display_critical_clusters(critical_clusters, critical_threshold, notification_manager)
        
        # Display warning clusters
        if warning_clusters:
            _display_warning_clusters(warning_clusters, warning_threshold, critical_threshold, notification_manager)
        
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
                slack_token=slack_token,
                slack_channel=slack_channel, 
                slack_username=slack_username,
                slack_icon_emoji=slack_icon_emoji
            )
        
    except ValueError as e:
        click.echo(f"{Fore.RED}Error: {e}{Style.RESET_ALL}")
        raise click.Abort()
    except Exception as e:
        click.echo(f"{Fore.RED}Error: {e}{Style.RESET_ALL}")
        raise click.Abort()


def _display_critical_clusters(critical_clusters, critical_threshold: int, notification_manager: NotificationManager):
    """Display critical clusters in a formatted table."""
    critical_table_data = []
    for cluster_info, elapsed_percentage, expiry_time in critical_clusters:
        cluster_data = notification_manager.get_cluster_notification_data(
            cluster_info, elapsed_percentage, expiry_time
        )
        
        critical_table_data.append([
            cluster_data["cluster_name"],
            cluster_data["namespace"],
            cluster_data["owner"],
            cluster_data["expires"],
            f"{cluster_data['elapsed_percentage']:.1f}%",
            cluster_data["time_remaining"]
        ])
    
    headers = ["Cluster Name", "Namespace", "Owner", "Expires", "Elapsed", "Remaining"]
    click.echo(f"\n{Fore.RED}🚨 CRITICAL: {len(critical_clusters)} clusters (≥{critical_threshold}% elapsed):{Style.RESET_ALL}")
    click.echo(tabulate(critical_table_data, headers=headers, tablefmt="grid"))


def _display_warning_clusters(warning_clusters, warning_threshold: int, critical_threshold: int, 
                             notification_manager: NotificationManager):
    """Display warning clusters in a formatted table."""
    warning_table_data = []
    for cluster_info, elapsed_percentage, expiry_time in warning_clusters:
        cluster_data = notification_manager.get_cluster_notification_data(
            cluster_info, elapsed_percentage, expiry_time
        )
        
        warning_table_data.append([
            cluster_data["cluster_name"],
            cluster_data["namespace"],
            cluster_data["owner"],
            cluster_data["expires"],
            f"{cluster_data['elapsed_percentage']:.1f}%",
            cluster_data["time_remaining"]
        ])
    
    headers = ["Cluster Name", "Namespace", "Owner", "Expires", "Elapsed", "Remaining"]
    click.echo(f"\n{Fore.YELLOW}⚠️  WARNING: {len(warning_clusters)} clusters ({warning_threshold}%-{critical_threshold-1}% elapsed):{Style.RESET_ALL}")
    click.echo(tabulate(warning_table_data, headers=headers, tablefmt="grid"))


def _display_summary(critical_clusters, warning_clusters):
    """Display notification summary."""
    click.echo(f"\n{Fore.CYAN}Notification Summary:{Style.RESET_ALL}")
    click.echo(f"  • Critical notifications: {len(critical_clusters)}")
    click.echo(f"  • Warning notifications: {len(warning_clusters)}")
    click.echo(f"  • Total notifications: {len(critical_clusters) + len(warning_clusters)}")


def _send_notifications(critical_clusters: List[Tuple], warning_clusters: List[Tuple], 
                       backend: str, notification_manager: NotificationManager, 
                       notification_history: Optional[NotificationHistory], **kwargs):
    """
    Send notifications using the specified backend.
    
    Args:
        critical_clusters: List of critical cluster data
        warning_clusters: List of warning cluster data
        backend: Notification backend to use
        notification_manager: NotificationManager instance
        notification_history: NotificationHistory instance for tracking sent notifications
        **kwargs: Backend-specific parameters
    """
    total_notifications = len(critical_clusters) + len(warning_clusters)
    
    if total_notifications == 0:
        click.echo(f"{Fore.GREEN}No new notifications to send.{Style.RESET_ALL}")
        return
    
    if backend == "slack":
        slack_channel = kwargs.get('slack_channel')
        click.echo(f"{Fore.CYAN}Sending {total_notifications} notifications to Slack channel #{slack_channel}...{Style.RESET_ALL}")
    
    try:
        # Send critical notifications
        if critical_clusters:
            notification_manager.send_notification(
                backend=backend,
                clusters=critical_clusters,
                severity="critical",
                threshold=kwargs.get('critical_threshold', 95),
                token=kwargs.get('slack_token'),
                channel=kwargs.get('slack_channel'),
                username=kwargs.get('slack_username', 'NKP Cluster Cleaner'),
                icon_emoji=kwargs.get('slack_icon_emoji', ':warning:')
            )
            
            # Mark as notified
            if notification_history:
                notification_history.mark_clusters_as_notified(critical_clusters, "critical")
            
            click.echo(f"{Fore.GREEN}Sent critical notification for {len(critical_clusters)} clusters{Style.RESET_ALL}")
        
        # Send warning notifications
        if warning_clusters:
            notification_manager.send_notification(
                backend=backend,
                clusters=warning_clusters,
                severity="warning",
                threshold=kwargs.get('warning_threshold', 80),
                token=kwargs.get('slack_token'),
                channel=kwargs.get('slack_channel'),
                username=kwargs.get('slack_username', 'NKP Cluster Cleaner'),
                icon_emoji=kwargs.get('slack_icon_emoji', ':warning:')
            )
            
            # Mark as notified
            if notification_history:
                notification_history.mark_clusters_as_notified(warning_clusters, "warning")
            
            click.echo(f"{Fore.GREEN}Sent warning notification for {len(warning_clusters)} clusters{Style.RESET_ALL}")
        
        click.echo(f"{Fore.GREEN}Successfully sent all notifications!{Style.RESET_ALL}")
        
    except Exception as e:
        click.echo(f"{Fore.RED}Failed to send notifications: {e}{Style.RESET_ALL}")
        raise click.Abort()