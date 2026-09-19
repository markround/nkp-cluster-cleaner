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

from ..core.models import ClusterState, DeletionReason
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


#
# Donut slice colours, keyed by DeletionReason so a reason keeps its colour
# whatever its rank in a given fortnight — a reason that drops from first to
# third must not repaint the ring. Declaration order here is the order the ring
# is drawn in: expiry, the routine reason, leads, then the label faults.
#
# The six match NkpCharts.palette.slices and were validated all-pairs, so any
# subset in any order still clears the separation floors. Anything unmapped
# falls to the neutral, which matches NkpCharts.palette.sliceOther.
#
_REASON_SLICES: dict[DeletionReason, str] = {
    DeletionReason.EXPIRED: "#1b6bdb",
    DeletionReason.MISSING_EXPIRES_LABEL: "#199e70",
    DeletionReason.MISSING_REQUIRED_LABEL: "#eda100",
    DeletionReason.LABEL_PATTERN_MISMATCH: "#e87ba4",
    DeletionReason.INVALID_EXPIRES_FORMAT: "#7a3e8f",
    DeletionReason.MISSING_CREATION_TIMESTAMP: "#b5651d",
}
_SLICE_OTHER = "#5c6b7a"


def _register_table_filters(app: Flask):
    """
    Filters that reshape analytics payloads for the charts and their tables.

    Every chart ships a table alternative, so that identity never rests on
    colour alone. These do the reshaping in Jinja rather than duplicating each
    payload's structure in the route.
    """

    @app.template_filter("zip_rows")
    def zip_rows(first, *rest):
        """
        Zip parallel sequences into rows.

        Shorter sequences are padded, so a series the snapshots do not carry
        yet renders as a dash instead of truncating the table.
        """
        columns = [list(first), *[list(column) for column in rest]]
        length = max((len(column) for column in columns), default=0)
        return [
            [column[i] if i < len(column) else "—" for column in columns]
            for i in range(length)
        ]

    @app.template_filter("dict_rows")
    def dict_rows(mapping):
        """Turn {label: count} into rows, largest first."""
        return sorted(
            ([key, value] for key, value in (mapping or {}).items()),
            key=lambda row: row[1],
            reverse=True,
        )

    @app.template_filter("nested_rows")
    def nested_rows(mapping, key, limit=8):
        """
        Turn {label: {key: count, ...}} into rows, largest first.

        Args:
            mapping: The nested payload.
            key: Which inner value to read.
            limit: How many rows to keep, matching the chart's own cap.
        """
        rows = [[name, stats.get(key, 0)] for name, stats in (mapping or {}).items()]
        rows.sort(key=lambda row: row[1], reverse=True)
        return rows[:limit]

    @app.template_filter("reason_slices")
    def reason_slices(mapping):
        """
        Turn {reason label: count} into donut slices.

        Slices come back in DeletionReason order rather than by size, so the
        ring's neighbours stay put as counts move, and each carries the share
        the legend prints beside its count. Reasons with no clusters are left
        out entirely — a zero-width arc is not a slice.

        Args:
            mapping: Counts keyed by DeletionReason.label.

        Returns:
            A list of {label, value, color, share} dicts.
        """
        counts = dict(mapping or {})
        ordered = [
            (reason.label, color)
            for reason, color in _REASON_SLICES.items()
            if counts.get(reason.label)
        ]
        # Anything the enum no longer covers still gets drawn, in the neutral,
        # rather than being dropped silently.
        known = {reason.label for reason in _REASON_SLICES}
        ordered += [
            (label, _SLICE_OTHER)
            for label, value in counts.items()
            if label not in known and value
        ]

        total = sum(counts.get(label, 0) for label, _ in ordered)
        return [
            {
                "label": label,
                "value": counts[label],
                "color": color,
                "share": round(counts[label] / total * 100) if total else 0,
            }
            for label, color in ordered
        ]


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

    # Templates and static files sit next to this module. The static URL has to
    # carry the prefix too, or the CSS 404s behind an ingress path.
    app = Flask(__name__, static_url_path=f"{prefix}/static")
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

    # Templates index counts by state, so they need the enum itself rather than
    # a stringly-typed copy of its members.
    app.jinja_env.globals["ClusterState"] = ClusterState
    _register_table_filters(app)

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
