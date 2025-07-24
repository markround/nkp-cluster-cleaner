"""
Notification Manager module for handling cluster expiration notifications.
"""

import requests
import json
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from colorama import Fore, Style
from .cluster_manager import ClusterManager
from .config import ConfigManager


class NotificationManager:
    """Manages notifications for clusters approaching expiration."""
    
    def __init__(self, kubeconfig_path: Optional[str] = None, config_manager: Optional[ConfigManager] = None):
        """
        Initialize the notification manager.
        
        Args:
            kubeconfig_path: Path to kubeconfig file. If None, uses default locations.
            config_manager: Configuration manager instance
        """
        self.kubeconfig_path = kubeconfig_path
        self.config_manager = config_manager or ConfigManager()
        self.cluster_manager = ClusterManager(kubeconfig_path, config_manager)
    
    def get_clusters_for_notification(self, warning_threshold: int, critical_threshold: int, 
                                    namespace: Optional[str] = None) -> Tuple[List[Tuple[Dict, float, datetime]], List[Tuple[Dict, float, datetime]]]:
        """
        Get clusters that require notifications based on thresholds.
        
        Args:
            warning_threshold: Warning threshold percentage (0-100)
            critical_threshold: Critical threshold percentage (0-100)
            namespace: Namespace to filter by (optional)
            
        Returns:
            Tuple of (critical_clusters, warning_clusters) where each is a list of 
            (cluster_info, elapsed_percentage, expiry_time) tuples
        """
        if not (0 <= warning_threshold <= 100) or not (0 <= critical_threshold <= 100):
            raise ValueError("Thresholds must be between 0 and 100")
        
        if warning_threshold >= critical_threshold:
            raise ValueError("Warning threshold must be less than critical threshold")
        
        # Get all clusters and categorize them
        clusters_to_delete, excluded_clusters = self.cluster_manager.get_clusters_with_exclusions(namespace)
        
        # We want to examine ALL clusters (both for deletion and excluded) for notifications
        # since clusters may be approaching expiry but still excluded for other reasons
        all_clusters = []
        for cluster_info, reason in clusters_to_delete:
            all_clusters.append(cluster_info)
        for cluster_info, reason in excluded_clusters:
            all_clusters.append(cluster_info)
        
        critical_clusters = []
        warning_clusters = []
        current_time = datetime.now()
        
        for cluster_info in all_clusters:
            labels = cluster_info.get("labels", {})
            expires_value = labels.get("expires")
            
            # Get creation timestamp from kommander cluster metadata
            kommander_cluster = cluster_info.get("kommander_cluster", {})
            metadata = kommander_cluster.get("metadata", {})
            creation_timestamp = metadata.get("creationTimestamp")
            
            if not expires_value or not creation_timestamp:
                continue
            
            try:
                # Calculate the expiry time using the same logic as ClusterManager
                expiry_time = self.cluster_manager._parse_expires_label(expires_value, creation_timestamp)
                
                # Parse creation time
                if creation_timestamp.endswith('Z'):
                    creation_time = datetime.fromisoformat(creation_timestamp[:-1])
                else:
                    creation_time = datetime.fromisoformat(creation_timestamp)
                
                # Calculate time elapsed and total duration
                total_duration = expiry_time - creation_time
                elapsed_duration = current_time - creation_time
                
                # Calculate percentage of time elapsed
                if total_duration.total_seconds() > 0:
                    elapsed_percentage = (elapsed_duration.total_seconds() / total_duration.total_seconds()) * 100
                    elapsed_percentage = max(0, min(100, elapsed_percentage))  # Clamp to 0-100
                    
                    # Determine notification level
                    if elapsed_percentage >= critical_threshold:
                        critical_clusters.append((cluster_info, elapsed_percentage, expiry_time))
                    elif elapsed_percentage >= warning_threshold:
                        warning_clusters.append((cluster_info, elapsed_percentage, expiry_time))
                
            except (ValueError, TypeError):
                # Skip clusters with invalid time formats
                continue
        
        return critical_clusters, warning_clusters
    
    def format_time_remaining(self, expiry_time: datetime) -> str:
        """
        Format time remaining until expiry as a human-readable string.
        
        Args:
            expiry_time: When the cluster expires
            
        Returns:
            Formatted time remaining (e.g., "2d", "5h", "EXPIRED", "IMMEDIATE")
        """
        current_time = datetime.now()
        time_remaining = expiry_time - current_time
        
        # If expiry time is current time, this indicates immediate deletion
        if expiry_time == current_time:
            return "IMMEDIATE"
        
        if time_remaining.total_seconds() <= 0:
            return "EXPIRED"
        
        days_remaining = time_remaining.days
        hours_remaining = time_remaining.seconds // 3600
        
        if days_remaining > 0:
            return f"{days_remaining}d"
        else:
            return f"{hours_remaining}h"
    
    def get_cluster_notification_data(self, cluster_info: Dict, elapsed_percentage: float, expiry_time: datetime) -> Dict:
        """
        Extract notification-relevant data from a cluster.
        
        Args:
            cluster_info: Raw cluster information dictionary
            elapsed_percentage: Percentage of cluster lifetime elapsed
            expiry_time: When the cluster expires
            
        Returns:
            Dictionary containing formatted notification data
        """
        return {
            "cluster_name": cluster_info.get("capi_cluster_name", "unknown"),
            "namespace": cluster_info.get("capi_cluster_namespace", "unknown"),
            "owner": cluster_info.get("owner", "unknown"),
            "expires": expiry_time.strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_percentage": elapsed_percentage,
            "time_remaining": self.format_time_remaining(expiry_time)
        }
    
    def send_notification(self, backend: str, title: str, text: str, severity: str = "info", **kwargs):
        """
        Send a generic notification using the specified backend.
        
        Args:
            backend: Notification backend to use ("slack", etc.)
            title: Notification title
            text: Notification message text
            severity: Notification severity level ("info", "warning", "critical")
            **kwargs: Backend-specific parameters
        """
        if backend == "slack":
            self._send_slack_notification(title, text, severity, **kwargs)
        else:
            raise ValueError(f"Unsupported notification backend: {backend}")
    
    def _send_slack_notification(self, title: str, text: str, severity: str, **kwargs):
        """
        Send a generic Slack notification.
        
        Args:
            title: Message title
            text: Message text
            severity: Severity level ("info", "warning", "critical")
            **kwargs: Slack-specific parameters (token, channel, username, icon_emoji)
        """
        token = kwargs.get('token')
        channel = kwargs.get('channel')
        username = kwargs.get('username', 'NKP Cluster Cleaner')
        icon_emoji = kwargs.get('icon_emoji', ':broom:')
        
        if not token or not channel:
            raise ValueError("Slack token and channel are required")
        
        # Select color and emoji based on severity
        if severity == "critical":
            color = "#ff0000"  # Red
            emoji = "🚨"
        elif severity == "warning":
            color = "#ff9900"  # Orange  
            emoji = "⚠️"
        elif severity == "info":
            color = "#0099ff"  # Blue
            emoji = "ℹ️"
        else:
            color = "#808080"  # Gray
            emoji = "📢"
        
        # Create Slack message payload
        message = {
            "channel": channel,
            "username": username,
            "icon_emoji": icon_emoji,
            "attachments": [
                {
                    "color": color,
                    "title": title,
                    "text": f"{emoji} {text}",
                    "footer": "NKP Cluster Cleaner",
                    "ts": int(datetime.now().timestamp())
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
    
    def send_expiry_notification(self, backend: str, clusters: List[Tuple[Dict, float, datetime]], 
                               severity: str, threshold: int, **kwargs):
        """
        Send expiry notifications for a group of clusters.
        
        Args:
            backend: Notification backend to use
            clusters: List of (cluster_info, elapsed_percentage, expiry_time) tuples
            severity: Notification severity ("warning" or "critical")
            threshold: The threshold percentage that triggered this notification
            **kwargs: Backend-specific parameters
        """
        if not clusters:
            return
        
        # Build title and message text for expiry notifications
        if severity == "critical":
            title = f"CRITICAL: {len(clusters)} clusters will be deleted soon"
            threshold_text = f"These clusters have exceeded {threshold}% of their lifetime or have immediate deletion issues:"
        elif severity == "warning":
            title = f"WARNING: {len(clusters)} clusters will be deleted soon"
            threshold_text = f"These clusters have exceeded {threshold}% of their lifetime:"
        else:
            title = f"INFO: {len(clusters)} clusters notification"
            threshold_text = f"These clusters require attention:"
        
        # Build cluster details
        cluster_details = []
        for cluster_info, elapsed_percentage, expiry_time in clusters:
            cluster_data = self.get_cluster_notification_data(
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
        
        # Combine threshold text and cluster details
        full_text = f"{threshold_text}\n\n" + "\n".join(cluster_details)
        
        # Send the notification
        self.send_notification(backend, title, full_text, severity, **kwargs)