"""
Web server module for the NKP Cluster Cleaner web UI.
"""

import os
import re
from datetime import datetime
from flask import Flask, render_template, jsonify, request
from typing import Optional
from .config import ConfigManager
from .cluster_manager import ClusterManager
from .cronjob_manager import CronJobManager
import nkp_cluster_cleaner

__version__ = nkp_cluster_cleaner.__version__

def create_app(kubeconfig_path: Optional[str] = None, config_path: Optional[str] = None, 
               url_prefix: Optional[str] = None) -> Flask:
    """
    Create and configure the Flask application.
    
    Args:
        kubeconfig_path: Path to kubeconfig file
        config_path: Path to configuration file
        url_prefix: URL prefix for all routes (e.g., '/foo')
        
    Returns:
        Flask application instance
    """
    # Get the directory where this file is located
    template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
    
    app = Flask(__name__, template_folder=template_dir)
    
    # Store configuration in app context
    app.config['KUBECONFIG_PATH'] = kubeconfig_path
    app.config['CONFIG_PATH'] = config_path
    
    # Normalize URL prefix
    if url_prefix:
        url_prefix = url_prefix.strip('/')
        if url_prefix:
            url_prefix = '/' + url_prefix
        else:
            url_prefix = ''
    else:
        url_prefix = ''
    
    app.config['URL_PREFIX'] = url_prefix
    
    # Create a template function for building URLs with prefix
    @app.template_global()
    def url_with_prefix(path):
        """Build URL with prefix."""
        if not path.startswith('/'):
            path = '/' + path
        return url_prefix + path
    
    def get_cluster_manager():
        """Helper to create cluster manager with current config."""
        config_manager = ConfigManager(app.config['CONFIG_PATH']) if app.config['CONFIG_PATH'] else ConfigManager()
        return ClusterManager(app.config['KUBECONFIG_PATH'], config_manager)
    
    def get_cronjob_manager():
        """Helper to create cronjob manager with current config."""
        return CronJobManager(app.config['KUBECONFIG_PATH'])

    @app.route(url_prefix + '/')
    def index():
        """Main page showing cluster information."""
        # Determine configuration status
        kubeconfig_status = app.config['KUBECONFIG_PATH'] or "Using default (~/.kube/config)"
        config_status = app.config['CONFIG_PATH'] or "Using default (no protection rules)"
        cluster_manager = get_cluster_manager()
        nkp_version = cluster_manager.get_nkp_version()

        return render_template(
            'index.html',
            kubeconfig_status=kubeconfig_status,
            config_status=config_status,
            version=__version__,
            nkp_version=nkp_version
        )
    
    @app.route(url_prefix + '/health')
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
                'version': __version__,
                'kubeconfig': app.config['KUBECONFIG_PATH'] or 'default',
                'config': app.config['CONFIG_PATH'] or 'none',
                'timestamp': datetime.now().isoformat()
            })
        except Exception as e:
            return jsonify({
                'status': 'error',
                'service': 'nkp-cluster-cleaner',
                'version': __version__,
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }), 500
    
    @app.route(url_prefix + '/clusters')
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
                version=__version__,
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
                version=__version__,
                refresh_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                error=str(e)
            )
    
    @app.route(url_prefix + '/rules')
    def rules():
        """Display deletion rules and configuration summary."""
        try:
            # Get cluster manager to access configuration
            cluster_manager = get_cluster_manager()
            config_manager = cluster_manager.config_manager
            criteria = config_manager.get_criteria()
            
            # Count various configuration elements
            protected_cluster_patterns = criteria.protected_cluster_patterns
            excluded_namespace_patterns = criteria.excluded_namespace_patterns
            
            # Calculate summary statistics
            rule_count = 4  # Core deletion rules (missing owner, missing expires, expired, invalid format)
            protected_cluster_count = len(protected_cluster_patterns)
            excluded_namespace_count = len(excluded_namespace_patterns)
            time_format_count = 4  # h, d, w, y
            
            # Determine configuration paths
            kubeconfig_path = app.config['KUBECONFIG_PATH']
            config_path = app.config['CONFIG_PATH']
            
            return render_template(
                'rules.html',
                # Summary statistics
                rule_count=rule_count,
                protected_cluster_count=protected_cluster_count,
                excluded_namespace_count=excluded_namespace_count,
                time_format_count=time_format_count,
                # Configuration details
                protected_cluster_patterns=protected_cluster_patterns,
                excluded_namespace_patterns=excluded_namespace_patterns,
                kubeconfig_path=kubeconfig_path,
                version=__version__,
                config_path=config_path
            )
        except Exception as e:
            # Render with error state
            return render_template(
                'rules.html',
                rule_count=4,
                protected_cluster_count=0,
                excluded_namespace_count=0,
                time_format_count=4,
                protected_cluster_patterns=[],
                excluded_namespace_patterns=[],
                kubeconfig_path=app.config['KUBECONFIG_PATH'],
                config_path=app.config['CONFIG_PATH'],
                version=__version__,
                error=str(e)
            )
    
    @app.route(url_prefix + '/api/clusters')
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
        
    @app.route(url_prefix + '/scheduled-tasks')
    def scheduled_tasks():
        """Display scheduled tasks (CronJobs) and recent executions."""
        try:
            # Get cronjob manager
            cronjob_manager = get_cronjob_manager()
            
            # Get  summary
            namespace = "kommander"  # Default namespace for NKP cronjobs
            summary = cronjob_manager.get_all_scheduled_tasks_summary(namespace)
            
            return render_template(
                'scheduled_tasks.html',
                summary=summary,
                namespace=namespace,
                refresh_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                version=__version__,
                error=None
            )
        except Exception as e:
            # Render error state
            return render_template(
                'scheduled_tasks.html',
                summary={
                    'total_cronjobs': 0,
                    'active_cronjobs': 0,
                    'suspended_cronjobs': 0,
                    'total_recent_jobs': 0,
                    'successful_jobs': 0,
                    'failed_jobs': 0,
                    'running_jobs': 0,
                    'cronjobs': [],
                    'recent_jobs': []
                },
                namespace="kommander",
                refresh_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                version=__version__,
                error=str(e)
            )

    @app.route(url_prefix + '/api/job-logs')
    def api_job_logs():
        """API endpoint to get logs for a specific job."""
        job_name = request.args.get('job_name')
        namespace = request.args.get('namespace', 'kommander')
        
        if not job_name:
            return jsonify({
                'status': 'error',
                'error': 'job_name parameter is required'
            }), 400
        
        try:
            cronjob_manager = get_cronjob_manager()
            
            # First validate that this job was created by one of our CronJobs
            try:
                job = cronjob_manager.batch_v1.read_namespaced_job(name=job_name, namespace=namespace)
                
                # Check if job was created by our CronJobs
                is_our_job = False
                if job.metadata.owner_references:
                    for owner in job.metadata.owner_references:
                        if owner.kind == "CronJob":
                            try:
                                cronjob = cronjob_manager.batch_v1.read_namespaced_cron_job(
                                    name=owner.name, 
                                    namespace=namespace
                                )
                                labels = cronjob.metadata.labels or {}
                                if labels.get("app") == "nkp-cluster-cleaner":
                                    is_our_job = True
                                    break
                            except:
                                continue
                
                if not is_our_job:
                    return jsonify({
                        'status': 'error',
                        'error': 'Access denied: Job was not created by nkp-cluster-cleaner'
                    }), 403
                    
            except Exception as e:
                return jsonify({
                    'status': 'error',
                    'error': f'Job not found or access denied: {e}'
                }), 404
            
            # Get pods for the job
            pods = cronjob_manager.get_job_pods(job_name, namespace)
            
            logs_data = []
            for pod in pods:
                # Get logs for each container in each pod
                if pod['container_statuses']:
                    for container in pod['container_statuses']:
                        logs = cronjob_manager.get_pod_logs(
                            pod['name'], 
                            container['name'], 
                            namespace,
                            tail_lines=200,
                            job_name=job_name
                        )
                        logs_data.append({
                            'pod_name': pod['name'],
                            'container_name': container['name'],
                            'logs': logs
                        })
                else:
                    # Single container pod
                    logs = cronjob_manager.get_pod_logs(
                        pod['name'], 
                        None, 
                        namespace,
                        tail_lines=200,
                        job_name=job_name
                    )
                    logs_data.append({
                        'pod_name': pod['name'],
                        'container_name': 'default',
                        'logs': logs
                    })
            
            return jsonify({
                'status': 'success',
                'job_name': job_name,
                'namespace': namespace,
                'logs': logs_data,
                'timestamp': datetime.now().isoformat()
            })
            
        except Exception as e:
            return jsonify({
                'status': 'error',
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }), 500

    @app.route(url_prefix + '/api/scheduled-tasks')
    def api_scheduled_tasks():
        """API endpoint for scheduled tasks data (JSON)."""
        try:
            cronjob_manager = get_cronjob_manager()
            namespace = "kommander"
            summary = cronjob_manager.get_all_scheduled_tasks_summary(namespace)
            
            # Convert datetime objects to ISO format for JSON serialization
            def serialize_datetime(obj):
                if hasattr(obj, 'isoformat'):
                    return obj.isoformat()
                return obj
            
            # Serialize cronjobs
            serialized_cronjobs = []
            for cj in summary['cronjobs']:
                serialized_cj = {}
                for key, value in cj.items():
                    serialized_cj[key] = serialize_datetime(value)
                serialized_cronjobs.append(serialized_cj)
            
            # Serialize recent jobs
            serialized_jobs = []
            for job in summary['recent_jobs']:
                serialized_job = {}
                for key, value in job.items():
                    serialized_job[key] = serialize_datetime(value)
                serialized_jobs.append(serialized_job)
            
            return jsonify({
                'status': 'success',
                'data': {
                    'summary': {
                        'total_cronjobs': summary['total_cronjobs'],
                        'active_cronjobs': summary['active_cronjobs'],
                        'suspended_cronjobs': summary['suspended_cronjobs'],
                        'total_recent_jobs': summary['total_recent_jobs'],
                        'successful_jobs': summary['successful_jobs'],
                        'failed_jobs': summary['failed_jobs'],
                        'running_jobs': summary['running_jobs']
                    },
                    'cronjobs': serialized_cronjobs,
                    'recent_jobs': serialized_jobs,
                    'namespace': namespace
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
               kubeconfig_path: Optional[str] = None, config_path: Optional[str] = None,
               url_prefix: Optional[str] = None):
    """
    Run the Flask development server.
    
    Args:
        host: Host to bind to
        port: Port to bind to
        debug: Enable debug mode
        kubeconfig_path: Path to kubeconfig file
        config_path: Path to configuration file
        url_prefix: URL prefix for all routes
    """
    app = create_app(kubeconfig_path, config_path, url_prefix)
    
    # Normalize prefix for display
    display_prefix = url_prefix if url_prefix else ""
    
    print(f"🚀 Starting NKP Cluster Cleaner web server...")
    print(f"📡 Server URL: http://{host}:{port}{display_prefix}")
    print(f"🔧 Debug mode: {'Enabled' if debug else 'Disabled'}")
    print(f"📋 Configuration: kubeconfig={kubeconfig_path or 'default'}, config={config_path or 'none'}")
    if url_prefix:
        print(f"🔗 URL prefix: {url_prefix}")
    print(f"🔗 Available endpoints:")
    print(f"   • http://{host}:{port}{display_prefix}/ - Dashboard")
    print(f"   • http://{host}:{port}{display_prefix}/clusters - Cluster listing")
    print(f"   • http://{host}:{port}{display_prefix}/rules - Deletion rules")
    print(f"   • http://{host}:{port}{display_prefix}/health - Health check")
    print(f"   • http://{host}:{port}{display_prefix}/api/clusters - API endpoint")
    print(f"🛑 Press Ctrl+C to stop the server")
    
    app.run(host=host, port=port, debug=debug)