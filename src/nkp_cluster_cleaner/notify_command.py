"""
Notify command implementation for the NKP Cluster Cleaner tool.
"""

import click
import requests
import json
from colorama import Fore, Style
from tabulate import tabulate
from typing import Optional, List, Tuple, Dict, Any
from .config import ConfigManager
from .notification_manager import NotificationManager


# Supported notification backends
SUPPORTED_BACKENDS = ["slack"]


def execute_notify_command(kubeconfig: Optional[str], config: Optional[str], namespace: Optional[str], 
                          warning_threshold: int, critical_threshold: int, notify_backend: Optional[str] = None,
                          slack_token: Optional[str] = None, slack_channel: Optional[str] = None,
                          slack_username: str = "NKP Cluster Cleaner", slack_icon_emoji: str = ":warning:"):
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
        
        # Send notifications if backend is specified
        if notify_backend:
            _send_notifications(
                critical_clusters, 
                warning_clusters, 
                notify_backend,
                notification_manager,
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
                       backend: str, notification_manager: NotificationManager, **kwargs):
    """
    Send actual notifications using the specified backend.
    
    Args:
        critical_clusters: List of critical cluster data
        warning_clusters: List of warning cluster data
        backend: Notification backend to use
        notification_manager: NotificationManager instance
        **kwargs: Backend-specific parameters
    """
    if backend == "slack":
        _send_slack_notifications(critical_clusters, warning_clusters, notification_manager, **kwargs)
    else:
        click.echo(f"{Fore.RED}Error: Unknown notification backend '{backend}'{Style.RESET_ALL}")
        raise click.Abort()


def _send_slack_notifications(critical_clusters: List[Tuple], warning_clusters: List[Tuple], 
                             notification_manager: NotificationManager, **kwargs):
    """
    Send notifications to Slack.
    
    Args:
        critical_clusters: List of critical cluster data
        warning_clusters: List of warning cluster data  
        notification_manager: NotificationManager instance
        **kwargs: Slack-specific parameters (slack_token, slack_channel, etc.)
    """
    slack_token = kwargs.get('slack_token')
    slack_channel = kwargs.get('slack_channel')
    slack_username = kwargs.get('slack_username', 'NKP Cluster Cleaner')
    slack_icon_emoji = kwargs.get('slack_icon_emoji', ':warning:')
    warning_threshold = kwargs.get('warning_threshold', 80)
    critical_threshold = kwargs.get('critical_threshold', 95)
    
    total_notifications = len(critical_clusters) + len(warning_clusters)
    
    if total_notifications == 0:
        click.echo(f"{Fore.GREEN}No notifications to send to Slack.{Style.RESET_ALL}")
        return
    
    click.echo(f"{Fore.CYAN}Sending {total_notifications} notifications to Slack channel #{slack_channel}...{Style.RESET_ALL}")
    
    try:
        # Send critical notifications
        if critical_clusters:
            _send_slack_message(
                critical_clusters, 
                "critical", 
                slack_token, 
                slack_channel, 
                slack_username, 
                slack_icon_emoji,
                notification_manager,
                critical_threshold
            )
        
        # Send warning notifications
        if warning_clusters:
            _send_slack_message(
                warning_clusters, 
                "warning", 
                slack_token, 
                slack_channel, 
                slack_username, 
                slack_icon_emoji,
                notification_manager,
                warning_threshold
            )
        
        click.echo(f"{Fore.GREEN}Successfully sent notifications to Slack!{Style.RESET_ALL}")
        
    except Exception as e:
        click.echo(f"{Fore.RED}Failed to send Slack notifications: {e}{Style.RESET_ALL}")
        raise click.Abort()


def _send_slack_message(clusters: List[Tuple], severity: str, token: str, channel: str, 
                       username: str, icon_emoji: str, notification_manager: NotificationManager, 
                       threshold: int):
    """
    Send a single Slack message for a group of clusters.
    
    Args:
        clusters: List of cluster data tuples
        severity: "critical" or "warning"
        token: Slack Bot User OAuth Token
        channel: Slack channel name
        username: Bot username
        icon_emoji: Bot icon emoji
        notification_manager: NotificationManager instance
        threshold: The threshold percentage that triggered this notification
    """
    # Prepare message
    if severity == "critical":
        title = f"🚨 CRITICAL: {len(clusters)} clusters will be deleted soon"
        color = "#ff0000"  # Red
        emoji = "🚨"
        threshold_text = f"These clusters have exceeded {threshold}% of their lifetime or have immediate deletion issues:"
    else:
        title = f"⚠️ WARNING: {len(clusters)} clusters will be deleted soon"
        color = "#ff9900"  # Orange
        emoji = "⚠️"
        threshold_text = f"These clusters have exceeded {threshold}% of their lifetime:"
    
    # Build cluster list for message
    cluster_details = []
    for cluster_info, elapsed_percentage, expiry_time in clusters:
        cluster_data = notification_manager.get_cluster_notification_data(
            cluster_info, elapsed_percentage, expiry_time
        )
        
        # Format cluster info
        cluster_text = (
            f"• *{cluster_data['cluster_name']}* "
            f"(ns: `{cluster_data['namespace']}`, "
            f"owner: `{cluster_data['owner']}`, "
            f"expires: `{cluster_data['expires']}`, "
            f"consumed: `{cluster_data['elapsed_percentage']:.1f}%`, "
            f"remaining: `{cluster_data['time_remaining']}`)"
        )
        cluster_details.append(cluster_text)
    
    # Create Slack message payload
    message = {
        "channel": channel,
        "username": username,
        "icon_emoji": icon_emoji,
        "attachments": [
            {
                "color": color,
                "title": title,
                "text": f"{emoji} {threshold_text}\n\n" + "\n".join(cluster_details),
                "footer": "NKP Cluster Cleaner",
                "ts": int(expiry_time.timestamp()) if clusters else None
            }
        ]
    }
    
    # Send to Slack
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    response = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers=headers,
        data=json.dumps(message)
    )
    
    # Check response
    if response.status_code != 200:
        raise Exception(f"HTTP {response.status_code}: {response.text}")
    
    result = response.json()
    if not result.get("ok"):
        error = result.get("error", "Unknown error")
        raise Exception(f"Slack API error: {error}")
    
    click.echo(f"{Fore.GREEN}Sent {severity} notification for {len(clusters)} clusters to #{channel}{Style.RESET_ALL}")