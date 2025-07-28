"""
Notification Manager module for handling cluster expiration notifications.
"""

import requests
import json
import redis
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from colorama import Fore, Style
from .cluster_manager import ClusterManager
from .config import ConfigManager


class NotificationManager:
    """Manages notifications for clusters."""
    
    SUPPORTED_BACKENDS = ["slack"]

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
            namespace: If specified, only examine clusters in this namespace
            
        Returns:
            Tuple of (critical_clusters, warning_clusters) where each contains 
            (cluster_info, elapsed_percentage, expiry_time) tuples
        """
        # Validate thresholds
        if warning_threshold < 0 or warning_threshold > 100:
            raise ValueError("Warning threshold must be between 0 and 100")
        
        if critical_threshold < 0 or critical_threshold > 100:
            raise ValueError("Critical threshold must be between 0 and 100")
        
        if warning_threshold >= critical_threshold:
            raise ValueError("Warning threshold must be less than critical threshold")
        
        # Get all clusters and categorize them
        clusters_to_delete, excluded_clusters = self.cluster_manager.get_clusters_with_exclusions(namespace)
        
        # Process clusters to find those requiring notifications
        warning_clusters = []
        critical_clusters = []
        current_time = datetime.now()
        
        # Add all clusters marked for deletion to critical list
        for cluster_info, reason in clusters_to_delete:
            labels = cluster_info.get("labels", {})
            expires_value = labels.get("expires")
            
            # Check if this is due to missing/invalid labels (immediate deletion)
            reason_lower = reason.lower()
            is_missing_labels = any(keyword in reason_lower for keyword in [
                "missing required label", "missing expires label", 
                "does not match pattern", "invalid expires format"
            ])
            
            if is_missing_labels:
                # Clusters with missing/invalid labels are deleted immediately - always critical
                critical_clusters.append((cluster_info, 100.0, current_time))
            elif "expired" in reason_lower:
                # Expired clusters
                if expires_value:
                    # Get creation timestamp and calculate expiry time
                    kommander_cluster = cluster_info.get("kommander_cluster", {})
                    metadata = kommander_cluster.get("metadata", {})
                    creation_timestamp = metadata.get("creationTimestamp")
                    
                    if creation_timestamp:
                        try:
                            expiry_time = self.cluster_manager._parse_expires_label(expires_value, creation_timestamp)
                            # Expired clusters are 100% elapsed
                            critical_clusters.append((cluster_info, 100.0, expiry_time))
                        except (ValueError, TypeError):
                            # If we can't parse the time, still include it with current time as expiry
                            critical_clusters.append((cluster_info, 100.0, current_time))
                else:
                    # Expired cluster without expires label
                    critical_clusters.append((cluster_info, 100.0, current_time))
            else:
                # Other deletion reasons - also critical since they will be deleted
                critical_clusters.append((cluster_info, 100.0, current_time))
        
        # Then process excluded clusters to find those approaching expiration
        for cluster_info, reason in excluded_clusters:
            # Only process clusters that are excluded because they haven't expired yet
            if "has not expired yet" not in reason:
                continue
            
            labels = cluster_info.get("labels", {})
            expires_value = labels.get("expires")
            
            if not expires_value:
                continue
            
            # Get creation timestamp
            kommander_cluster = cluster_info.get("kommander_cluster", {})
            metadata = kommander_cluster.get("metadata", {})
            creation_timestamp = metadata.get("creationTimestamp")
            
            if not creation_timestamp:
                continue
            
            try:
                # Parse the expires label and calculate elapsed percentage
                expiry_time = self.cluster_manager._parse_expires_label(expires_value, creation_timestamp)
                
                # Parse creation timestamp 
                if creation_timestamp.endswith('Z'):
                    creation_time = datetime.fromisoformat(creation_timestamp[:-1])
                else:
                    creation_time = datetime.fromisoformat(creation_timestamp)
                
                # Calculate elapsed percentage
                total_duration = (expiry_time - creation_time).total_seconds()
                elapsed_duration = (current_time - creation_time).total_seconds()
                
                if total_duration > 0:
                    elapsed_percentage = (elapsed_duration / total_duration) * 100
                    elapsed_percentage = max(0, min(100, elapsed_percentage))  # Clamp to 0-100
                    
                    # Categorize based on thresholds
                    if elapsed_percentage >= critical_threshold:
                        critical_clusters.append((cluster_info, elapsed_percentage, expiry_time))
                    elif elapsed_percentage >= warning_threshold:
                        warning_clusters.append((cluster_info, elapsed_percentage, expiry_time))
                    
            except (ValueError, TypeError):
                # Skip clusters with invalid expires format
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
            "owner": cluster_info.get("labels", {}).get("owner", "unknown"),
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
    
    #
    # Specific notifications
    #
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

        

    def send_deletion_notification(self, backend: str, deleted_clusters: List[Dict[str, any]], 
                                  severity: str = "info", **kwargs):
        """
        Send deletion notifications for clusters that have been deleted.
        
        Args:
            backend: Notification backend to use
            deleted_clusters: List of dicts containing details of deleted clusters
                Expected format: [{"name": str, "namespace": str, "owner": str, "reason": str}, ...]
            severity: Notification severity (default: "info")
            **kwargs: Backend-specific parameters
        """
        if not deleted_clusters:
            return
        
        # Build title based on number of deleted clusters
        cluster_count = len(deleted_clusters)
        if cluster_count == 1:
            title = "1 cluster has been deleted"
        else:
            title = f"{cluster_count} clusters have been deleted"
        
        # Build cluster details
        cluster_details = []
        for cluster in deleted_clusters:
            cluster_name = cluster.get("name", "unknown")
            namespace = cluster.get("namespace", "unknown")
            owner = cluster.get("owner", "unknown")
            reason = cluster.get("reason", "unknown reason")
            
            # Format cluster info
            cluster_text = (
                f"• *{cluster_name}* "
                f"(ns: `{namespace}`, "
                f"owner: `{owner}`, "
                f"reason: `{reason}`)"
            )
            cluster_details.append(cluster_text)
        
        # Build the full message text
        if cluster_count == 1:
            intro_text = "The following cluster has been deleted:"
        else:
            intro_text = "The following clusters have been deleted:"
        
        full_text = f"{intro_text}\n\n" + "\n".join(cluster_details)
        
        # Send the notification
        self.send_notification(backend, title, full_text, severity, **kwargs)
