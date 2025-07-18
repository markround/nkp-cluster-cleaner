"""
Data collection service for NKP Cluster Cleaner analytics.

This module handles collecting cluster data snapshots for historical analysis
and reporting. Data is stored in JSON files with daily rotation.
"""

import json
import yaml
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict, Counter
from .cluster_manager import ClusterManager
from .config import ConfigManager


class DataCollector:
    """Collects and stores cluster analytics data."""
    
    def __init__(self, kubeconfig_path: Optional[str] = None, config_manager: Optional[ConfigManager] = None, 
                 data_dir: str = '/app/data', debug: bool = False):
        """
        Initialize the data collector.
        
        Args:
            kubeconfig_path: Path to kubeconfig file
            config_manager: Configuration manager instance
            data_dir: Directory to store analytics data files
            debug: Enable debug output
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.debug = debug
        
        self.config_manager = config_manager or ConfigManager()
        self.cluster_manager = ClusterManager(kubeconfig_path, self.config_manager)
        
    def collect_snapshot(self) -> Dict[str, Any]:
        """
        Collect current cluster state and store as historical data point.
        
        Returns:
            Dictionary containing the collected snapshot data
        """
        timestamp = datetime.now()
        print(f"Collecting analytics snapshot at {timestamp.isoformat()}")
        
        try:
            # Get current cluster state
            self._debug_print("Getting cluster data...")
            clusters_to_delete, excluded_clusters = self.cluster_manager.get_clusters_with_exclusions()
            self._debug_print(f"Found {len(clusters_to_delete)} clusters for deletion, {len(excluded_clusters)} excluded")
            
            # Convert to consistent format with status indicator
            self._debug_print("Converting to consistent format...")
            all_clusters = []
            for cluster, reason in clusters_to_delete:
                all_clusters.append((cluster, reason, 'deletion'))
            for cluster, reason in excluded_clusters:
                all_clusters.append((cluster, reason, 'excluded'))
            
            self._debug_print(f"Total clusters processed: {len(all_clusters)}")
            
            # Test that our tuples are correctly formatted
            if all_clusters:
                self._debug_print(f"Sample tuple structure: {len(all_clusters[0])} elements")
            
            self._debug_print("Building snapshot data...")
            
            try:
                self._debug_print("Getting unique namespaces...")
                unique_namespaces = self._get_unique_namespaces(all_clusters)
                self._debug_print(f"Found {len(unique_namespaces)} namespaces")
            except Exception as e:
                print(f"Error in _get_unique_namespaces: {e}")
                raise
            
            try:
                self._debug_print("Grouping by namespace...")
                clusters_by_namespace = self._group_by_namespace(all_clusters)
                self._debug_print(f"Namespace grouping complete")
            except Exception as e:
                print(f"Error in _group_by_namespace: {e}")
                raise
            
            try:
                self._debug_print("Grouping by owner...")
                clusters_by_owner = self._group_by_owner(all_clusters)
                self._debug_print(f"Owner grouping complete")
            except Exception as e:
                print(f"Error in _group_by_owner: {e}")
                raise
            
            try:
                self._debug_print("Grouping by status...")
                clusters_by_status = self._group_by_status(all_clusters)
                self._debug_print(f"Status grouping complete")
            except Exception as e:
                print(f"Error in _group_by_status: {e}")
                raise
            
            try:
                self._debug_print("Analyzing expiration patterns...")
                expiration_analysis = self._analyze_expiration_patterns(all_clusters)
                self._debug_print(f"Expiration analysis complete")
            except Exception as e:
                print(f"Error in _analyze_expiration_patterns: {e}")
                raise
            
            try:
                self._debug_print("Calculating label compliance...")
                label_compliance = self._calculate_label_compliance(all_clusters)
                self._debug_print(f"Label compliance complete")
            except Exception as e:
                print(f"Error in _calculate_label_compliance: {e}")
                raise
            
            try:
                self._debug_print("Analyzing protection rules...")
                # Convert excluded_clusters to 3-element tuples for consistency
                excluded_clusters_with_status = [(cluster, reason, 'excluded') for cluster, reason in excluded_clusters]
                if self.debug and excluded_clusters_with_status:
                    self._debug_print(f"Converted {len(excluded_clusters_with_status)} excluded clusters, sample: {len(excluded_clusters_with_status[0])} elements")
                protection_rule_effectiveness = self._analyze_protection_rules(excluded_clusters_with_status)
                self._debug_print(f"Protection rules analysis complete")
            except Exception as e:
                print(f"Error in _analyze_protection_rules: {e}")
                # Debug: let's see what we're actually working with
                if self.debug:
                    print(f"excluded_clusters type: {type(excluded_clusters)}")
                    print(f"excluded_clusters length: {len(excluded_clusters)}")
                    if excluded_clusters:
                        print(f"Sample excluded_clusters item: {excluded_clusters[0]}")
                        print(f"Sample excluded_clusters item length: {len(excluded_clusters[0])}")
                raise
            
            try:
                self._debug_print("Calculating age distribution...")
                cluster_age_distribution = self._calculate_age_distribution(all_clusters)
                self._debug_print(f"Age distribution complete")
            except Exception as e:
                print(f"Error in _calculate_age_distribution: {e}")
                raise
            
            try:
                self._debug_print("Analyzing deletion reasons...")
                # Convert clusters_to_delete to 3-element tuples for consistency  
                clusters_to_delete_with_status = [(cluster, reason, 'deletion') for cluster, reason in clusters_to_delete]
                deletion_reasons = self._analyze_deletion_reasons(clusters_to_delete_with_status)
                self._debug_print(f"Deletion reasons analysis complete")
            except Exception as e:
                print(f"Error in _analyze_deletion_reasons: {e}")
                raise
            
            # Build comprehensive snapshot
            snapshot = {
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
                'clusters_by_namespace': clusters_by_namespace,
                'clusters_by_owner': clusters_by_owner,
                'clusters_by_status': clusters_by_status,
                'expiration_analysis': expiration_analysis,
                'label_compliance': label_compliance,
                'protection_rule_effectiveness': protection_rule_effectiveness,
                'cluster_age_distribution': cluster_age_distribution,
                'deletion_reasons': deletion_reasons
            }
            
            # Store the snapshot
            self._debug_print("Storing snapshot...")
            self._store_snapshot(snapshot)
            
            print(f"Analytics snapshot collected successfully:")
            print(f"  - Total clusters: {len(all_clusters)}")
            print(f"  - For deletion: {len(clusters_to_delete)}")
            print(f"  - Protected: {len(excluded_clusters)}")
            print(f"  - Namespaces: {len(unique_namespaces)}")
            
            return snapshot
            
        except Exception as e:
            print(f"Error collecting analytics snapshot: {e}")
            raise
    
    def _store_snapshot(self, snapshot: Dict[str, Any]):
        """
        Store snapshot in daily file with rotation.
        
        Args:
            snapshot: Snapshot data to store
        """
        # Use date for filename
        date = datetime.now().strftime('%Y-%m-%d')
        file_path = self.data_dir / f"analytics_{date}.json"
        
        # Load existing daily data
        daily_data = []
        if file_path.exists():
            try:
                with open(file_path, 'r') as f:
                    daily_data = json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                daily_data = []
        
        # Append new snapshot
        daily_data.append(snapshot)
        
        # Write updated data
        with open(file_path, 'w') as f:
            json.dump(daily_data, f, indent=2, default=str)
        
        # Clean up old files (keep last 90 days)
        self._cleanup_old_files(days_to_keep=90)
    
    def _cleanup_old_files(self, days_to_keep: int = 90):
        """
        Remove analytics files older than specified days.
        
        Args:
            days_to_keep: Number of days of data to retain
        """
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        
        for file_path in self.data_dir.glob("analytics_*.json"):
            try:
                # Extract date from filename
                date_str = file_path.stem.replace('analytics_', '')
                file_date = datetime.strptime(date_str, '%Y-%m-%d')
                
                if file_date < cutoff_date:
                    file_path.unlink()
                    print(f"Removed old analytics file: {file_path}")
                    
            except (ValueError, OSError) as e:
                print(f"Warning: Could not process file {file_path}: {e}")
    
    def _debug_print(self, message: str):
        """Print debug message if debug mode is enabled."""
        if self.debug:
            print(message)
    
    def _get_tool_version(self) -> str:
        """Get the current tool version."""
        try:
            import nkp_cluster_cleaner
            return nkp_cluster_cleaner.__version__
        except:
            return "unknown"
    
    def _get_unique_namespaces(self, all_clusters: List[Tuple]) -> set:
        """Get set of unique namespaces from cluster data."""
        namespaces = set()
        for cluster_info, reason, status in all_clusters:
            namespace = cluster_info.get('capi_cluster_namespace')
            if namespace:
                namespaces.add(namespace)
        return namespaces
    
    def _group_by_namespace(self, all_clusters: List[Tuple]) -> Dict[str, Dict[str, int]]:
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
    
    def _group_by_owner(self, all_clusters: List[Tuple]) -> Dict[str, Dict[str, int]]:
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
    
    def _group_by_status(self, all_clusters: List[Tuple]) -> Dict[str, int]:
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
    
    def _analyze_expiration_patterns(self, all_clusters: List[Tuple]) -> Dict[str, Any]:
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
    
    def _calculate_label_compliance(self, all_clusters: List[Tuple]) -> Dict[str, Any]:
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
    
    def _analyze_protection_rules(self, excluded_clusters: List[Tuple]) -> Dict[str, int]:
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
    
    def _calculate_age_distribution(self, all_clusters: List[Tuple]) -> Dict[str, int]:
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
    
    def _analyze_deletion_reasons(self, clusters_to_delete: List[Tuple]) -> Dict[str, int]:
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
    
    def get_historical_data(self, days: int = 30) -> List[Dict[str, Any]]:
        """
        Retrieve historical analytics data.
        
        Args:
            days: Number of days of historical data to retrieve
            
        Returns:
            List of snapshots from the specified time period
        """
        historical_data = []
        cutoff_date = datetime.now() - timedelta(days=days)
        
        # Get all analytics files
        for file_path in sorted(self.data_dir.glob("analytics_*.json")):
            try:
                # Extract date from filename
                date_str = file_path.stem.replace('analytics_', '')
                file_date = datetime.strptime(date_str, '%Y-%m-%d')
                
                if file_date >= cutoff_date:
                    with open(file_path, 'r') as f:
                        daily_snapshots = json.load(f)
                        historical_data.extend(daily_snapshots)
                        
            except (ValueError, json.JSONDecodeError, OSError) as e:
                print(f"Warning: Could not process file {file_path}: {e}")
        
        # Sort by timestamp
        historical_data.sort(key=lambda x: x['timestamp'])
        
        return historical_data