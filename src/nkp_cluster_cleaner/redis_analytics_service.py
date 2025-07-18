"""
Redis-based analytics service for retrieving and processing historical cluster data.

This module provides methods to query historical analytics data stored in Redis
for use in the web UI.
"""

import redis
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from collections import defaultdict, Counter
from .redis_data_collector import RedisDataCollector


class RedisAnalyticsService:
    """Service for retrieving and processing analytics data from Redis."""
    
    def __init__(self, kubeconfig_path: Optional[str] = None, redis_host: str = 'redis', 
                 redis_port: int = 6379, redis_db: int = 0):
        """
        Initialize the analytics service.
        
        Args:
            kubeconfig_path: Path to kubeconfig file
            redis_host: Redis host
            redis_port: Redis port
            redis_db: Redis database number
        """
        # Redis connection (reuse RedisDataCollector connection settings)
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
        
        # Test connection
        try:
            self.redis_client.ping()
        except redis.ConnectionError as e:
            raise Exception(f"Failed to connect to Redis at {redis_host}:{redis_port}: {e}")
    
    def _get_historical_data(self, days: int = 30) -> List[Dict[str, Any]]:
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
                    except json.JSONDecodeError:
                        continue
        
        # Sort by timestamp
        historical_data.sort(key=lambda x: x.get('timestamp', ''))
        
        return historical_data
    
    def get_cluster_trends(self, days: int = 30) -> Dict[str, Any]:
        """
        Get cluster count trends over time.
        
        Args:
            days: Number of days to analyze
            
        Returns:
            Dictionary with trend data suitable for charting
        """
        historical_data = self._get_historical_data(days)
        
        if not historical_data:
            return {
                'dates': [],
                'deletion_counts': [],
                'protected_counts': [],
                'total_counts': [],
                'summary': {
                    'current_for_deletion': 0,
                    'current_protected': 0,
                    'average_daily_deletions': 0,
                    'trend_direction': 'stable'
                }
            }
        
        # Group by date and aggregate
        daily_data = defaultdict(lambda: {
            'for_deletion': 0, 
            'protected': 0, 
            'total': 0, 
            'count': 0
        })
        
        for snapshot in historical_data:
            date = snapshot['timestamp'][:10]  # Extract YYYY-MM-DD
            counts = snapshot['cluster_counts']
            
            daily_data[date]['for_deletion'] += counts['for_deletion']
            daily_data[date]['protected'] += counts['protected']
            daily_data[date]['total'] += counts['total']
            daily_data[date]['count'] += 1
        
        # Calculate daily averages and prepare chart data
        dates = []
        deletion_counts = []
        protected_counts = []
        total_counts = []
        
        for date in sorted(daily_data.keys()):
            data = daily_data[date]
            # Average across multiple snapshots per day
            count = data['count']
            
            dates.append(date)
            deletion_counts.append(data['for_deletion'] // count)
            protected_counts.append(data['protected'] // count)
            total_counts.append(data['total'] // count)
        
        # Calculate summary statistics
        current_for_deletion = deletion_counts[-1] if deletion_counts else 0
        current_protected = protected_counts[-1] if protected_counts else 0
        
        # Calculate trend direction
        trend_direction = 'stable'
        if len(deletion_counts) >= 7:
            recent_avg = sum(deletion_counts[-7:]) / 7
            older_avg = sum(deletion_counts[-14:-7]) / 7 if len(deletion_counts) >= 14 else recent_avg
            
            if recent_avg > older_avg * 1.1:
                trend_direction = 'increasing'
            elif recent_avg < older_avg * 0.9:
                trend_direction = 'decreasing'
        
        return {
            'dates': dates,
            'deletion_counts': deletion_counts,
            'protected_counts': protected_counts,
            'total_counts': total_counts,
            'summary': {
                'current_for_deletion': current_for_deletion,
                'current_protected': current_protected,
                'average_daily_deletions': sum(deletion_counts) / len(deletion_counts) if deletion_counts else 0,
                'trend_direction': trend_direction
            }
        }
    
    def get_deletion_activity(self, days: int = 14) -> Dict[str, Any]:
        """
        Get deletion activity patterns.
        
        Args:
            days: Number of days to analyze
            
        Returns:
            Dictionary with deletion activity data
        """
        historical_data = self._get_historical_data(days)
        
        deletion_reasons = Counter()
        hourly_activity = defaultdict(int)
        daily_activity = defaultdict(int)
        
        for snapshot in historical_data:
            timestamp = datetime.fromisoformat(snapshot['timestamp'].replace('Z', ''))
            hour = timestamp.hour
            date = timestamp.strftime('%Y-%m-%d')
            
            # Count deletion reasons
            if 'deletion_reasons' in snapshot:
                for reason, count in snapshot['deletion_reasons'].items():
                    deletion_reasons[reason] += count
            
            # Track activity by hour and day
            deletion_count = snapshot['cluster_counts']['for_deletion']
            hourly_activity[hour] += deletion_count
            daily_activity[date] += deletion_count
        
        # Prepare hourly distribution (0-23 hours)
        hourly_distribution = [hourly_activity[hour] for hour in range(24)]
        
        # Get top deletion reasons
        top_reasons = dict(deletion_reasons.most_common(5))
        
        return {
            'deletion_reasons': top_reasons,
            'hourly_distribution': hourly_distribution,
            'daily_activity': dict(daily_activity),
            'summary': {
                'total_deletion_candidates': sum(deletion_reasons.values()),
                'most_common_reason': deletion_reasons.most_common(1)[0] if deletion_reasons else ('None', 0),
                'peak_hour': max(range(24), key=lambda h: hourly_activity[h]) if hourly_activity else 0
            }
        }
    
    def get_compliance_stats(self, days: int = 30) -> Dict[str, Any]:
        """
        Get label compliance statistics over time.
        
        Args:
            days: Number of days to analyze
            
        Returns:
            Dictionary with compliance statistics
        """
        historical_data = self._get_historical_data(days)
        
        if not historical_data:
            return {
                'dates': [],
                'compliance_trend': [],
                'label_trends': {},
                'current_compliance': 0,
                'summary': {
                    'average_compliance': 0,
                    'compliance_direction': 'stable',
                    'worst_performing_label': 'N/A'
                }
            }
        
        # Track compliance over time
        daily_compliance = defaultdict(list)
        label_compliance_trends = defaultdict(lambda: defaultdict(list))
        
        for snapshot in historical_data:
            date = snapshot['timestamp'][:10]
            
            if 'label_compliance' in snapshot:
                compliance = snapshot['label_compliance']
                overall_rate = compliance.get('overall_compliance_rate', 0)
                daily_compliance[date].append(overall_rate)
                
                # Track individual label compliance
                for label_name, stats in compliance.get('label_stats', {}).items():
                    rate = stats.get('compliance_rate', 0)
                    label_compliance_trends[label_name][date].append(rate)
        
        # Calculate daily averages
        compliance_trend = []
        dates = []
        for date in sorted(daily_compliance.keys()):
            rates = daily_compliance[date]
            avg_rate = sum(rates) / len(rates)
            compliance_trend.append(avg_rate)
            dates.append(date)
        
        # Calculate label trends
        label_trends = {}
        for label_name, date_data in label_compliance_trends.items():
            label_trend = []
            for date in dates:
                if date in date_data:
                    rates = date_data[date]
                    avg_rate = sum(rates) / len(rates)
                    label_trend.append(avg_rate)
                else:
                    label_trend.append(0)
            label_trends[label_name] = label_trend
        
        # Calculate summary statistics
        current_compliance = compliance_trend[-1] if compliance_trend else 0
        average_compliance = sum(compliance_trend) / len(compliance_trend) if compliance_trend else 0
        
        # Determine compliance direction
        compliance_direction = 'stable'
        if len(compliance_trend) >= 7:
            recent_avg = sum(compliance_trend[-7:]) / 7
            older_avg = sum(compliance_trend[-14:-7]) / 7 if len(compliance_trend) >= 14 else recent_avg
            
            if recent_avg > older_avg + 5:
                compliance_direction = 'improving'
            elif recent_avg < older_avg - 5:
                compliance_direction = 'declining'
        
        # Find worst performing label
        worst_label = 'N/A'
        if label_trends:
            worst_rate = 100
            for label_name, trend in label_trends.items():
                if trend:
                    current_rate = trend[-1]
                    if current_rate < worst_rate:
                        worst_rate = current_rate
                        worst_label = label_name
        
        return {
            'dates': dates,
            'compliance_trend': compliance_trend,
            'label_trends': label_trends,
            'current_compliance': current_compliance,
            'summary': {
                'average_compliance': average_compliance,
                'compliance_direction': compliance_direction,
                'worst_performing_label': worst_label
            }
        }
    
    def get_namespace_activity(self, days: int = 30) -> Dict[str, Any]:
        """
        Get namespace activity and cluster distribution.
        
        Args:
            days: Number of days to analyze
            
        Returns:
            Dictionary with namespace activity data
        """
        historical_data = self._get_historical_data(days)
        
        namespace_stats = defaultdict(lambda: {
            'total_clusters': 0,
            'deletion_clusters': 0,
            'protected_clusters': 0,
            'activity_score': 0
        })
        
        namespace_trends = defaultdict(lambda: defaultdict(int))
        
        for snapshot in historical_data:
            date = snapshot['timestamp'][:10]
            
            if 'clusters_by_namespace' in snapshot:
                for namespace, counts in snapshot['clusters_by_namespace'].items():
                    stats = namespace_stats[namespace]
                    stats['total_clusters'] = max(stats['total_clusters'], counts.get('total', 0))
                    stats['deletion_clusters'] += counts.get('deletion', 0)
                    stats['protected_clusters'] += counts.get('excluded', 0)
                    stats['activity_score'] += counts.get('total', 0)
                    
                    # Track trends
                    namespace_trends[namespace][date] += counts.get('total', 0)
        
        # Sort namespaces by activity
        sorted_namespaces = sorted(
            namespace_stats.items(),
            key=lambda x: x[1]['activity_score'],
            reverse=True
        )
        
        # Prepare data for heatmap/charts
        top_namespaces = dict(sorted_namespaces[:10])
        
        return {
            'namespace_stats': dict(namespace_stats),
            'top_namespaces': top_namespaces,
            'namespace_trends': dict(namespace_trends),
            'summary': {
                'total_namespaces': len(namespace_stats),
                'most_active_namespace': sorted_namespaces[0][0] if sorted_namespaces else 'N/A',
                'average_clusters_per_namespace': sum(s['total_clusters'] for s in namespace_stats.values()) / len(namespace_stats) if namespace_stats else 0
            }
        }
    
    def get_owner_distribution(self, days: int = 30) -> Dict[str, Any]:
        """
        Get cluster ownership distribution and trends.
        
        Args:
            days: Number of days to analyze
            
        Returns:
            Dictionary with ownership data
        """
        historical_data = self._get_historical_data(days)
        
        owner_stats = defaultdict(lambda: {
            'total_clusters': 0,
            'deletion_clusters': 0,
            'protected_clusters': 0
        })
        
        for snapshot in historical_data:
            if 'clusters_by_owner' in snapshot:
                for owner, counts in snapshot['clusters_by_owner'].items():
                    stats = owner_stats[owner]
                    stats['total_clusters'] = max(stats['total_clusters'], counts.get('total', 0))
                    stats['deletion_clusters'] += counts.get('deletion', 0)
                    stats['protected_clusters'] += counts.get('excluded', 0)
        
        # Sort by total clusters
        sorted_owners = sorted(
            owner_stats.items(),
            key=lambda x: x[1]['total_clusters'],
            reverse=True
        )
        
        return {
            'owner_stats': dict(owner_stats),
            'top_owners': dict(sorted_owners[:10]),
            'summary': {
                'total_owners': len(owner_stats),
                'no_owner_clusters': owner_stats.get('no-owner', {}).get('total_clusters', 0),
                'largest_owner': sorted_owners[0][0] if sorted_owners else 'N/A'
            }
        }
    
    def get_expiration_analysis(self, days: int = 30) -> Dict[str, Any]:
        """
        Get cluster expiration pattern analysis.
        
        Args:
            days: Number of days to analyze
            
        Returns:
            Dictionary with expiration analysis
        """
        historical_data = self._get_historical_data(days)
        
        expiration_patterns = Counter()
        expiration_trends = defaultdict(lambda: defaultdict(int))
        
        for snapshot in historical_data:
            date = snapshot['timestamp'][:10]
            
            if 'expiration_analysis' in snapshot:
                analysis = snapshot['expiration_analysis']
                
                # Track common expires values
                for expires_value, count in analysis.get('common_expires_values', {}).items():
                    expiration_patterns[expires_value] += count
                
                # Track expiration bucket trends
                for bucket, count in analysis.get('buckets', {}).items():
                    expiration_trends[bucket][date] += count
        
        # Get most common expiration values
        common_patterns = dict(expiration_patterns.most_common(10))
        
        # Calculate current distribution from latest snapshot
        current_distribution = {}
        if historical_data:
            latest = historical_data[-1]
            if 'expiration_analysis' in latest:
                current_distribution = latest['expiration_analysis'].get('buckets', {})
        
        return {
            'common_expiration_patterns': common_patterns,
            'current_distribution': current_distribution,
            'expiration_trends': dict(expiration_trends),
            'summary': {
                'most_common_duration': expiration_patterns.most_common(1)[0] if expiration_patterns else ('N/A', 0),
                'clusters_without_expiration': current_distribution.get('no_expiration', 0),
                'expired_clusters': current_distribution.get('expired', 0)
            }
        }
    
    def get_dashboard_summary(self) -> Dict[str, Any]:
        """
        Get a comprehensive summary for the dashboard.
        
        Returns:
            Dictionary with dashboard summary data
        """
        try:
            # Get recent data for quick summary
            trends_7d = self.get_cluster_trends(7)
            trends_30d = self.get_cluster_trends(30)
            compliance = self.get_compliance_stats(7)
            
            return {
                'current_status': {
                    'clusters_for_deletion': trends_7d['summary']['current_for_deletion'],
                    'clusters_protected': trends_7d['summary']['current_protected'],
                    'compliance_rate': compliance['current_compliance'],
                    'trend_direction': trends_7d['summary']['trend_direction']
                },
                'week_summary': {
                    'average_deletions': trends_7d['summary']['average_daily_deletions'],
                    'trend_direction': trends_7d['summary']['trend_direction']
                },
                'month_summary': {
                    'average_deletions': trends_30d['summary']['average_daily_deletions'],
                    'compliance_direction': compliance['summary']['compliance_direction']
                }
            }
        except Exception as e:
            return {
                'error': str(e),
                'current_status': {
                    'clusters_for_deletion': 0,
                    'clusters_protected': 0,
                    'compliance_rate': 0,
                    'trend_direction': 'unknown'
                }
            }
    
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