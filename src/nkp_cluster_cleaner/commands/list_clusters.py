"""
List clusters command implementation for the NKP Cluster Cleaner tool.
"""

import click
from colorama import Fore, Style
from tabulate import tabulate
from typing import Optional
from ..cluster_manager import ClusterManager
from ..config import ConfigManager


def execute_list_clusters_command(kubeconfig: Optional[str], config: Optional[str], 
                                 namespace: Optional[str], no_exclusions: bool):
    """
    Execute the list-clusters command with the given parameters.
    
    Args:
        kubeconfig: Path to kubeconfig file
        config: Path to configuration file
        namespace: Namespace to limit operation to
        no_exclusions: Skip showing excluded clusters
    """
    if namespace:
        click.echo(f"{Fore.BLUE}Listing CAPI clusters for deletion in namespace '{namespace}'...{Style.RESET_ALL}")
    else:
        click.echo(f"{Fore.BLUE}Listing CAPI clusters for deletion across all namespaces...{Style.RESET_ALL}")
    
    try:
        # Initialize configuration and cluster manager
        config_manager = ConfigManager(config) if config else ConfigManager()
        cluster_manager = ClusterManager(kubeconfig, config_manager)
        
        # Get all clusters and categorize them
        clusters_to_delete, excluded_clusters = cluster_manager.get_clusters_with_exclusions(namespace)
        
        # Display clusters for deletion
        if clusters_to_delete:
            table_data = []
            for cluster_info, reason in clusters_to_delete:
                capi_cluster_name = cluster_info.get("capi_cluster_name", "unknown")
                capi_cluster_namespace = cluster_info.get("capi_cluster_namespace", "unknown")
                labels = cluster_info.get("labels", {})
                owner = labels.get("owner", "N/A")
                expires = labels.get("expires", "N/A")
                
                table_data.append([
                    capi_cluster_name,
                    capi_cluster_namespace,
                    owner,
                    expires,
                    reason
                ])
            
            headers = ["Cluster Name", "Namespace", "Owner", "Expires", "Reason"]
            click.echo(f"\n{Fore.RED}Found {len(clusters_to_delete)} clusters for deletion:{Style.RESET_ALL}")
            click.echo(tabulate(table_data, headers=headers, tablefmt="grid"))
        else:
            click.echo(f"\n{Fore.GREEN}No clusters found matching deletion criteria.{Style.RESET_ALL}")
        
        # Display excluded clusters if not suppressed
        if not no_exclusions and excluded_clusters:
            excluded_table_data = []
            for cluster_info, reason in excluded_clusters:
                capi_cluster_name = cluster_info.get("capi_cluster_name", "unknown")
                capi_cluster_namespace = cluster_info.get("capi_cluster_namespace", "unknown")
                labels = cluster_info.get("labels", {})
                owner = labels.get("owner", "N/A")
                expires = labels.get("expires", "N/A")
                
                excluded_table_data.append([
                    capi_cluster_name,
                    capi_cluster_namespace,
                    owner,
                    expires,
                    reason
                ])
            
            excluded_headers = ["Cluster Name", "Namespace", "Owner", "Expires", "Exclusion Reason"]
            click.echo(f"\n{Fore.CYAN}Found {len(excluded_clusters)} excluded clusters:{Style.RESET_ALL}")
            click.echo(tabulate(excluded_table_data, headers=excluded_headers, tablefmt="grid"))
        
    except Exception as e:
        click.echo(f"{Fore.RED}Error: {e}{Style.RESET_ALL}")
        raise click.Abort()