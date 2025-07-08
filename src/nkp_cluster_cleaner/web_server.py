"""
Web server module for the NKP Cluster Cleaner web UI.
"""

import os
from datetime import datetime
from flask import Flask, render_template, jsonify, request
from typing import Optional
from .config import ConfigManager
from .cluster_manager import ClusterManager


def create_app(kubeconfig_path: Optional[str] = None, config_path: Optional[str] = None) -> Flask:
    """
    Create and configure the Flask application.
    
    Args:
        kubeconfig_path: Path to kubeconfig file
        config_path: Path to configuration file
        
    Returns:
        Flask application instance
    """
    # Get the directory where this file is located
    template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
    
    app = Flask(__name__, template_folder=template_dir)
    
    # Store configuration in app context
    app.config['KUBECONFIG_PATH'] = kubeconfig_path
    app.config['CONFIG_PATH'] = config_path
    
    def get_cluster_manager():
        """Helper to create cluster manager with current config."""
        config_manager = ConfigManager(app.config['CONFIG_PATH']) if app.config['CONFIG_PATH'] else ConfigManager()
        return ClusterManager(app.config['KUBECONFIG_PATH'], config_manager)
    
    @app.route('/')
    def index():
        """Main page showing cluster information."""
        # Determine configuration status
        kubeconfig_status = app.config['KUBECONFIG_PATH'] or "Using default (~/.kube/config)"
        config_status = app.config['CONFIG_PATH'] or "Using default (no protection rules)"
        
        return render_template(
            'index.html',
            kubeconfig_status=kubeconfig_status,
            config_status=config_status
        )
    
    @app.route('/health')
    def health():
        """Health check endpoint."""
        try:
            # Try to create cluster manager to test connectivity
            cluster_manager = get_cluster_manager()
            # Simple connectivity test
            cluster_manager.check_kommander_crds()
            
            return jsonify({
                'status': 'ok', 
                'service': 'nkp-cluster-cleaner',
                'kubeconfig': app.config['KUBECONFIG_PATH'] or 'default',
                'config': app.config['CONFIG_PATH'] or 'none',
                'timestamp': datetime.now().isoformat()
            })
        except Exception as e:
            return jsonify({
                'status': 'error',
                'service': 'nkp-cluster-cleaner',
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }), 500
    
    @app.route('/clusters')
    def clusters():
        """Display clusters that match deletion criteria."""
        namespace_filter = request.args.get('namespace')
        
        try:
            # Get cluster manager
            cluster_manager = get_cluster_manager()
            
            # Get clusters with exclusions
            clusters_to_delete, excluded_clusters = cluster_manager.get_clusters_with_exclusions(namespace_filter)
            
            # Determine configuration status
            kubeconfig_status = app.config['KUBECONFIG_PATH'] or "default"
            config_status = app.config['CONFIG_PATH'] or "none"
            
            return render_template(
                'clusters.html',
                clusters_to_delete=clusters_to_delete,
                excluded_clusters=excluded_clusters,
                kubeconfig_status=kubeconfig_status,
                config_status=config_status,
                namespace_filter=namespace_filter,
                refresh_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                error=None
            )
        except Exception as e:
            # Render error state
            return render_template(
                'clusters.html',
                clusters_to_delete=[],
                excluded_clusters=[],
                kubeconfig_status=app.config['KUBECONFIG_PATH'] or "default",
                config_status=app.config['CONFIG_PATH'] or "none",
                namespace_filter=namespace_filter,
                refresh_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                error=str(e)
            )
    
    @app.route('/api/clusters')
    def api_clusters():
        """API endpoint for cluster data (JSON)."""
        namespace_filter = request.args.get('namespace')
        
        try:
            # Get cluster manager
            cluster_manager = get_cluster_manager()
            
            # Get clusters with exclusions
            clusters_to_delete, excluded_clusters = cluster_manager.get_clusters_with_exclusions(namespace_filter)
            
            # Convert to JSON-serializable format
            def serialize_cluster_data(cluster_list):
                result = []
                for cluster_info, reason in cluster_list:
                    result.append({
                        'capi_cluster_name': cluster_info.get('capi_cluster_name'),
                        'capi_cluster_namespace': cluster_info.get('capi_cluster_namespace'),
                        'labels': cluster_info.get('labels', {}),
                        'reason': reason
                    })
                return result
            
            return jsonify({
                'status': 'success',
                'data': {
                    'clusters_to_delete': serialize_cluster_data(clusters_to_delete),
                    'excluded_clusters': serialize_cluster_data(excluded_clusters),
                    'namespace_filter': namespace_filter,
                    'total_for_deletion': len(clusters_to_delete),
                    'total_excluded': len(excluded_clusters)
                },
                'timestamp': datetime.now().isoformat()
            })
        except Exception as e:
            return jsonify({
                'status': 'error',
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }), 500
    
    return app


def run_server(host: str = '127.0.0.1', port: int = 8080, debug: bool = False,
               kubeconfig_path: Optional[str] = None, config_path: Optional[str] = None):
    """
    Run the Flask development server.
    
    Args:
        host: Host to bind to
        port: Port to bind to
        debug: Enable debug mode
        kubeconfig_path: Path to kubeconfig file
        config_path: Path to configuration file
    """
    app = create_app(kubeconfig_path, config_path)
    print(f"🚀 Starting NKP Cluster Cleaner web server...")
    print(f"📡 Server URL: http://{host}:{port}")
    print(f"🔧 Debug mode: {'Enabled' if debug else 'Disabled'}")
    print(f"📋 Configuration: kubeconfig={kubeconfig_path or 'default'}, config={config_path or 'none'}")
    print(f"🔗 Available endpoints:")
    print(f"   • http://{host}:{port}/ - Dashboard")
    print(f"   • http://{host}:{port}/clusters - Cluster listing")
    print(f"   • http://{host}:{port}/health - Health check")
    print(f"   • http://{host}:{port}/api/clusters - API endpoint")
    print(f"🛑 Press Ctrl+C to stop the server")
    
    app.run(host=host, port=port, debug=debug)