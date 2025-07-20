"""
Prometheus metrics service for NKP Cluster Cleaner.

This module generates Prometheus-formatted metrics from analytics data.
"""

from datetime import datetime
from typing import Dict, Any, List, Optional
from .redis_analytics_service import RedisAnalyticsService
import nkp_cluster_cleaner

__version__ = nkp_cluster_cleaner.__version__


class PrometheusMetricsService:
    """Service for generating Prometheus metrics from analytics data."""
    
    def __init__(self, analytics_service: Optional[RedisAnalyticsService] = None):
        """
        Initialize the Prometheus metrics service.
        
        Args:
            analytics_service: Optional analytics service instance
        """
        self.analytics_service = analytics_service
    
    def generate_metrics(self) -> str:
        """
        Generate Prometheus metrics in text format.
        
        Returns:
            Prometheus metrics as a string
        """
        if not self.analytics_service:
            return self._generate_basic_metrics()
        
        try:
            # Get analytics data
            dashboard_summary = self.analytics_service.get_dashboard_summary()
            cluster_trends_7d = self.analytics_service.get_cluster_trends(7)
            compliance_stats = self.analytics_service.get_compliance_stats(30)
            deletion_activity = self.analytics_service.get_deletion_activity(14)
            namespace_activity = self.analytics_service.get_namespace_activity(30)
            owner_distribution = self.analytics_service.get_owner_distribution(30)
            expiration_analysis = self.analytics_service.get_expiration_analysis(30)
            database_stats = self.analytics_service.get_database_stats()
            
            metrics_lines = []
            
            # Application info
            metrics_lines.extend(self._get_application_metrics(enabled=True))
            
            # Current cluster status
            metrics_lines.extend(self._get_cluster_status_metrics(dashboard_summary))
            
            # Trend metrics
            metrics_lines.extend(self._get_trend_metrics(cluster_trends_7d, dashboard_summary))
            
            # Compliance metrics
            metrics_lines.extend(self._get_compliance_metrics(compliance_stats))
            
            # Activity metrics
            metrics_lines.extend(self._get_activity_metrics(deletion_activity))
            
            # Namespace metrics
            metrics_lines.extend(self._get_namespace_metrics(namespace_activity))
            
            # Owner metrics
            metrics_lines.extend(self._get_owner_metrics(owner_distribution))
            
            # Expiration metrics
            metrics_lines.extend(self._get_expiration_metrics(expiration_analysis))
            
            # Infrastructure metrics
            metrics_lines.extend(self._get_infrastructure_metrics(database_stats))
            
            # Timestamp
            metrics_lines.extend(self._get_timestamp_metrics())
            
            return '\n'.join(metrics_lines)
            
        except Exception as e:
            return self._generate_error_metrics(str(e))
    
    def _generate_basic_metrics(self) -> str:
        """Generate basic metrics when analytics is disabled."""
        metrics_lines = []
        metrics_lines.extend(self._get_application_metrics(enabled=False))
        return '\n'.join(metrics_lines)
    
    def _generate_error_metrics(self, error: str) -> str:
        """Generate error metrics when analytics fails."""
        metrics_lines = []
        metrics_lines.extend(self._get_application_metrics(enabled=False))
        metrics_lines.extend([
            "# HELP nkp_cluster_cleaner_analytics_error Analytics error status (1=error, 0=ok)",
            "# TYPE nkp_cluster_cleaner_analytics_error gauge",
            "nkp_cluster_cleaner_analytics_error 1",
            ""
        ])
        return '\n'.join(metrics_lines)
    
    def _get_application_metrics(self, enabled: bool = True) -> List[str]:
        """Get application information metrics."""
        return [
            "# HELP nkp_cluster_cleaner_info Application information",
            "# TYPE nkp_cluster_cleaner_info gauge",
            f'nkp_cluster_cleaner_info{{version="{__version__}"}} 1',
            "",
            "# HELP nkp_cluster_cleaner_analytics_enabled Analytics feature status",
            "# TYPE nkp_cluster_cleaner_analytics_enabled gauge",
            f"nkp_cluster_cleaner_analytics_enabled {1 if enabled else 0}",
            ""
        ]
    
    def _get_cluster_status_metrics(self, dashboard_summary: Dict[str, Any]) -> List[str]:
        """Get current cluster status metrics."""
        if 'error' in dashboard_summary:
            return []
        
        current_status = dashboard_summary.get('current_status', {})
        week_summary = dashboard_summary.get('week_summary', {})
        
        return [
            "# HELP nkp_cluster_cleaner_clusters_for_deletion Current number of clusters marked for deletion",
            "# TYPE nkp_cluster_cleaner_clusters_for_deletion gauge",
            f"nkp_cluster_cleaner_clusters_for_deletion {current_status.get('clusters_for_deletion', 0)}",
            "",
            "# HELP nkp_cluster_cleaner_clusters_protected Current number of protected clusters",
            "# TYPE nkp_cluster_cleaner_clusters_protected gauge",
            f"nkp_cluster_cleaner_clusters_protected {current_status.get('clusters_protected', 0)}",
            "",
            "# HELP nkp_cluster_cleaner_compliance_rate Current label compliance rate (0-100)",
            "# TYPE nkp_cluster_cleaner_compliance_rate gauge",
            f"nkp_cluster_cleaner_compliance_rate {current_status.get('compliance_rate', 0)}",
            "",
            "# HELP nkp_cluster_cleaner_average_deletions_7d Average daily deletions over last 7 days",
            "# TYPE nkp_cluster_cleaner_average_deletions_7d gauge",
            f"nkp_cluster_cleaner_average_deletions_7d {week_summary.get('average_deletions', 0)}",
            ""
        ]
    
    def _get_trend_metrics(self, cluster_trends_7d: Dict[str, Any], dashboard_summary: Dict[str, Any]) -> List[str]:
        """Get trend direction metrics."""
        if not cluster_trends_7d or 'summary' not in cluster_trends_7d:
            return []
        
        trend_summary = cluster_trends_7d['summary']
        trend_direction_value = self._encode_trend_direction(trend_summary.get('trend_direction'))
        
        return [
            "# HELP nkp_cluster_cleaner_deletion_trend_direction Deletion trend direction (-1=decreasing, 0=stable, 1=increasing)",
            "# TYPE nkp_cluster_cleaner_deletion_trend_direction gauge",
            f"nkp_cluster_cleaner_deletion_trend_direction {trend_direction_value}",
            ""
        ]
    
    def _get_compliance_metrics(self, compliance_stats: Dict[str, Any]) -> List[str]:
        """Get compliance metrics."""
        if not compliance_stats or 'summary' not in compliance_stats:
            return []
        
        compliance_summary = compliance_stats['summary']
        compliance_direction_value = self._encode_compliance_direction(compliance_summary.get('compliance_direction'))
        
        metrics = [
            "# HELP nkp_cluster_cleaner_compliance_trend_direction Compliance trend direction (-1=declining, 0=stable, 1=improving)",
            "# TYPE nkp_cluster_cleaner_compliance_trend_direction gauge",
            f"nkp_cluster_cleaner_compliance_trend_direction {compliance_direction_value}",
            "",
            "# HELP nkp_cluster_cleaner_average_compliance Average compliance rate over the analysis period",
            "# TYPE nkp_cluster_cleaner_average_compliance gauge",
            f"nkp_cluster_cleaner_average_compliance {compliance_summary.get('average_compliance', 0)}",
            ""
        ]
        
        # Worst performing label
        worst_label = compliance_summary.get('worst_performing_label', 'N/A')
        if worst_label != 'N/A':
            safe_label = self._sanitize_label_value(worst_label)
            metrics.extend([
                "# HELP nkp_cluster_cleaner_worst_label_info Information about worst performing label",
                "# TYPE nkp_cluster_cleaner_worst_label_info gauge",
                f'nkp_cluster_cleaner_worst_label_info{{label="{safe_label}"}} 1',
                ""
            ])
        
        return metrics
    
    def _get_activity_metrics(self, deletion_activity: Dict[str, Any]) -> List[str]:
        """Get deletion activity metrics."""
        if not deletion_activity or 'summary' not in deletion_activity:
            return []
        
        activity_summary = deletion_activity['summary']
        metrics = [
            "# HELP nkp_cluster_cleaner_total_deletion_candidates Total deletion candidates in analysis period",
            "# TYPE nkp_cluster_cleaner_total_deletion_candidates counter",
            f"nkp_cluster_cleaner_total_deletion_candidates {activity_summary.get('total_deletion_candidates', 0)}",
            "",
            "# HELP nkp_cluster_cleaner_peak_hour Peak hour for deletion activity (0-23)",
            "# TYPE nkp_cluster_cleaner_peak_hour gauge",
            f"nkp_cluster_cleaner_peak_hour {activity_summary.get('peak_hour', 0)}",
            ""
        ]
        
        # Deletion reasons
        if 'deletion_reasons' in deletion_activity:
            metrics.extend([
                "# HELP nkp_cluster_cleaner_deletion_reason_count Number of clusters by deletion reason",
                "# TYPE nkp_cluster_cleaner_deletion_reason_count counter"
            ])
            for reason, count in deletion_activity['deletion_reasons'].items():
                safe_reason = self._sanitize_label_value(reason)
                metrics.append(f'nkp_cluster_cleaner_deletion_reason_count{{reason="{safe_reason}"}} {count}')
            metrics.append("")
        
        return metrics
    
    def _get_namespace_metrics(self, namespace_activity: Dict[str, Any]) -> List[str]:
        """Get namespace activity metrics."""
        if not namespace_activity or 'summary' not in namespace_activity:
            return []
        
        ns_summary = namespace_activity['summary']
        metrics = [
            "# HELP nkp_cluster_cleaner_total_namespaces Total number of namespaces with clusters",
            "# TYPE nkp_cluster_cleaner_total_namespaces gauge",
            f"nkp_cluster_cleaner_total_namespaces {ns_summary.get('total_namespaces', 0)}",
            "",
            "# HELP nkp_cluster_cleaner_avg_clusters_per_namespace Average clusters per namespace",
            "# TYPE nkp_cluster_cleaner_avg_clusters_per_namespace gauge",
            f"nkp_cluster_cleaner_avg_clusters_per_namespace {ns_summary.get('average_clusters_per_namespace', 0)}",
            ""
        ]
        
        # Top namespaces
        if 'top_namespaces' in namespace_activity:
            metrics.extend([
                "# HELP nkp_cluster_cleaner_namespace_clusters Number of clusters by namespace",
                "# TYPE nkp_cluster_cleaner_namespace_clusters gauge"
            ])
            for namespace, stats in namespace_activity['top_namespaces'].items():
                safe_namespace = self._sanitize_label_value(namespace)
                metrics.append(f'nkp_cluster_cleaner_namespace_clusters{{namespace="{safe_namespace}",type="total"}} {stats.get("total_clusters", 0)}')
                metrics.append(f'nkp_cluster_cleaner_namespace_clusters{{namespace="{safe_namespace}",type="deletion"}} {stats.get("deletion_clusters", 0)}')
                metrics.append(f'nkp_cluster_cleaner_namespace_clusters{{namespace="{safe_namespace}",type="protected"}} {stats.get("protected_clusters", 0)}')
            metrics.append("")
        
        return metrics
    
    def _get_owner_metrics(self, owner_distribution: Dict[str, Any]) -> List[str]:
        """Get owner distribution metrics."""
        if not owner_distribution or 'summary' not in owner_distribution:
            return []
        
        owner_summary = owner_distribution['summary']
        metrics = [
            "# HELP nkp_cluster_cleaner_total_owners Total number of cluster owners",
            "# TYPE nkp_cluster_cleaner_total_owners gauge",
            f"nkp_cluster_cleaner_total_owners {owner_summary.get('total_owners', 0)}",
            "",
            "# HELP nkp_cluster_cleaner_avg_clusters_per_owner Average clusters per owner",
            "# TYPE nkp_cluster_cleaner_avg_clusters_per_owner gauge",
            f"nkp_cluster_cleaner_avg_clusters_per_owner {owner_summary.get('average_clusters_per_owner', 0)}",
            ""
        ]
        
        # Top owners
        if 'top_owners' in owner_distribution:
            metrics.extend([
                "# HELP nkp_cluster_cleaner_owner_clusters Number of clusters by owner",
                "# TYPE nkp_cluster_cleaner_owner_clusters gauge"
            ])
            for owner, stats in owner_distribution['top_owners'].items():
                safe_owner = self._sanitize_label_value(owner)
                metrics.append(f'nkp_cluster_cleaner_owner_clusters{{owner="{safe_owner}",type="total"}} {stats.get("total_clusters", 0)}')
                metrics.append(f'nkp_cluster_cleaner_owner_clusters{{owner="{safe_owner}",type="deletion"}} {stats.get("deletion_clusters", 0)}')
                metrics.append(f'nkp_cluster_cleaner_owner_clusters{{owner="{safe_owner}",type="protected"}} {stats.get("protected_clusters", 0)}')
            metrics.append("")
        
        return metrics
    
    def _get_expiration_metrics(self, expiration_analysis: Dict[str, Any]) -> List[str]:
        """Get expiration analysis metrics."""
        if not expiration_analysis or 'summary' not in expiration_analysis:
            return []
        
        exp_summary = expiration_analysis['summary']
        metrics = [
            "# HELP nkp_cluster_cleaner_clusters_without_expiration Number of clusters without expiration date",
            "# TYPE nkp_cluster_cleaner_clusters_without_expiration gauge",
            f"nkp_cluster_cleaner_clusters_without_expiration {exp_summary.get('clusters_without_expiration', 0)}",
            "",
            "# HELP nkp_cluster_cleaner_expired_clusters Number of expired clusters",
            "# TYPE nkp_cluster_cleaner_expired_clusters gauge",
            f"nkp_cluster_cleaner_expired_clusters {exp_summary.get('expired_clusters', 0)}",
            ""
        ]
        
        # Current expiration distribution
        if 'current_distribution' in expiration_analysis:
            metrics.extend([
                "# HELP nkp_cluster_cleaner_expiration_buckets Number of clusters by expiration timeframe",
                "# TYPE nkp_cluster_cleaner_expiration_buckets gauge"
            ])
            for bucket, count in expiration_analysis['current_distribution'].items():
                safe_bucket = self._sanitize_label_value(bucket)
                metrics.append(f'nkp_cluster_cleaner_expiration_buckets{{timeframe="{safe_bucket}"}} {count}')
            metrics.append("")
        
        return metrics
    
    def _get_infrastructure_metrics(self, database_stats: Dict[str, Any]) -> List[str]:
        """Get infrastructure/Redis metrics."""
        if not database_stats or 'error' in database_stats:
            return []
        
        metrics = [
            "# HELP nkp_cluster_cleaner_snapshots_total Total number of analytics snapshots stored",
            "# TYPE nkp_cluster_cleaner_snapshots_total gauge",
            f"nkp_cluster_cleaner_snapshots_total {database_stats.get('total_snapshots', 0)}",
            "",
            "# HELP nkp_cluster_cleaner_redis_connected_clients Number of connected Redis clients",
            "# TYPE nkp_cluster_cleaner_redis_connected_clients gauge",
            f"nkp_cluster_cleaner_redis_connected_clients {database_stats.get('redis_connected_clients', 0)}",
            "",
            "# HELP nkp_cluster_cleaner_redis_uptime_days Redis uptime in days",
            "# TYPE nkp_cluster_cleaner_redis_uptime_days gauge",
            f"nkp_cluster_cleaner_redis_uptime_days {database_stats.get('redis_uptime_days', 0)}",
            ""
        ]
        
        # Add Redis memory metrics (parse human-readable values to bytes)
        if database_stats.get('redis_memory_used'):
            memory_used_bytes = self._parse_memory_value(database_stats['redis_memory_used'])
            if memory_used_bytes is not None:
                metrics.extend([
                    "# HELP nkp_cluster_cleaner_redis_memory_used_bytes Redis memory usage in bytes",
                    "# TYPE nkp_cluster_cleaner_redis_memory_used_bytes gauge",
                    f"nkp_cluster_cleaner_redis_memory_used_bytes {memory_used_bytes}",
                    ""
                ])
        
        if database_stats.get('redis_memory_peak'):
            memory_peak_bytes = self._parse_memory_value(database_stats['redis_memory_peak'])
            if memory_peak_bytes is not None:
                metrics.extend([
                    "# HELP nkp_cluster_cleaner_redis_memory_peak_bytes Redis peak memory usage in bytes",
                    "# TYPE nkp_cluster_cleaner_redis_memory_peak_bytes gauge",
                    f"nkp_cluster_cleaner_redis_memory_peak_bytes {memory_peak_bytes}",
                    ""
                ])
        
        # Add Redis version as info metric
        if database_stats.get('redis_version'):
            version = self._sanitize_label_value(database_stats['redis_version'])
            metrics.extend([
                "# HELP nkp_cluster_cleaner_redis_info Redis version information",
                "# TYPE nkp_cluster_cleaner_redis_info gauge",
                f'nkp_cluster_cleaner_redis_info{{version="{version}"}} 1',
                ""
            ])
        
        # Add snapshot date range metrics
        if database_stats.get('earliest_snapshot'):
            try:
                earliest_timestamp = datetime.fromisoformat(database_stats['earliest_snapshot'].replace('Z', '')).timestamp()
                metrics.extend([
                    "# HELP nkp_cluster_cleaner_earliest_snapshot_timestamp Unix timestamp of earliest stored snapshot",
                    "# TYPE nkp_cluster_cleaner_earliest_snapshot_timestamp gauge",
                    f"nkp_cluster_cleaner_earliest_snapshot_timestamp {earliest_timestamp}",
                    ""
                ])
            except (ValueError, AttributeError):
                pass
        
        if database_stats.get('latest_snapshot'):
            try:
                latest_timestamp = datetime.fromisoformat(database_stats['latest_snapshot'].replace('Z', '')).timestamp()
                metrics.extend([
                    "# HELP nkp_cluster_cleaner_latest_snapshot_timestamp Unix timestamp of latest stored snapshot",
                    "# TYPE nkp_cluster_cleaner_latest_snapshot_timestamp gauge",
                    f"nkp_cluster_cleaner_latest_snapshot_timestamp {latest_timestamp}",
                    ""
                ])
            except (ValueError, AttributeError):
                pass
        
        return metrics
    
    def _get_timestamp_metrics(self) -> List[str]:
        """Get timestamp metrics."""
        return [
            "# HELP nkp_cluster_cleaner_metrics_last_update_timestamp Unix timestamp of last metrics update",
            "# TYPE nkp_cluster_cleaner_metrics_last_update_timestamp gauge",
            f"nkp_cluster_cleaner_metrics_last_update_timestamp {datetime.now().timestamp()}",
            ""
        ]
    
    def _encode_trend_direction(self, direction: str) -> int:
        """Encode trend direction as numeric value."""
        if direction == 'increasing':
            return 1
        elif direction == 'decreasing':
            return -1
        else:
            return 0
    
    def _encode_compliance_direction(self, direction: str) -> int:
        """Encode compliance direction as numeric value."""
        if direction == 'improving':
            return 1
        elif direction == 'declining':
            return -1
        else:
            return 0
    
    def _sanitize_label_value(self, value: str) -> str:
        """Sanitize label values for Prometheus format."""
        return value.replace('"', '\\"').replace('\\', '\\\\')
    
    def _parse_memory_value(self, memory_str: str) -> Optional[int]:
        """
        Parse human-readable memory values to bytes.
        
        Args:
            memory_str: Memory value like "1.5M", "512K", "2G", etc.
            
        Returns:
            Memory value in bytes, or None if parsing fails
        """
        if not memory_str:
            return None
        
        try:
            # Remove any whitespace and convert to uppercase
            memory_str = memory_str.strip().upper()
            
            # Handle different suffixes
            multipliers = {
                'B': 1,
                'K': 1024,
                'KB': 1024,
                'M': 1024 * 1024,
                'MB': 1024 * 1024,
                'G': 1024 * 1024 * 1024,
                'GB': 1024 * 1024 * 1024,
                'T': 1024 * 1024 * 1024 * 1024,
                'TB': 1024 * 1024 * 1024 * 1024
            }
            
            # Extract number and suffix
            import re
            match = re.match(r'([0-9.]+)([A-Z]*)', memory_str)
            if not match:
                return None
            
            number_str, suffix = match.groups()
            number = float(number_str)
            
            # Default to bytes if no suffix
            if not suffix:
                suffix = 'B'
            
            if suffix in multipliers:
                return int(number * multipliers[suffix])
            else:
                return None
                
        except (ValueError, AttributeError):
            return None