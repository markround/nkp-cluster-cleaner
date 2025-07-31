"""
Notification History module for storing notification history in Redis to avoid duplicate alerts.
"""

import redis
from typing import List, Tuple, Optional
from colorama import Fore, Style


class NotificationHistory:
    """Manages notification history using Redis to prevent duplicate alerts."""

    def __init__(
        self,
        redis_host: str = "redis",
        redis_port: int = 6379,
        redis_db: int = 0,
        redis_username: Optional[str] = None,
        redis_password: Optional[str] = None,
    ):
        """
        Initialize notification history manager.

        Args:
            redis_host: Redis host
            redis_port: Redis port
            redis_db: Redis database number
            redis_username: Redis username for authentication
            redis_password: Redis password for authentication
        """
        redis_kwargs = {
            "host": redis_host,
            "port": redis_port,
            "db": redis_db,
            "decode_responses": True,
            "socket_connect_timeout": 5,
            "socket_timeout": 5,
            "retry_on_timeout": True,
        }

        if redis_username:
            redis_kwargs["username"] = redis_username
        if redis_password:
            redis_kwargs["password"] = redis_password

        self.redis_client = redis.Redis(**redis_kwargs)

        # Test connection
        try:
            self.redis_client.ping()
        except redis.ConnectionError as e:
            raise Exception(
                f"Failed to connect to Redis at {redis_host}:{redis_port}: {e}"
            )

    def _get_cluster_key(self, cluster_name: str, namespace: str) -> str:
        """Generate Redis key for cluster notification history."""
        return f"notifications:cluster:{namespace}:{cluster_name}"

    def has_been_notified(
        self, cluster_name: str, namespace: str, severity: str
    ) -> bool:
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

    def mark_as_notified(
        self, cluster_name: str, namespace: str, severity: str, ttl_days: int = 30
    ):
        """
        Mark a cluster as notified at the given severity level.

        Args:
            cluster_name: Name of the cluster
            namespace: Namespace of the cluster
            severity: "warning" or "critical"
            ttl_days: Number of days to keep the notification record
        """
        key = self._get_cluster_key(cluster_name, namespace)

        # Add severity to the set for this cluster
        self.redis_client.sadd(key, severity)

        # Set TTL for the key
        self.redis_client.expire(key, ttl_days * 24 * 3600)

    def filter_new_notifications(
        self, clusters: List[Tuple], severity: str
    ) -> List[Tuple]:
        """
        Filter out clusters that have already been notified at this severity level.

        Args:
            clusters: List of (cluster_info, elapsed_percentage, expiry_time) tuples
            severity: "warning" or "critical"

        Returns:
            List of clusters that haven't been notified yet
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
        Mark multiple clusters as notified.

        Args:
            clusters: List of (cluster_info, elapsed_percentage, expiry_time) tuples
            severity: "warning" or "critical"
        """
        for cluster_info, elapsed_percentage, expiry_time in clusters:
            cluster_name = cluster_info.get("capi_cluster_name", "unknown")
            namespace = cluster_info.get("capi_cluster_namespace", "unknown")
            self.mark_as_notified(cluster_name, namespace, severity)

    def clear_cluster_history(self, cluster_name: str, namespace: str):
        """
        Clear all notification history for a deleted cluster.

        Args:
            cluster_name: Name of the cluster
            namespace: Namespace of the cluster
        """
        key = self._get_cluster_key(cluster_name, namespace)

        # Delete the entire key from Redis
        deleted = self.redis_client.delete(key)

        if deleted:
            print(
                f"{Fore.CYAN}Cleared notification history for {cluster_name} in {namespace}{Style.RESET_ALL}"
            )

        return deleted > 0

    def get_active_notification_count(self) -> int:
        """
        Get the count of active notification keys in Redis.

        Returns:
            Number of active notification tracking keys
        """
        try:
            keys = self.redis_client.keys("notifications:cluster:*")
            return len(keys)
        except Exception:
            return 0

    def get_all_notified_clusters(self) -> List[dict]:
        """
        Get all clusters that have been notified and their notification levels.

        Returns:
            List of dictionaries containing cluster notification information
        """
        try:
            keys = self.redis_client.keys("notifications:cluster:*")
            clusters = []

            for key in keys:
                # Parse namespace and cluster name from key
                # Format: notifications:cluster:namespace:clustername
                parts = key.split(":")
                if len(parts) >= 4:
                    namespace = parts[2]
                    cluster_name = ":".join(
                        parts[3:]
                    )  # Handle cluster names with colons

                    # Get notification levels for this cluster
                    severities = self.redis_client.smembers(key)
                    ttl = self.redis_client.ttl(key)

                    clusters.append(
                        {
                            "cluster_name": cluster_name,
                            "namespace": namespace,
                            "severities": list(severities),
                            "ttl_seconds": ttl,
                        }
                    )

            return clusters

        except Exception:
            return []

    def clear_expired_notifications(self) -> int:
        """
        Clear expired notification records from Redis.

        Returns:
            Number of expired keys removed
        """
        try:
            keys = self.redis_client.keys("notifications:cluster:*")
            expired_count = 0

            for key in keys:
                ttl = self.redis_client.ttl(key)
                if ttl == -2:  # Key doesn't exist
                    expired_count += 1
                elif ttl == -1:  # Key exists but no expiry set
                    # Set a default expiry of 30 days
                    self.redis_client.expire(key, 30 * 24 * 3600)

            return expired_count

        except Exception:
            return 0
