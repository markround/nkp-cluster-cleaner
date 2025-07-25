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
from .redis_analytics_service import RedisAnalyticsService
from .prometheus_metrics_service import PrometheusMetricsService
import nkp_cluster_cleaner

__version__ = nkp_cluster_cleaner.__version__

def create_app(kubeconfig_path: Optional[str] = None, config_path: Optional[str] = None, 
               url_prefix: Optional[str] = None, redis_host: str = 'redis', 
               redis_port: int = 6379, redis_db: int = 0, 
               no_analytics: bool = False) -> Flask:
    """
    Create and configure the Flask application.
    
    Args:
        kubeconfig_path: Path to kubeconfig file
        config_path: Path to configuration file
        url_prefix: URL prefix for all routes (e.g., '/foo')
        redis_host: Redis host for analytics data
        redis_port: Redis port
        redis_db: Redis database number
        no_analytics: Disable analytics and Redis connections
        
    Returns:
        Flask application instance
    """
    # Get the directory where this file is located
    template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
    
    app = Flask(__name__, template_folder=template_dir)
    
    # Store configuration in app context
    app.config['KUBECONFIG_PATH'] = kubeconfig_path
    app.config['CONFIG_PATH'] = config_path
    app.config['REDIS_HOST'] = redis_host
    app.config['REDIS_PORT'] = redis_port
    app.config['REDIS_DB'] = redis_db
    app.config['NO_ANALYTICS'] = no_analytics
    
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
    
    #
    # Helpers
    #
    def get_cluster_manager():
        """Helper to create cluster manager with current config."""
        config_manager = ConfigManager(app.config['CONFIG_PATH']) if app.config['CONFIG_PATH'] else ConfigManager()
        return ClusterManager(app.config['KUBECONFIG_PATH'], config_manager)
    
    def get_cronjob_manager():
        """Helper to create cronjob manager with current config."""
        return CronJobManager(app.config['KUBECONFIG_PATH'])
    
    def get_analytics_service():
        """Helper to create analytics service with current config."""
        return RedisAnalyticsService(
            kubeconfig_path=app.config['KUBECONFIG_PATH'],
            redis_host=app.config['REDIS_HOST'],
            redis_port=app.config['REDIS_PORT'],
            redis_db=app.config['REDIS_DB']
        )


    #
    # Routes
    #
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
            no_analytics=app.config['NO_ANALYTICS'],
            kubeconfig_status=kubeconfig_status,
            config_status=config_status,
            version=__version__,
            nkp_version=nkp_version,
        )
    
    @app.route(url_prefix + '/health')
    def health():
        """Health check endpoint."""
        try:
            # Try to create cluster manager to test connectivity
            cluster_manager = get_cluster_manager()
            # Simple connectivity test
            cluster_manager.check_kommander_crds()
              
            health_data = {
                'status': 'ok', 
                'service': 'nkp-cluster-cleaner',
                'version': __version__,
                'kubeconfig': app.config['KUBECONFIG_PATH'] or 'default',
                'config': app.config['CONFIG_PATH'] or 'none',
                'timestamp': datetime.now().isoformat()
            }
            
            if not app.config['NO_ANALYTICS']:
                health_data['redis'] = f"{app.config['REDIS_HOST']}:{app.config['REDIS_PORT']}"
                # Test Redis connection
                analytics_service = get_analytics_service()
                redis_stats = analytics_service.get_database_stats()

                if 'error' not in redis_stats:
                    health_data['redis_status'] = 'connected'
                    health_data['redis_snapshots'] = redis_stats.get('total_snapshots', 0)
                else:
                    health_data['redis_status'] = 'error'
                    health_data['redis_error'] = redis_stats['error']
            
            return jsonify(health_data)
        
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
                no_analytics=app.config['NO_ANALYTICS'],
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
                no_analytics=app.config['NO_ANALYTICS'],
                clusters_to_delete=[],
                excluded_clusters=[],
                kubeconfig_status=app.config['KUBECONFIG_PATH'] or "default",
                config_status=app.config['CONFIG_PATH'] or "none",
                namespace_filter=namespace_filter,
                version=__version__,
                refresh_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                error=str(e)
            )
    
    @app.route(url_prefix + '/analytics')
    def analytics():
        """Analytics dashboard page."""

        if app.config['NO_ANALYTICS']:
            return render_template(
                'analytics.html',
                no_analytics=app.config['NO_ANALYTICS'],
                error="Analytics features have been disabled.",
                version=__version__,
                refresh_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )

        try:
            analytics_service = get_analytics_service()
            
            # Get analytics data for different time periods
            cluster_trends_7d = analytics_service.get_cluster_trends(7)
            cluster_trends_30d = analytics_service.get_cluster_trends(30)
            deletion_activity = analytics_service.get_deletion_activity(14)
            compliance_stats = analytics_service.get_compliance_stats(30)
            namespace_activity = analytics_service.get_namespace_activity(30)
            owner_distribution = analytics_service.get_owner_distribution(30)
            expiration_analysis = analytics_service.get_expiration_analysis(30)
            dashboard_summary = analytics_service.get_dashboard_summary()
            
            return render_template(
                'analytics.html',
                no_analytics=app.config['NO_ANALYTICS'],
                cluster_trends_7d=cluster_trends_7d,
                cluster_trends_30d=cluster_trends_30d,
                deletion_activity=deletion_activity,
                compliance_stats=compliance_stats,
                namespace_activity=namespace_activity,
                owner_distribution=owner_distribution,
                expiration_analysis=expiration_analysis,
                dashboard_summary=dashboard_summary,
                version=__version__,
                refresh_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                error=None
            )
        except Exception as e:
            return render_template(
                'analytics.html',
                no_analytics=app.config['NO_ANALYTICS'],
                error=str(e),
                version=__version__,
                refresh_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
            extra_labels = criteria.extra_labels
            
            # Calculate summary statistics
            rule_count = 4  # Core deletion rules (missing expires, missing extra labels, expired, invalid format)
            protected_cluster_count = len(protected_cluster_patterns)
            excluded_namespace_count = len(excluded_namespace_patterns)
            extra_labels_count = len(extra_labels)
            time_format_count = 4  # h, d, w, y
            
            # Determine configuration paths
            kubeconfig_path = app.config['KUBECONFIG_PATH']
            config_path = app.config['CONFIG_PATH']
            
            return render_template(
                'rules.html',
                no_analytics=app.config['NO_ANALYTICS'],
                # Summary statistics
                rule_count=rule_count,
                protected_cluster_count=protected_cluster_count,
                excluded_namespace_count=excluded_namespace_count,
                extra_labels_count=extra_labels_count,
                time_format_count=time_format_count,
                # Configuration details
                protected_cluster_patterns=protected_cluster_patterns,
                excluded_namespace_patterns=excluded_namespace_patterns,
                extra_labels=extra_labels,
                kubeconfig_path=kubeconfig_path,
                version=__version__,
                config_path=config_path
            )
        except Exception as e:
            # Render with error state
            return render_template(
                'rules.html',
                no_analytics=app.config['NO_ANALYTICS'],
                rule_count=4,
                protected_cluster_count=0,
                excluded_namespace_count=0,
                extra_labels_count=0,
                time_format_count=4,
                protected_cluster_patterns=[],
                excluded_namespace_patterns=[],
                extra_labels=[],
                kubeconfig_path=app.config['KUBECONFIG_PATH'],
                config_path=app.config['CONFIG_PATH'],
                version=__version__,
                error=str(e)
            )
    
    @app.route(url_prefix + '/scheduled-tasks')
    def scheduled_tasks():
        """Display scheduled tasks (CronJobs) and recent executions."""

        try:
            # Get cronjob manager
            cronjob_manager = get_cronjob_manager()
            
            # Get summary
            namespace = "kommander"  # Default namespace for NKP cronjobs
            summary = cronjob_manager.get_all_scheduled_tasks_summary(namespace)
            
            return render_template(
                'scheduled_tasks.html',
                no_analytics=app.config['NO_ANALYTICS'],
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
                no_analytics=app.config['NO_ANALYTICS'],
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
    
    @app.route(url_prefix + '/metrics')
    def metrics():
        """Prometheus metrics endpoint."""
        try:
            if app.config['NO_ANALYTICS']:
                # Create metrics service without analytics
                metrics_service = PrometheusMetricsService()
            else:
                # Create metrics service with analytics
                analytics_service = get_analytics_service()
                metrics_service = PrometheusMetricsService(analytics_service)
            
            metrics_output = metrics_service.generate_metrics()
            return metrics_output, 200, {'Content-Type': 'text/plain; charset=utf-8'}
            
        except Exception as e:
            # Fallback to basic metrics on any error
            metrics_service = PrometheusMetricsService()
            metrics_output = metrics_service._generate_error_metrics(str(e))
            return metrics_output, 200, {'Content-Type': 'text/plain; charset=utf-8'}
    
    @app.route(url_prefix + '/notifications')
    def notifications():
        """Display currently active notifications."""
        if app.config['NO_ANALYTICS']:
            return render_template(
                'notifications.html',
                no_analytics=app.config['NO_ANALYTICS'],
                error="Notifications feature requires Redis/analytics to be enabled.",
                version=__version__,
                refresh_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                critical_count=0,
                warning_count=0,
                total_count=0
            )

        try:
            from .notification_manager import NotificationManager
            from .notification_history import NotificationHistory
            
            # Initialize managers
            config_manager = ConfigManager(app.config['CONFIG_PATH']) if app.config['CONFIG_PATH'] else ConfigManager()
            notification_manager = NotificationManager(app.config['KUBECONFIG_PATH'], config_manager)
            notification_history = NotificationHistory(
                app.config['REDIS_HOST'], 
                app.config['REDIS_PORT'], 
                app.config['REDIS_DB']
            )
            
            # Default thresholds (these could be made configurable)
            warning_threshold = 80
            critical_threshold = 95
            
            # Get clusters requiring notifications
            critical_clusters, warning_clusters = notification_manager.get_clusters_for_notification(
                warning_threshold, critical_threshold
            )
            
            # Format notification data
            critical_notifications = []
            for cluster_info, elapsed_percentage, expiry_time in critical_clusters:
                notification_data = notification_manager.get_cluster_notification_data(
                    cluster_info, elapsed_percentage, expiry_time
                )
                critical_notifications.append(notification_data)
            
            warning_notifications = []
            for cluster_info, elapsed_percentage, expiry_time in warning_clusters:
                notification_data = notification_manager.get_cluster_notification_data(
                    cluster_info, elapsed_percentage, expiry_time
                )
                warning_notifications.append(notification_data)
            
            # Get notification statistics from Redis
            notification_stats = {
                'total_tracked': len(critical_clusters) + len(warning_clusters),
                'active_keys': notification_history.get_active_notification_count()
            }
            
            return render_template(
                'notifications.html',
                no_analytics=app.config['NO_ANALYTICS'],
                critical_notifications=critical_notifications,
                warning_notifications=warning_notifications,
                critical_count=len(critical_notifications),
                warning_count=len(warning_notifications),
                total_count=len(critical_notifications) + len(warning_notifications),
                warning_threshold=warning_threshold,
                critical_threshold=critical_threshold,
                notification_stats=notification_stats,
                version=__version__,
                refresh_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                error=None
            )
            
        except Exception as e:
            return render_template(
                'notifications.html',
                no_analytics=app.config['NO_ANALYTICS'],
                error=str(e),
                version=__version__,
                refresh_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                critical_count=0,
                warning_count=0,
                total_count=0
            )
    #
    # End of routes
    #
    return app

def run_server(host: str = '127.0.0.1', port: int = 8080, debug: bool = False,
               kubeconfig_path: Optional[str] = None, config_path: Optional[str] = None,
               url_prefix: Optional[str] = None, redis_host: str = 'redis', 
               redis_port: int = 6379, redis_db: int = 0, no_analytics: bool = False):
    """
    Run the Flask development server.
    
    Args:
        host: Host to bind to
        port: Port to bind to
        debug: Enable debug mode
        kubeconfig_path: Path to kubeconfig file
        config_path: Path to configuration file
        url_prefix: URL prefix for all routes
        redis_host: Redis host for analytics data
        redis_port: Redis port
        redis_db: Redis database number
        no_analytics: Disable analytics and Redis connections
    """
    app = create_app(kubeconfig_path, config_path, url_prefix, redis_host, redis_port, redis_db, no_analytics)
    
    # Normalize prefix for display
    display_prefix = url_prefix if url_prefix else ""
    
    print(f"🚀 Starting NKP Cluster Cleaner web server...")
    print(f"📡 Server URL: http://{host}:{port}{display_prefix}")
    print(f"🔧 Debug mode: {'Enabled' if debug else 'Disabled'}")
    print(f"📋 Configuration: kubeconfig={kubeconfig_path or 'default'}, config={config_path or 'none'}")
    if not no_analytics:
        print(f"📊 Analytics storage: Redis at {redis_host}:{redis_port} (db {redis_db})")
    if url_prefix:
        print(f"🔗 URL prefix: {url_prefix}")
    print(f"🔗 Available endpoints:")
    print(f"   • http://{host}:{port}{display_prefix}/ - Dashboard")
    print(f"   • http://{host}:{port}{display_prefix}/clusters - Cluster listing")
    print(f"   • http://{host}:{port}{display_prefix}/rules - Deletion rules")
    if not no_analytics:
        print(f"   • http://{host}:{port}{display_prefix}/analytics - Analytics dashboard")
        print(f"   • http://{host}:{port}{display_prefix}/notifications - Active notifications")
    print(f"   • http://{host}:{port}{display_prefix}/metrics - Prometheus metrics")    
    print(f"   • http://{host}:{port}{display_prefix}/scheduled-tasks - CronJob status")
    print(f"   • http://{host}:{port}{display_prefix}/health - Health check")
    print(f"🛑 Press Ctrl+C to stop the server")
    
    app.run(host=host, port=port, debug=debug)