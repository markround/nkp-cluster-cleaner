"""
Web server module for the NKP Cluster Cleaner web UI.
"""

import os
from flask import Flask, render_template, jsonify
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
        return jsonify({
            'status': 'ok', 
            'service': 'nkp-cluster-cleaner',
            'kubeconfig': app.config['KUBECONFIG_PATH'] or 'default',
            'config': app.config['CONFIG_PATH'] or 'none'
        })
    
    @app.route('/clusters')
    def clusters():
        """Placeholder for future cluster listing page."""
        return render_template(
            'base.html',
            header_title="Cluster Listing",
            header_subtitle="Coming Soon!"
        )
    
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
    print(f"🛑 Press Ctrl+C to stop the server")
    
    app.run(host=host, port=port, debug=debug)