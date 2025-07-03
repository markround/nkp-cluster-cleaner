"""
Configuration management for cluster deletion criteria.
"""

import yaml
import re
from typing import Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class DeletionCriteria:
    """Complete configuration for cluster deletion criteria."""
    
    # Clusters to always exclude (regex patterns for cluster names)
    protected_cluster_patterns: List[str] = field(default_factory=list)
    
    # Namespaces to exclude (regex patterns for namespace names)
    excluded_namespace_patterns: List[str] = field(default_factory=list)


class ConfigManager:
    """Manages configuration for cluster deletion criteria."""
    
    def __init__(self, config_file: Optional[str] = None):
        """
        Initialize config manager.
        
        Args:
            config_file: Path to YAML configuration file
        """
        self.config_file = config_file
        self.criteria = DeletionCriteria()
        
        if config_file:
            self.load_config(config_file)
    
    def load_config(self, config_file: str):
        """
        Load configuration from YAML file.
        
        Args:
            config_file: Path to YAML configuration file
        """
        try:
            with open(config_file, 'r') as f:
                config_data = yaml.safe_load(f)
            
            self.criteria = self._parse_config(config_data)
        except Exception as e:
            raise Exception(f"Failed to load config file {config_file}: {e}")
    
    def _parse_config(self, config_data: Dict) -> DeletionCriteria:
        """
        Parse configuration data into DeletionCriteria object.
        
        Args:
            config_data: Dictionary from YAML file
            
        Returns:
            DeletionCriteria object
        """
        criteria = DeletionCriteria()
        
        # Parse protection patterns
        criteria.protected_cluster_patterns = config_data.get("protected_cluster_patterns", [])
        criteria.excluded_namespace_patterns = config_data.get("excluded_namespace_patterns", [])
        
        return criteria
    
    def get_criteria(self) -> DeletionCriteria:
        """
        Get current deletion criteria.
        
        Returns:
            DeletionCriteria object
        """
        return self.criteria
    
    def save_example_config(self, output_file: str):
        """
        Save an example configuration file.
        
        Args:
            output_file: Path where to save the example config
        """
        example_config = {
            "protected_cluster_patterns": [
                "^production-.*",
                ".*-prod$",
                "critical-.*"
            ],
            "excluded_namespace_patterns": [
                "^default$",
                ".*-prod$"
            ]
        }
        
        with open(output_file, 'w') as f:
            yaml.dump(example_config, f, default_flow_style=False, indent=2)
        
        print(f"Example configuration saved to {output_file}")
    
    def is_cluster_protected(self, cluster_name: str, namespace: str) -> bool:
        """
        Check if a cluster is protected from deletion.
        
        Args:
            cluster_name: Name of the cluster
            namespace: Namespace of the cluster
            
        Returns:
            True if cluster should be protected from deletion
        """
        # Check if cluster name matches any protection pattern
        for pattern in self.criteria.protected_cluster_patterns:
            if re.match(pattern, cluster_name):
                return True
        
        # Check if namespace matches any exclusion pattern
        for pattern in self.criteria.excluded_namespace_patterns:
            if re.match(pattern, namespace):
                return True
        
        return False