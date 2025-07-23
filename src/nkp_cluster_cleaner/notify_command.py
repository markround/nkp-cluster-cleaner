"""
Notify command implementation for the NKP Cluster Cleaner tool.
"""

import click
from colorama import Fore, Style
from tabulate import tabulate
from typing import Optional
from .config import ConfigManager
from .notification_manager import NotificationManager


def execute_notify_command(kubeconfig: Optional[str], config: Optional[str], namespace: Optional[str], 
                          warning_threshold: int, critical_threshold: int):
    """
    Execute the notify command with the given parameters.
    
    Args:
        kubeconfig: Path to kubeconfig file
        config: Path to configuration file
        namespace: Namespace to limit operation to
        warning_threshold: Warning threshold percentage
        critical_threshold: Critical threshold percentage
    """
    if namespace:
        click.echo(f"{Fore.BLUE}Checking clusters for notification in namespace '{namespace}'...{Style.RESET_ALL}")
    else:
        click.echo(f"{Fore.BLUE}Checking clusters for notification across all namespaces...{Style.RESET_ALL}")
    
    try:
        # Initialize configuration and notification manager
        config_manager = ConfigManager(config) if config else ConfigManager()
        notification_manager = NotificationManager(kubeconfig, config_manager)
        
        # Get clusters requiring notifications
        critical_clusters, warning_clusters = notification_manager.get_clusters_for_notification(
            warning_threshold, critical_threshold, namespace
        )
        
        # Display results
        total_notifications = len(warning_clusters) + len(critical_clusters)
        
        if total_notifications == 0:
            click.echo(f"\n{Fore.GREEN}No clusters require notifications at current thresholds.{Style.RESET_ALL}")
            click.echo(f"{Fore.CYAN}Thresholds: Warning {warning_threshold}%, Critical {critical_threshold}%{Style.RESET_ALL}")
            return
        
        click.echo(f"\n{Fore.YELLOW}Found {total_notifications} clusters requiring notifications:{Style.RESET_ALL}")
        click.echo(f"{Fore.CYAN}Thresholds: Warning {warning_threshold}%, Critical {critical_threshold}%{Style.RESET_ALL}")
        
        # Display critical clusters
        if critical_clusters:
            _display_critical_clusters(critical_clusters, critical_threshold, notification_manager)
        
        # Display warning clusters
        if warning_clusters:
            _display_warning_clusters(warning_clusters, warning_threshold, critical_threshold, notification_manager)
        
        # Display summary
        _display_summary(critical_clusters, warning_clusters)
        
        # TODO: Add actual notification sending here
        # _send_notifications(critical_clusters, warning_clusters)
        
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


def _send_notifications(critical_clusters, warning_clusters):
    """
    Send actual notifications (placeholder for future implementation).
    
    Args:
        critical_clusters: List of critical cluster data
        warning_clusters: List of warning cluster data
    """
    # TODO: Implement notification sending
    # This could include:
    # - Email notifications
    # - Slack notifications  
    # - Webhook notifications
    # - etc.
    pass