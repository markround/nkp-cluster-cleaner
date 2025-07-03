#!/usr/bin/env python3
"""
Main entry point for the NKP Cluster Cleaner tool.
"""

import click
from colorama import init, Fore, Style
from tabulate import tabulate
from .cluster_manager import ClusterManager
from .config import ConfigManager

# Initialize colorama for cross-platform colored output
init()

@click.group()
@click.version_option()
def cli():
    """NKP Cluster Cleaner - Delete CAPI clusters based on label criteria."""
    pass

@cli.command()
@click.option(
    '--kubeconfig',
    type=click.Path(exists=True),
    help='Path to kubeconfig file (default: ~/.kube/config or $KUBECONFIG)'
)
@click.option(
    '--config',
    type=click.Path(exists=True),
    help='Path to configuration file for protection rules'
)
@click.option(
    '--namespace',
    help='Limit operation to specific namespace (default: examine all namespaces)'
)
def list_clusters(kubeconfig, config, namespace):
    """List CAPI clusters that match deletion criteria."""
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
        
        # Display excluded clusters
        if excluded_clusters:
            excluded_table_data = []
            for cluster_info, reason in excluded_clusters:
                capi_cluster_name = cluster_info.get("capi_cluster_name", "N/A")
                capi_cluster_namespace = cluster_info.get("capi_cluster_namespace", "N/A")
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
            
            headers = ["Cluster Name", "Namespace", "Owner", "Expires", "Exclusion Reason"]
            click.echo(f"\n{Fore.GREEN}Found {len(excluded_clusters)} clusters excluded from deletion:{Style.RESET_ALL}")
            click.echo(tabulate(excluded_table_data, headers=headers, tablefmt="grid"))
        else:
            click.echo(f"\n{Fore.CYAN}No clusters were excluded from deletion.{Style.RESET_ALL}")
        
    except Exception as e:
        click.echo(f"{Fore.RED}Error: {e}{Style.RESET_ALL}")
        raise click.Abort()

@cli.command()
@click.option(
    '--kubeconfig',
    type=click.Path(exists=True),
    help='Path to kubeconfig file (default: ~/.kube/config or $KUBECONFIG)'
)
@click.option(
    '--config',
    type=click.Path(exists=True),
    help='Path to configuration file for protection rules'
)
@click.option(
    '--dry-run',
    is_flag=True,
    help='Show what would be deleted without actually deleting'
)
@click.option(
    '--confirm',
    is_flag=True,
    help='Skip confirmation prompts (use with caution!)'
)
def delete_clusters(kubeconfig, config, dry_run, confirm):
    """Delete CAPI clusters that match deletion criteria."""
    if dry_run:
        click.echo(f"{Fore.YELLOW}[DRY RUN] Simulating cluster deletion...{Style.RESET_ALL}")
    else:
        click.echo(f"{Fore.RED}Deleting CAPI clusters...{Style.RESET_ALL}")
    
    try:
        # Initialize configuration and cluster manager
        config_manager = ConfigManager(config) if config else ConfigManager()
        cluster_manager = ClusterManager(kubeconfig, config_manager)
        
        # Get clusters that match deletion criteria
        clusters_to_delete = cluster_manager.get_clusters_for_deletion()
        
        if not clusters_to_delete:
            click.echo(f"{Fore.GREEN}No clusters found matching deletion criteria.{Style.RESET_ALL}")
            return
        
        # Show what will be deleted
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
        click.echo(f"\n{Fore.YELLOW}Found {len(clusters_to_delete)} clusters for deletion:{Style.RESET_ALL}")
        click.echo(tabulate(table_data, headers=headers, tablefmt="grid"))
        
        # Confirmation prompt (unless --confirm is used or dry-run)
        if not dry_run and not confirm:
            click.echo(f"\n{Fore.RED}WARNING: This will permanently delete the clusters listed above!{Style.RESET_ALL}")
            if not click.confirm("Are you sure you want to proceed?"):
                click.echo("Deletion cancelled.")
                return
        
        # Delete clusters
        deleted_count = 0
        failed_count = 0
        
        for cluster_info, reason in clusters_to_delete:
            capi_cluster_name = cluster_info.get("capi_cluster_name", "unknown")
            capi_cluster_namespace = cluster_info.get("capi_cluster_namespace", "unknown")
            
            if dry_run:
                click.echo(f"{Fore.YELLOW}[DRY RUN] Would delete: {capi_cluster_name} in {capi_cluster_namespace} ({reason}){Style.RESET_ALL}")
                deleted_count += 1
            else:
                if cluster_manager.delete_cluster(capi_cluster_name, capi_cluster_namespace, dry_run):
                    deleted_count += 1
                else:
                    failed_count += 1
        
        # Summary
        if dry_run:
            click.echo(f"\n{Fore.CYAN}Dry run completed. {deleted_count} clusters would be deleted.{Style.RESET_ALL}")
        else:
            click.echo(f"\n{Fore.GREEN}Deletion completed. {deleted_count} clusters deleted successfully.{Style.RESET_ALL}")
            if failed_count > 0:
                click.echo(f"{Fore.RED}{failed_count} clusters failed to delete.{Style.RESET_ALL}")
        
    except Exception as e:
        click.echo(f"{Fore.RED}Error: {e}{Style.RESET_ALL}")
        raise click.Abort()

@cli.command()
@click.argument('output_file', type=click.Path())
def generate_config(output_file):
    """Generate an example configuration file."""
    config_manager = ConfigManager()
    config_manager.save_example_config(output_file)
    click.echo(f"{Fore.GREEN}Example configuration saved to {output_file}{Style.RESET_ALL}")

if __name__ == '__main__':
    cli()