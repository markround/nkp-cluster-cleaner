"""
Flask application factory.

Routes live in the blueprints; this module only assembles them. The URL prefix
is applied once at registration rather than being concatenated onto every
individual route, which is how it used to be done.
"""

from __future__ import annotations

import logging
from datetime import datetime

from flask import Blueprint, Flask, jsonify

import nkp_cluster_cleaner

from ..core.settings import RedisSettings
from .blueprints import api, pages
from .services import Services, WebSettings, services, settings

logger = logging.getLogger(__name__)

__version__ = nkp_cluster_cleaner.__version__

health_bp = Blueprint("health", __name__)


@health_bp.route("/health")
def health():
    """
    Liveness and connectivity check.

    Reports 200 only if the Kubernetes API answers, and includes the detected
    deletion API so an operator can confirm which path is live without reading
    the logs.
    """
    config = settings()
    try:
        clusters = services().clusters
        clusters.check_kommander_crds()

        health_data = {
            "status": "ok",
            "service": "nkp-cluster-cleaner",
            "version": __version__,
            # "nkpcluster" on NKP 2.18+, "capi" on older releases.
            "api_mode": clusters.api_mode,
            "kubeconfig": config.kubeconfig_display,
            "config": config.config_display,
            "timestamp": datetime.now().isoformat(),
        }

        if not config.no_redis:
            health_data["redis"] = f"{config.redis.host}:{config.redis.port}"
            redis_stats = services().analytics().get_database_stats()

            if "error" in redis_stats:
                health_data["redis_status"] = "error"
                health_data["redis_error"] = redis_stats["error"]
            else:
                health_data["redis_status"] = "connected"
                health_data["redis_snapshots"] = redis_stats.get("total_snapshots", 0)

        return jsonify(health_data)

    except Exception as e:
        logger.exception("Health check failed")
        return jsonify(
            {
                "status": "error",
                "service": "nkp-cluster-cleaner",
                "version": __version__,
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }
        ), 500


def normalise_prefix(url_prefix: str | None) -> str:
    """
    Normalise a URL prefix to either "" or "/something".

    Args:
        url_prefix: Raw value, with or without surrounding slashes.

    Returns:
        The normalised prefix.
    """
    if not url_prefix:
        return ""
    stripped = url_prefix.strip("/")
    return f"/{stripped}" if stripped else ""


def create_app(
    kubeconfig_path: str | None = None,
    config_path: str | None = None,
    url_prefix: str | None = None,
    grace_period: str | None = None,
    redis: RedisSettings | None = None,
    no_redis: bool = False,
) -> Flask:
    """
    Create and configure the Flask application.

    Args:
        kubeconfig_path: Path to kubeconfig file.
        config_path: Path to the deletion criteria configuration.
        url_prefix: Prefix for every route, e.g. "/foo".
        grace_period: Clusters younger than this are excluded from the UI.
        redis: Where analytics and notification history live.
        no_redis: Run without Redis, disabling analytics and notifications.

    Returns:
        The configured application.
    """
    prefix = normalise_prefix(url_prefix)

    app = Flask(__name__)
    app.extensions["nkp_cluster_cleaner"] = Services(
        WebSettings(
            kubeconfig_path=kubeconfig_path,
            config_path=config_path,
            url_prefix=prefix,
            grace_period=grace_period,
            redis=redis or RedisSettings(),
            no_redis=no_redis,
        )
    )

    @app.template_global()
    def url_with_prefix(path: str) -> str:
        """Build a URL with the configured prefix. Used throughout the templates."""
        if not path.startswith("/"):
            path = f"/{path}"
        return prefix + path

    app.register_blueprint(pages.bp, url_prefix=prefix or None)
    app.register_blueprint(health_bp, url_prefix=prefix or None)
    app.register_blueprint(api.bp, url_prefix=f"{prefix}/api" if prefix else None)

    return app


def run_server(
    host: str = "127.0.0.1",
    port: int = 8080,
    debug: bool = False,
    kubeconfig_path: str | None = None,
    config_path: str | None = None,
    url_prefix: str | None = None,
    grace_period: str | None = None,
    redis: RedisSettings | None = None,
    no_redis: bool = False,
):
    """
    Run the Flask development server.

    Args:
        host: Address to bind to.
        port: Port to bind to.
        debug: Enable Flask debug mode.
        kubeconfig_path: Path to kubeconfig file.
        config_path: Path to the deletion criteria configuration.
        url_prefix: Prefix for every route.
        grace_period: Clusters younger than this are excluded from the UI.
        redis: Where analytics and notification history live.
        no_redis: Run without Redis.
    """
    redis = redis or RedisSettings()
    prefix = normalise_prefix(url_prefix)

    app = create_app(
        kubeconfig_path=kubeconfig_path,
        config_path=config_path,
        url_prefix=prefix,
        grace_period=grace_period,
        redis=redis,
        no_redis=no_redis,
    )

    base = f"http://{host}:{port}{prefix}"

    print("🚀 Starting NKP Cluster Cleaner web server...")
    print(f"📡 Server URL: {base}")
    print(f"🔧 Debug mode: {'Enabled' if debug else 'Disabled'}")
    print(
        f"📋 Configuration: kubeconfig={kubeconfig_path or 'default'}, "
        f"config={config_path or 'none'}"
    )
    if grace_period:
        print(
            f"⏰ Grace period: {grace_period} "
            "(clusters younger than this will be excluded)"
        )
    if not no_redis:
        print(f"📊 Analytics storage: Redis at {redis}")
    if prefix:
        print(f"🔗 URL prefix: {prefix}")

    endpoints = [
        ("/", "Dashboard"),
        ("/clusters", "Cluster listing"),
        ("/rules", "Deletion rules"),
    ]
    if not no_redis:
        endpoints += [
            ("/analytics", "Analytics dashboard"),
            ("/notifications", "Active notifications"),
        ]
    endpoints += [
        ("/metrics", "Prometheus metrics"),
        ("/scheduled-tasks", "CronJob status"),
        ("/health", "Health check"),
    ]

    print("🔗 Available endpoints:")
    for path, description in endpoints:
        print(f"   • {base}{path} - {description}")
    print("🛑 Press Ctrl+C to stop the server")

    app.run(host=host, port=port, debug=debug)
