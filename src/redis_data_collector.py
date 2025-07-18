"""
Redis-based data collector for NKP Cluster Cleaner analytics.

Uses Redis for zero-config, network-based storage that supports
multiple readers/writers and horizontal scaling.
"""

import redis
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from collections import defaultdict, Counter
from .cluster_manager import ClusterManager
from .config import ConfigManager


class RedisDataCollector:
    """Collects and stores cluster analytics data using Redis."""
    
    def __init__(self, kubeconfig_path: Optional[str] = None, config_manager: Optional[ConfigManager] = None, 
                 redis_host: str = 'redis', redis_port: int = 6379, redis_db: int = 0, 
                 debug: bool = False):
        """
        Initialize the data collector.
        
        Args:
            kubeconfig_path: Path to kubeconfig file
            config_manager: Configuration manager instance
            redis_host: Redis host (default: 'redis' for k8s service)
            redis_port: Redis port (default: 6379)
            redis_db: Redis database number (default: 0)
            debug: Enable debug output
        """
        self.debug = debug
        
        # Redis connection with retries and timeouts
        self.redis_client = redis.Redis(
            host=redis_host,
            port=redis_port,
            db=redis_db,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30
        )
        
        self.config_manager = config_manager or ConfigManager()
        self.cluster_manager = ClusterManager(kubeconfig_path, self.config_manager)
        
        # Test Redis connection
        self._test_connection()
        
    def _debug_print(self, message: str):
        """Print debug message if debug mode is enabled."""
        if self.debug:
            print(message)
    
    def _test_connection(self):
        """Test Redis connection and log status."""
        try:
            self.redis_client.ping()
            self._debug_print("Redis connection established successfully")
        except redis.ConnectionError as e:
            raise Exception(f"Failed to connect to Redis: {e}")
    
    def collect_snapshot(self, retention_days: int = 90) -> Dict[str, Any]:
        """
        Collect current cluster state and store in Redis.
        
        Args:
            retention_days: Number of days to retain data
            
        Returns:
            Dictionary containing the collected snapshot data
        """
        timestamp = datetime.now()
        self._debug_print(f"Collecting analytics snapshot at {timestamp.isoformat()}")
        
        try:
            # Get current cluster state
            self._debug_print("Getting cluster data...")
            clusters_to_delete, excluded_clusters = self.cluster_manager.get_clusters_with_exclusions()
            self._debug_print(f"Found {len(clusters_to_delete)} clusters for deletion, {len(excluded_clusters)} excluded")
            
            # Convert to consistent format with status indicator
            self._debug_print("Processing cluster data...")
            all_clusters = []
            for cluster, reason in clusters_to_delete:
                all_clusters.append((cluster, reason, 'deletion'))
            for cluster, reason in excluded_clusters:
                all_clusters.append((cluster, reason, 'excluded'))
            
            self._debug_print(f"Total clusters processed: {len(all_clusters)}")
            
            # Build comprehensive snapshot
            snapshot_data = self._build_snapshot_data(all_clusters, clusters_to_delete, excluded_clusters, timestamp)
            
            # Store in Redis with automatic expiration
            self._debug_print("Storing snapshot in Redis...")
            self._store_snapshot(snapshot_data, timestamp, retention_days)
            
            # Cleanup old data (defensive cleanup)
            self._debug_print("Cleaning up old data...")
            cleaned_count = self._cleanup_old_data(retention_days)
            
            if cleaned_count > 0:
                self._debug_print(f"Cleaned up {cleaned_count} old snapshots")
            
            print(f"Analytics snapshot stored successfully in Redis:")
            print(f"  - Total clusters: {len(all_clusters)}")
            print(f"  - For deletion: {len(clusters_to_delete)}")
            print(f"  - Protected: {len(excluded_clusters)}")
            print(f"  - Redis key: analytics:snapshot:{timestamp.strftime('%Y-%m-%d:%H:%M:%S')}")
            
            return snapshot_data
            
        except Exception as e:
            print(f"Error collecting analytics snapshot: {e}")
            raise
    
    def _store_snapshot(self, snapshot_data: Dict[str, Any], timestamp: datetime, retention_days: int):
        """Store snapshot in Redis with expiration."""
        # Create unique key with timestamp
        snapshot_key = f"analytics:snapshot:{timestamp.strftime('%Y-%m-%d:%H:%M:%S')}"
        
        # Store with TTL (Time To Live) for automatic cleanup
        ttl_seconds = retention_days * 24 * 60 * 60  # Convert days to seconds
        
        pipe = self.redis_client.pipeline()
        
        # Store the main snapshot
        pipe.setex(snapshot_key, ttl_seconds, json.dumps(snapshot_data))
        
        # Store in a sorted set for easy time-based queries
        score = timestamp.timestamp()  # Unix timestamp for sorting
        pipe.zadd("analytics:snapshots:index", {snapshot_key: score})
        
        # Store summary data for quick access
        summary_key = f"analytics:summary:{timestamp.strftime('%Y-%m-%d:%H:%M:%S')}"
        summary = {
            'timestamp': timestamp.isoformat(),
            'total_clusters': snapshot_data['cluster_counts']['total'],
            'for_deletion': snapshot_data['cluster_counts']['for_deletion'],
            'protected': snapshot_data['cluster_counts']['protected'],
            'compliance_rate': snapshot_data.get('label_compliance', {}).get('overall_compliance_rate', 0)
        }
        pipe.setex(summary_key, ttl_seconds, json.dumps(summary))
        pipe.zadd("analytics:summaries:index", {summary_key: score})
        
        # Execute all commands
        pipe.execute()
    
    def _cleanup_old_data(self, retention_days: int) -> int:
        """
        Remove analytics snapshots older than specified days.
        
        Args:
            retention_days: Number of days of data to retain
            
        Returns:
            Number of snapshots that were cleaned up
        """
        cutoff_timestamp = (datetime.now() - timedelta(days=retention_days)).timestamp()
        
        # Get old snapshot keys
        old_snapshots = self.redis_client.zrangebyscore("analytics:snapshots:index", 0, cutoff_timestamp)
        old_summaries = self.redis_client.zrangebyscore("analytics:summaries:index", 0, cutoff_timestamp)
        
        if not old_snapshots and not old_summaries:
            return 0
        
        pipe = self.redis_client.pipeline()
        
        # Remove old snapshots
        for key in old_snapshots:
            pipe.delete(key)
        
        # Remove old summaries  
        for key in old_summaries:
            pipe.delete(key)
        
        # Remove from sorted sets
        if old_snapshots:
            pipe.zremrangebyscore("analytics:snapshots:index", 0, cutoff_timestamp)
        if old_summaries:
            pipe.zremrangebyscore("analytics:summaries:index", 0, cutoff_timestamp)
        
        pipe.execute()
        
        return len(old_snapshots)
    
    def get_historical_data(self, days: int = 30) -> List[Dict[str, Any]]:
        """
        Retrieve historical analytics data from Redis.
        
        Args:
            days: Number of days of historical data to retrieve
            
        Returns:
            List of snapshots from the specified time period
        """
        cutoff_timestamp = (datetime.now() - timedelta(days=days)).timestamp()
        current_timestamp = datetime.now().timestamp()
        
        # Get snapshot keys in time range
        snapshot_keys = self.redis_client.zrangebyscore(
            "analytics:snapshots:index", 
            cutoff_timestamp, 
            current_timestamp
        )
        
        historical_data = []
        
        # Batch get all snapshots
        if snapshot_keys:
            snapshots = self.redis_client.mget(snapshot_keys)
            
            for snapshot_json in snapshots:
                if snapshot_json:
                    try:
                        snapshot = json.loads(snapshot_json)
                        historical_data.append(snapshot)
                    except json.JSONDecodeError as e:
                        self._debug_print(f"Error parsing snapshot: {e}")
                        continue
        
        # Sort by timestamp
        historical_data.sort(key=lambda x: x.get('timestamp', ''))
        
        return historical_data
    
    def get_database_stats(self) -> Dict[str, Any]:
        """Get Redis statistics and health information."""
        try:
            info = self.redis_client.info()
            
            # Count snapshots
            total_snapshots = self.redis_client.zcard("analytics:snapshots:index")
            
            # Get date range
            oldest_score = self.redis_client.zrange("analytics:snapshots:index", 0, 0, withscores=True)
            newest_score = self.redis_client.zrange("analytics:snapshots:index", -1, -1, withscores=True)
            
            earliest = None
            latest = None
            
            if oldest_score:
                earliest = datetime.fromtimestamp(oldest_score[0][1]).isoformat()
            if newest_score:
                latest = datetime.fromtimestamp(newest_score[0][1]).isoformat()
            
            return {
                'total_snapshots': total_snapshots,
                'earliest_snapshot': earliest,
                'latest_snapshot': latest,
                'redis_version': info.get('redis_version'),
                'redis_memory_used': info.get('used_memory_human'),
                'redis_memory_peak': info.get('used_memory_peak_human'),
                'redis_connected_clients': info.get('connected_clients'),
                'redis_uptime_days': info.get('uptime_in_days')
            }
        except Exception as e:
            return {'error': str(e)}
    
    def _build_snapshot_data(self, all_clusters, clusters_to_delete, excluded_clusters, timestamp) -> Dict[str, Any]:
        """Build the complete snapshot data structure."""
        unique_namespaces = self._get_unique_namespaces(all_clusters)
        
        return {
            'timestamp': timestamp.isoformat(),
            'collection_metadata': {
                'tool_version': self._get_tool_version(),
                'total_clusters_found': len(all_clusters),
                'namespaces_scanned': len(unique_namespaces),
                'nkp_version': self.cluster_manager.get_nkp_version()
            },
            'cluster_counts': {
                'for_deletion': len(clusters_to_delete),
                'protected': len(excluded_clusters),
                'total': len(all_clusters)
            },
            'clusters_by_namespace': self._group_by_namespace(all_clusters),
            'clusters_by_owner': self._group_by_owner(all_clusters),
            'clusters_by_status': self._group_by_status(all_clusters),
            'expiration_analysis': self._analyze_expiration_patterns(all_clusters),
            'label_compliance': self._calculate_label_compliance(all_clusters),
            'protection_rule_effectiveness': self._analyze_protection_rules(
                [(cluster, reason, 'excluded') for cluster, reason in excluded_clusters]
            ),
            'cluster_age_distribution': self._calculate_age_distribution(all_clusters),
            'deletion_reasons': self._analyze_deletion_reasons(
                [(cluster, reason, 'deletion') for cluster, reason in clusters_to_delete]
            )
        }
    
    def _get_tool_version(self) -> str:
        """Get the current tool version."""
        try:
            import nkp_cluster_cleaner
            return nkp_cluster_cleaner.__version__
        except:
            return "unknown"
    
    def _get_unique_namespaces(self, all_clusters: List[tuple]) -> set:
        """Get set of unique namespaces from cluster data."""
        namespaces = set()
        for cluster_info, reason, status in all_clusters:
            namespace = cluster_info.get('capi_cluster_namespace')
            if namespace:
                namespaces.add(namespace)
        return namespaces
    
    def _group_by_namespace(self, all_clusters: List[tuple]) -> Dict[str, Dict[str, int]]:
        """
        Group clusters by namespace with counts by status.
        
        Args:
            all_clusters: List of (cluster_info, reason, status) tuples
            
        Returns:
            Dictionary with namespace as key and status counts as values
        """
        namespace_data = defaultdict(lambda: {'deletion': 0, 'excluded': 0, 'total': 0})
        
        for cluster_info, reason, status in all_clusters:
            namespace = cluster_info.get('capi_cluster_namespace', 'unknown')
            namespace_data[namespace][status] += 1
            namespace_data[namespace]['total'] += 1
        
        return dict(namespace_data)
    
    def _group_by_owner(self, all_clusters: List[tuple]) -> Dict[str, Dict[str, int]]:
        """
        Group clusters by owner with counts by status.
        
        Args:
            all_clusters: List of (cluster_info, reason, status) tuples
            
        Returns:
            Dictionary with owner as key and status counts as values
        """
        owner_data = defaultdict(lambda: {'deletion': 0, 'excluded': 0, 'total': 0})
        
        for cluster_info, reason, status in all_clusters:
            labels = cluster_info.get('labels', {})
            owner = labels.get('owner', 'no-owner')
            owner_data[owner][status] += 1
            owner_data[owner]['total'] += 1
        
        return dict(owner_data)
    
    def _group_by_status(self, all_clusters: List[tuple]) -> Dict[str, int]:
        """
        Count clusters by their overall status.
        
        Args:
            all_clusters: List of (cluster_info, reason, status) tuples
            
        Returns:
            Dictionary with status counts
        """
        status_counts = Counter()
        
        for cluster_info, reason, status in all_clusters:
            status_counts[status] += 1
        
        return dict(status_counts)
    
    def _analyze_expiration_patterns(self, all_clusters: List[tuple]) -> Dict[str, Any]:
        """
        Analyze cluster expiration patterns.
        
        Args:
            all_clusters: List of (cluster_info, reason, status) tuples
            
        Returns:
            Dictionary with expiration analysis
        """
        expiration_buckets = {
            'expired': 0,
            'expires_soon': 0,  # < 24 hours
            'expires_this_week': 0,  # < 7 days
            'expires_this_month': 0,  # < 30 days
            'expires_later': 0,  # > 30 days
            'no_expiration': 0
        }
        
        expires_values = []
        
        for cluster_info, reason, status in all_clusters:
            labels = cluster_info.get('labels', {})
            expires = labels.get('expires')
            
            if not expires:
                expiration_buckets['no_expiration'] += 1
                continue
                
            expires_values.append(expires)
            
            # Determine if cluster has expired based on reason
            if 'expired' in reason.lower():
                expiration_buckets['expired'] += 1
            elif 'expires in' in reason.lower():
                # Extract time remaining from reason
                if '~1d' in reason or 'expires in ~0d' in reason:
                    expiration_buckets['expires_soon'] += 1
                elif any(f'~{i}d' in reason for i in range(2, 8)):
                    expiration_buckets['expires_this_week'] += 1
                elif any(f'~{i}d' in reason for i in range(8, 31)):
                    expiration_buckets['expires_this_month'] += 1
                else:
                    expiration_buckets['expires_later'] += 1
            else:
                expiration_buckets['expires_later'] += 1
        
        # Analyze expires label patterns
        expires_patterns = Counter(expires_values)
        
        return {
            'buckets': expiration_buckets,
            'common_expires_values': dict(expires_patterns.most_common(10)),
            'total_with_expires': len(expires_values),
            'total_without_expires': expiration_buckets['no_expiration']
        }
    
    def _calculate_label_compliance(self, all_clusters: List[tuple]) -> Dict[str, Any]:
        """
        Calculate label compliance statistics.
        
        Args:
            all_clusters: List of (cluster_info, reason, status) tuples
            
        Returns:
            Dictionary with compliance statistics
        """
        total_clusters = len(all_clusters)
        if total_clusters == 0:
            return {'total_clusters': 0, 'compliance_rate': 0, 'label_stats': {}}
        
        required_labels = [label.name for label in self.config_manager.get_criteria().extra_labels]
        required_labels.append('expires')  # Always required
        
        label_stats = {}
        for label_name in required_labels:
            present_count = 0
            for cluster_info, reason, status in all_clusters:
                labels = cluster_info.get('labels', {})
                if label_name in labels and labels[label_name]:
                    present_count += 1
            
            label_stats[label_name] = {
                'present': present_count,
                'missing': total_clusters - present_count,
                'compliance_rate': (present_count / total_clusters) * 100
            }
        
        # Overall compliance (all required labels present)
        fully_compliant = 0
        for cluster_info, reason, status in all_clusters:
            labels = cluster_info.get('labels', {})
            if all(label_name in labels and labels[label_name] for label_name in required_labels):
                fully_compliant += 1
        
        overall_compliance_rate = (fully_compliant / total_clusters) * 100
        
        return {
            'total_clusters': total_clusters,
            'fully_compliant': fully_compliant,
            'overall_compliance_rate': overall_compliance_rate,
            'label_stats': label_stats,
            'required_labels': required_labels
        }
    
    def _analyze_protection_rules(self, excluded_clusters: List[tuple]) -> Dict[str, int]:
        """
        Analyze which protection rules are most effective.
        
        Args:
            excluded_clusters: List of (cluster_info, reason, status) tuples for excluded clusters
            
        Returns:
            Dictionary with protection rule usage counts
        """
        protection_reasons = Counter()
        
        for cluster_info, reason, status in excluded_clusters:
            # Categorize protection reasons
            reason_lower = reason.lower()
            if 'management cluster' in reason_lower:
                protection_reasons['Management Cluster'] += 1
            elif 'protected by configuration' in reason_lower:
                protection_reasons['Protected Pattern'] += 1
            elif 'not expired yet' in reason_lower:
                protection_reasons['Not Expired'] += 1
            elif 'referenced capi cluster' in reason_lower:
                protection_reasons['Missing CAPI Reference'] += 1
            elif 'no valid capi cluster reference' in reason_lower:
                protection_reasons['Invalid CAPI Reference'] += 1
            else:
                protection_reasons['Other'] += 1
        
        return dict(protection_reasons)
    
    def _calculate_age_distribution(self, all_clusters: List[tuple]) -> Dict[str, int]:
        """
        Calculate cluster age distribution based on creation timestamps.
        
        Args:
            all_clusters: List of (cluster_info, reason, status) tuples
            
        Returns:
            Dictionary with age bucket counts
        """
        age_buckets = {
            '0-1_days': 0,
            '1-7_days': 0,
            '1-4_weeks': 0,
            '1-12_months': 0,
            'over_1_year': 0,
            'unknown_age': 0
        }
        
        now = datetime.now()
        
        for cluster_info, reason, status in all_clusters:
            kommander_cluster = cluster_info.get('kommander_cluster', {})
            metadata = kommander_cluster.get('metadata', {})
            creation_timestamp = metadata.get('creationTimestamp')
            
            if not creation_timestamp:
                age_buckets['unknown_age'] += 1
                continue
            
            try:
                # Parse creation timestamp
                if creation_timestamp.endswith('Z'):
                    creation_time = datetime.fromisoformat(creation_timestamp[:-1])
                else:
                    creation_time = datetime.fromisoformat(creation_timestamp)
                
                age = now - creation_time
                age_days = age.days
                
                if age_days <= 1:
                    age_buckets['0-1_days'] += 1
                elif age_days <= 7:
                    age_buckets['1-7_days'] += 1
                elif age_days <= 28:
                    age_buckets['1-4_weeks'] += 1
                elif age_days <= 365:
                    age_buckets['1-12_months'] += 1
                else:
                    age_buckets['over_1_year'] += 1
                    
            except (ValueError, TypeError):
                age_buckets['unknown_age'] += 1
        
        return age_buckets
    
    def _analyze_deletion_reasons(self, clusters_to_delete: List[tuple]) -> Dict[str, int]:
        """
        Analyze the reasons why clusters are marked for deletion.
        
        Args:
            clusters_to_delete: List of (cluster_info, reason, status) tuples
            
        Returns:
            Dictionary with deletion reason counts
        """
        deletion_reasons = Counter()
        
        for cluster_info, reason, status in clusters_to_delete:
            reason_lower = reason.lower()
            if 'missing' in reason_lower and 'expires' in reason_lower:
                deletion_reasons['Missing Expires Label'] += 1
            elif 'missing' in reason_lower and 'label' in reason_lower:
                deletion_reasons['Missing Required Label'] += 1
            elif 'expired' in reason_lower:
                deletion_reasons['Cluster Expired'] += 1
            elif 'invalid' in reason_lower and 'expires' in reason_lower:
                deletion_reasons['Invalid Expires Format'] += 1
            elif 'does not match pattern' in reason_lower:
                deletion_reasons['Label Pattern Mismatch'] += 1
            else:
                deletion_reasons['Other'] += 1
        
        return dict(deletion_reasons)