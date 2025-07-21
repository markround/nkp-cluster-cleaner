"""
Cluster Manager module for interacting with CAPI clusters.
"""

from kubernetes import client, config
from kubernetes.client.rest import ApiException
import yaml
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from colorama import Fore, Style
from .config import ConfigManager


class ClusterManager:
    """Manages CAPI cluster operations."""
    
    def __init__(self, kubeconfig_path: Optional[str] = None, config_manager: Optional[ConfigManager] = None):
        """
        Initialize the cluster manager.
        
        Args:
            kubeconfig_path: Path to kubeconfig file. If None, uses default locations.
            config_manager: Configuration manager instance
        """
        self.kubeconfig_path = kubeconfig_path
        self.config_manager = config_manager or ConfigManager()
        self._load_config()
        
    def _load_config(self):
        """Load Kubernetes configuration."""
        try:
            if self.kubeconfig_path:
                config.load_kube_config(config_file=self.kubeconfig_path)
            else:
                config.load_kube_config()
        except Exception as e:
            raise Exception(f"Failed to load kubeconfig: {e}")
            
        # Initialize API clients
        self.core_v1 = client.CoreV1Api()
        self.custom_api = client.CustomObjectsApi()

    def get_nkp_version(self) -> Optional[str]:
        """
        Get the NKP version from KommanderCore CRD.
        
        Returns:
            NKP version string or None if not found
        """
        try:
            # Get KommanderCore resources
            kommander_cores = self.custom_api.list_cluster_custom_object(
                group="dkp.d2iq.io",
                version="v1alpha1",
                plural="kommandercores"
            )
            
            # Look for a KommanderCore with version in status
            for core in kommander_cores.get("items", []):
                status = core.get("status", {})
                version = status.get("version")
                if version:
                    return version
            
            return None
        
        except ApiException as e:
            if e.status == 404:
                print(f"{Fore.YELLOW}Warning: KommanderCore CRDs not found.{Style.RESET_ALL}")
                return None
            else:
                print(f"{Fore.YELLOW}Warning: Could not retrieve NKP version: {e}{Style.RESET_ALL}")
                return None
        except Exception as e:
            print(f"{Fore.YELLOW}Warning: Could not retrieve NKP version: {e}{Style.RESET_ALL}")
            return None

    def list_all_kommander_clusters(self, namespace: Optional[str] = None) -> List[Dict]:
        """
        List all KommanderCluster objects across all namespaces or in a specific namespace.
        Excludes clusters that do not have a spec.clusterRef.capiCluster dictionary (attached clusters).
        
        Args:
            namespace: If specified, only list KommanderClusters in this namespace
        
        Returns:
            List of KommanderCluster objects with their namespaces
        """
        all_kommander_clusters = []
        
        try:
            if namespace:
                # List only in the specified namespace
                namespaces_to_check = [namespace]
            else:
                # Get all namespaces
                namespaces_response = self.core_v1.list_namespace()
                namespaces_to_check = [ns.metadata.name for ns in namespaces_response.items]
            
            for namespace_name in namespaces_to_check:
                try:
                    # List KommanderClusters in this namespace
                    response = self.custom_api.list_namespaced_custom_object(
                        group="kommander.mesosphere.io",
                        version="v1beta1",
                        namespace=namespace_name,
                        plural="kommanderclusters"
                    )
                    
                    kommander_clusters = response.get("items", [])
                    for kc in kommander_clusters:
                        # Filter out clusters without spec.clusterRef.capiCluster (attached clusters)
                        spec = kc.get("spec", {})
                        cluster_ref = spec.get("clusterRef", {})
                        capi_cluster = cluster_ref.get("capiCluster")
                        
                        # Skip clusters that don't have a capiCluster dictionary
                        if not isinstance(capi_cluster, dict):
                            kc_name = kc.get("metadata", {}).get("name", "unknown")
                            print(f"{Fore.CYAN}Info: Skipping attached cluster {kc_name} (no spec.clusterRef.capiCluster){Style.RESET_ALL}")
                            continue
                        
                        # Add namespace info for easier handling
                        kc["_namespace"] = namespace_name
                        all_kommander_clusters.append(kc)
                                      
                except ApiException as e:
                    if e.status == 404:
                        # No KommanderClusters in this namespace, continue
                        continue
                    else:
                        print(f"{Fore.YELLOW}Warning: Could not list KommanderClusters in namespace {namespace_name}: {e}{Style.RESET_ALL}")
                        continue
                    
        except ApiException as e:
            if e.status == 404:
                print(f"{Fore.YELLOW}Warning: KommanderCluster CRDs not found. Is Kommander installed?{Style.RESET_ALL}")
                return []
            raise Exception(f"Failed to list namespaces: {e}")
    
        return all_kommander_clusters
    
    def check_kommander_crds(self) -> bool:
        """
        Check if KommanderCluster CRDs are available.
        
        Returns:
            True if KommanderCluster CRDs are available
        """
        try:
            # Try to list KommanderClusters to check if CRDs exist
            self.custom_api.list_cluster_custom_object(
                group="kommander.mesosphere.io",
                version="v1beta1",
                plural="kommanderclusters"
            )
            return True
        except ApiException as e:
            if e.status == 404:
                print(f"{Fore.YELLOW}Warning: KommanderCluster CRDs not found. Is Kommander installed?{Style.RESET_ALL}")
                return False
            else:
                print(f"{Fore.YELLOW}Warning: Could not check KommanderCluster CRDs: {e}{Style.RESET_ALL}")
                return False
    
    def get_cluster_labels(self, kommander_cluster: Dict) -> Dict[str, str]:
        """
        Extract labels from a KommanderCluster object.
        
        Args:
            kommander_cluster: KommanderCluster object from Kubernetes API
            
        Returns:
            Dictionary of labels
        """
        metadata = kommander_cluster.get("metadata", {})
        return metadata.get("labels", {})
    
    def kommander_cluster_matches_criteria(self, kommander_cluster: Dict) -> Tuple[bool, str]:
        """
        Check if a KommanderCluster matches deletion criteria.
        
        Args:
            kommander_cluster: KommanderCluster object with _namespace field
            
        Returns:
            Tuple of (should_delete, reason)
        """
        kc_name = kommander_cluster.get("metadata", {}).get("name", "unknown")
        kc_namespace = kommander_cluster.get("_namespace", "unknown")
        
        # Special case: management cluster (host-cluster) should always be excluded!
        # If the name ever changes, this will be a problem but a recent Slack conversation indicated
        # that although the display name may change, the object name should stay consistent in future
        # releases
        if kc_name == "host-cluster":
            return False, f"Cluster is a management cluster"
        
        # Check if KommanderCluster is protected by configuration
        if self.config_manager.is_cluster_protected(kc_name, kc_namespace):
            return False, f"KommanderCluster {kc_name} is protected by configuration"
        
        # Get labels from KommanderCluster
        labels = self.get_cluster_labels(kommander_cluster)
        
        # Check if expires label exists
        if "expires" not in labels:
            return True, f"Missing 'expires' label"
        
        # Validate extra labels
        extra_label_errors = self.config_manager.validate_extra_labels(labels)
        if extra_label_errors:
            # Return the first error as the reason
            return True, extra_label_errors[0]
        
        # Parse and check expires label
        expires_value = labels["expires"]
        try:
            # Get creation timestamp from metadata
            creation_timestamp = kommander_cluster.get("metadata", {}).get("creationTimestamp")
            if not creation_timestamp:
                return True, f"Missing creationTimestamp in KommanderCluster metadata"
            
            expiry_time = self._parse_expires_label(expires_value, creation_timestamp)
            current_time = datetime.now()
            
            if current_time >= expiry_time:
                return True, f"Cluster has expired (created: {creation_timestamp[:10]}, expires after: {expires_value})"
            else:
                remaining_time = expiry_time - current_time
                days_remaining = remaining_time.days
                hours_remaining = remaining_time.seconds // 3600
                if days_remaining > 0:
                    time_remaining = f"{days_remaining}d"
                else:
                    time_remaining = f"{hours_remaining}h"
                return False, f"Cluster has not expired yet (expires in ~{time_remaining})"
                
        except ValueError as e:
            return True, f"Invalid 'expires' label format: {expires_value} ({e})"
    
    def get_capi_cluster_reference(self, kommander_cluster: Dict) -> Tuple[Optional[str], Optional[str]]:
        """
        Extract CAPI cluster reference from KommanderCluster.
        
        Args:
            kommander_cluster: KommanderCluster object
            
        Returns:
            Tuple of (cluster_name, cluster_namespace) or (None, None) if not found
        """
        spec = kommander_cluster.get("spec", {})
        cluster_ref = spec.get("clusterRef", {})
        capi_cluster = cluster_ref.get("capiCluster", {})
        
        cluster_name = capi_cluster.get("name")
        cluster_namespace = capi_cluster.get("namespace")
        
        return cluster_name, cluster_namespace
    
    def verify_capi_cluster_exists(self, cluster_name: str, cluster_namespace: str) -> bool:
        """
        Verify that the referenced CAPI cluster actually exists.
        
        Args:
            cluster_name: Name of the CAPI cluster
            cluster_namespace: Namespace of the CAPI cluster
            
        Returns:
            True if the CAPI cluster exists
        """
        try:
            self.custom_api.get_namespaced_custom_object(
                group="cluster.x-k8s.io",
                version="v1beta1",
                namespace=cluster_namespace,
                plural="clusters",
                name=cluster_name
            )
            return True
        except ApiException as e:
            if e.status == 404:
                return False
            else:
                print(f"{Fore.YELLOW}Warning: Could not verify CAPI cluster {cluster_name}: {e}{Style.RESET_ALL}")
                return False
    
    def _parse_expires_label(self, expires_value: str, creation_timestamp: str) -> datetime:
        """
        Parse expires label value and calculate actual expiry time based on creation timestamp.
        
        Args:
            expires_value: String like "1d", "2w", "48h", "1y", etc.
            creation_timestamp: ISO format timestamp like "2025-06-23T07:04:37Z"
            
        Returns:
            datetime object representing when the cluster expires
            
        Raises:
            ValueError: If format is invalid
        """
        # Remove whitespace
        expires_value = expires_value.strip()
        
        # Parse number and unit
        pattern = r'^(\d+)([dhwy])$'
        match = re.match(pattern, expires_value.lower())
        
        if not match:
            raise ValueError(f"Invalid format. Expected format: <number><unit> where unit is d/w/h/y (e.g., '1d', '2w', '48h', '1y')")
        
        number, unit = match.groups()
        number = int(number)
        
        # Calculate timedelta
        if unit == 'h':
            delta = timedelta(hours=number)
        elif unit == 'd':
            delta = timedelta(days=number)
        elif unit == 'w':
            delta = timedelta(weeks=number)
        elif unit == 'y':
            delta = timedelta(days=number * 365)
        else:
            raise ValueError(f"Unsupported time unit: {unit}")
        
        # Parse creation timestamp
        try:
            # Handle ISO format with Z suffix
            if creation_timestamp.endswith('Z'):
                creation_time = datetime.fromisoformat(creation_timestamp[:-1])
            else:
                creation_time = datetime.fromisoformat(creation_timestamp)
        except ValueError as e:
            raise ValueError(f"Invalid creation timestamp format: {creation_timestamp} ({e})")
        
        # Return creation time plus the delta (when it expires)
        return creation_time + delta
    
    def delete_cluster(self, cluster_name: str, cluster_namespace: str, dry_run: bool = False) -> bool:
        """
        Delete a CAPI cluster.
        
        Args:
            cluster_name: Name of the CAPI cluster to delete
            cluster_namespace: Namespace of the CAPI cluster
            dry_run: If True, only simulate the deletion
            
        Returns:
            True if deletion was successful (or simulated)
        """
        if dry_run:
            print(f"{Fore.YELLOW}[DRY RUN] Would delete cluster: {cluster_name} in namespace {cluster_namespace}{Style.RESET_ALL}")
            return True
        
        try:
            self.custom_api.delete_namespaced_custom_object(
                group="cluster.x-k8s.io",
                version="v1beta1",
                namespace=cluster_namespace,
                plural="clusters",
                name=cluster_name
            )
            print(f"{Fore.GREEN}Successfully deleted cluster: {cluster_name} in namespace {cluster_namespace}{Style.RESET_ALL}")
            return True
        except ApiException as e:
            print(f"{Fore.RED}Failed to delete cluster {cluster_name} in namespace {cluster_namespace}: {e}{Style.RESET_ALL}")
            return False
    
    def get_clusters_for_deletion(self, namespace: Optional[str] = None) -> List[Tuple[str, str, str]]:
        """
        Get all clusters that should be deleted based on criteria.
        
        Args:
            namespace: If specified, only examine clusters in this namespace
        
        Returns:
            List of tuples (cluster_name, cluster_namespace, reason) for clusters to be deleted
        """
        all_kommander_clusters = self.list_all_kommander_clusters(namespace)
        clusters_to_delete = []
        
        for kc in all_kommander_clusters:
            should_delete, reason = self.kommander_cluster_matches_criteria(kc)
            if should_delete:
                # Get the CAPI cluster reference
                cluster_name, cluster_namespace = self.get_capi_cluster_reference(kc)
                
                if cluster_name and cluster_namespace:
                    # Verify the CAPI cluster exists
                    if self.verify_capi_cluster_exists(cluster_name, cluster_namespace):
                        clusters_to_delete.append((cluster_name, cluster_namespace, reason))
                    else:
                        kc_name = kc.get("metadata", {}).get("name", "unknown")
                        print(f"{Fore.YELLOW}Warning: CAPI cluster {cluster_name} referenced by KommanderCluster {kc_name} not found{Style.RESET_ALL}")
                else:
                    kc_name = kc.get("metadata", {}).get("name", "unknown")
                    print(f"{Fore.YELLOW}Warning: KommanderCluster {kc_name} has no valid CAPI cluster reference{Style.RESET_ALL}")
        
        return clusters_to_delete
    
    def get_clusters_with_exclusions(self, namespace: Optional[str] = None) -> Tuple[List[Tuple[Dict, str]], List[Tuple[Dict, str]]]:
        """
        Get all clusters categorized into those for deletion and those excluded.
        
        Args:
            namespace: If specified, only examine clusters in this namespace
        
        Returns:
            Tuple of (clusters_to_delete, excluded_clusters) where each contains 
            (kommander_cluster_with_capi_info, reason) tuples
        """
        all_kommander_clusters = self.list_all_kommander_clusters(namespace)
        clusters_to_delete = []
        excluded_clusters = []
        
        for kc in all_kommander_clusters:
            should_delete, reason = self.kommander_cluster_matches_criteria(kc)
            
            # Get the CAPI cluster reference
            cluster_name, cluster_namespace = self.get_capi_cluster_reference(kc)
            
            # Create a combined object with both KommanderCluster and CAPI cluster info
            combined_info = {
                "kommander_cluster": kc,
                "capi_cluster_name": cluster_name,
                "capi_cluster_namespace": cluster_namespace,
                "labels": self.get_cluster_labels(kc)
            }
            
            if should_delete:
                if cluster_name and cluster_namespace:
                    # Verify the CAPI cluster exists
                    if self.verify_capi_cluster_exists(cluster_name, cluster_namespace):
                        clusters_to_delete.append((combined_info, reason))
                    else:
                        # CAPI cluster doesn't exist, exclude for safety
                        excluded_clusters.append((combined_info, f"Referenced CAPI cluster {cluster_name} not found"))
                else:
                    # No valid CAPI cluster reference, exclude for safety
                    excluded_clusters.append((combined_info, "No valid CAPI cluster reference"))
            else:
                excluded_clusters.append((combined_info, reason))
        
        return clusters_to_delete, excluded_clusters