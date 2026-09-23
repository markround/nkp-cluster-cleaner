"""
Human-facing pages.

Every page renders even when the cluster or Redis is unreachable: the route
returns its own template with an `error` set, rather than a generic error page,
so the operator keeps the navigation and can see what failed and where.
`render_or_error` exists so that fallback does not have to be spelled out twice
in every route.
"""

from __future__ import annotations

import logging
from datetime import datetime

from flask import Blueprint, render_template, request

import nkp_cluster_cleaner

from ...core.models import ClusterState
from ...metrics.prometheus import PrometheusMetricsService
from ...notifications.manager import CRITICAL, WARNING
from ..services import services, settings

logger = logging.getLogger(__name__)

bp = Blueprint("pages", __name__)

__version__ = nkp_cluster_cleaner.__version__

#: States shown under the "excluded" heading, in the order they appear.
EXCLUDED_STATES = [
    ClusterState.MANAGEMENT,
    ClusterState.PROTECTED,
    ClusterState.IN_GRACE,
    ClusterState.ACTIVE,
    ClusterState.NO_TARGET,
]

#: Thresholds the notifications page reports against. The notify command takes
#: these as options; the page shows the defaults the CronJob ships with.
WARNING_THRESHOLD = 80
CRITICAL_THRESHOLD = 95

#: Namespace NKP's CronJobs live in.
CRONJOB_NAMESPACE = "kommander"

#: Shape the scheduled-tasks page expects when the lookup fails.
_EMPTY_TASK_SUMMARY = {
    "total_cronjobs": 0,
    "active_cronjobs": 0,
    "suspended_cronjobs": 0,
    "total_recent_jobs": 0,
    "successful_jobs": 0,
    "failed_jobs": 0,
    "running_jobs": 0,
    "cronjobs": [],
    "recent_jobs": [],
}


def base_context() -> dict:
    """Template variables every page needs."""
    config = settings()
    return {
        "no_redis": config.no_redis,
        "grace_period": config.grace_period,
        "version": __version__,
        "refresh_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def render_or_error(template: str, build: callable, **fallback) -> str:
    """
    Render a page, falling back to the same page with an error message.

    Args:
        template: Template to render.
        build: Callable returning the page's template variables. Any exception
            it raises becomes the page's error message.
        **fallback: Variables the template needs in order to render at all when
            `build` failed.

    Returns:
        The rendered page.
    """
    context = base_context()
    try:
        return render_template(template, **context, **build(), error=None)
    except Exception as e:
        logger.exception("Failed to render %s", template)
        return render_template(template, **context, **fallback, error=str(e))


@bp.route("/")
def index():
    """Dashboard: current estate at a glance, plus how the tool is configured."""

    def build():
        manager = services().clusters
        grouped = manager.group_by_state()

        return {
            "nkp_version": manager.get_nkp_version(),
            "api_mode": manager.api_mode,
            "counts": {state: len(rows) for state, rows in grouped.items()},
            "total_clusters": sum(len(rows) for rows in grouped.values()),
            # The soonest-expiring clusters, so the dashboard answers "what is
            # about to go" without a trip to the clusters page.
            "upcoming": _soonest_expiring(grouped[ClusterState.ACTIVE]),
            "for_deletion": grouped[ClusterState.FOR_DELETION],
            "kubeconfig_status": settings().kubeconfig_path
            or "Using default (~/.kube/config)",
            "config_status": settings().config_path
            or "Using default (no protection rules)",
        }

    return render_or_error(
        "index.html",
        build,
        nkp_version=None,
        api_mode="unknown",
        counts={state: 0 for state in ClusterState},
        total_clusters=0,
        upcoming=[],
        for_deletion=[],
        kubeconfig_status=settings().kubeconfig_display,
        config_status=settings().config_display,
    )


def _soonest_expiring(statuses: list, limit: int = 5) -> list:
    """
    The active clusters closest to expiry.

    Args:
        statuses: Clusters in the ACTIVE state.
        limit: How many to return.

    Returns:
        Up to `limit` clusters, soonest expiry first. Clusters with no
        computable expiry are omitted rather than sorted arbitrarily.
    """
    datable = [s for s in statuses if s.verdict.expires_at]
    datable.sort(key=lambda s: s.verdict.expires_at)
    return datable[:limit]


@bp.route("/clusters")
def clusters():
    """Clusters grouped by what the tool has decided about them."""
    namespace_filter = request.args.get("namespace")

    def build():
        manager = services().clusters
        grouped = manager.group_by_state(namespace_filter)
        return {
            "clusters_to_delete": grouped[ClusterState.FOR_DELETION],
            "deleting_clusters": grouped[ClusterState.DELETING],
            "excluded_clusters": [
                status for state in EXCLUDED_STATES for status in grouped[state]
            ],
            "api_mode": manager.api_mode,
            "namespace_filter": namespace_filter,
            "kubeconfig_status": settings().kubeconfig_display,
            "config_status": settings().config_display,
        }

    return render_or_error(
        "clusters.html",
        build,
        clusters_to_delete=[],
        deleting_clusters=[],
        excluded_clusters=[],
        api_mode="unknown",
        namespace_filter=namespace_filter,
        kubeconfig_status=settings().kubeconfig_display,
        config_status=settings().config_display,
    )


@bp.route("/rules")
def rules():
    """Deletion rules and configuration summary."""

    def build():
        criteria = services().config_manager.get_criteria()
        return {
            # Core rules: missing expires, missing extra label, expired,
            # invalid expires format.
            "rule_count": 4,
            # Supported time units: h, d, w, y.
            "time_format_count": 4,
            "protected_cluster_count": len(criteria.protected_cluster_patterns),
            "excluded_namespace_count": len(criteria.excluded_namespace_patterns),
            "extra_labels_count": len(criteria.extra_labels),
            "protected_cluster_patterns": criteria.protected_cluster_patterns,
            "excluded_namespace_patterns": criteria.excluded_namespace_patterns,
            "extra_labels": criteria.extra_labels,
            "kubeconfig_path": settings().kubeconfig_path,
            "config_path": settings().config_path,
        }

    return render_or_error(
        "rules.html",
        build,
        rule_count=4,
        time_format_count=4,
        protected_cluster_count=0,
        excluded_namespace_count=0,
        extra_labels_count=0,
        protected_cluster_patterns=[],
        excluded_namespace_patterns=[],
        extra_labels=[],
        kubeconfig_path=settings().kubeconfig_path,
        config_path=settings().config_path,
    )


@bp.route("/scheduled-tasks")
def scheduled_tasks():
    """Scheduled tasks (CronJobs) and their recent executions."""

    def build():
        summary = services().cronjobs.get_all_scheduled_tasks_summary(CRONJOB_NAMESPACE)
        return {"summary": summary, "namespace": CRONJOB_NAMESPACE}

    return render_or_error(
        "scheduled_tasks.html",
        build,
        summary=_EMPTY_TASK_SUMMARY,
        namespace=CRONJOB_NAMESPACE,
    )


@bp.route("/analytics")
def analytics():
    """Historical analytics dashboard."""
    if settings().no_redis:
        return render_template(
            "analytics.html",
            **base_context(),
            error="Analytics features have been disabled.",
        )

    def build():
        service = services().analytics()
        return {
            "cluster_trends_7d": service.get_cluster_trends(7),
            "cluster_trends_30d": service.get_cluster_trends(30),
            "deletion_activity": service.get_deletion_activity(14),
            "compliance_stats": service.get_compliance_stats(30),
            "namespace_activity": service.get_namespace_activity(30),
            "owner_distribution": service.get_owner_distribution(30),
            "expiration_analysis": service.get_expiration_analysis(30),
            "dashboard_summary": service.get_dashboard_summary(),
        }

    return render_or_error("analytics.html", build)


@bp.route("/notifications")
def notifications():
    """Clusters that currently warrant an alert."""
    if settings().no_redis:
        return render_template(
            "notifications.html",
            **base_context(),
            error="Notifications feature requires Redis/analytics to be enabled.",
            critical_count=0,
            warning_count=0,
            total_count=0,
        )

    def build():
        history = services().notification_history()
        found = services().notifications.get_notifications(
            WARNING_THRESHOLD, CRITICAL_THRESHOLD
        )

        critical = [n.as_dict() for n in found if n.severity == CRITICAL]
        warning = [n.as_dict() for n in found if n.severity == WARNING]

        return {
            "critical_notifications": critical,
            "warning_notifications": warning,
            "critical_count": len(critical),
            "warning_count": len(warning),
            "total_count": len(critical) + len(warning),
            "warning_threshold": WARNING_THRESHOLD,
            "critical_threshold": CRITICAL_THRESHOLD,
            "notification_stats": {
                "total_tracked": len(found),
                "active_keys": history.get_active_notification_count(),
            },
            "active_notifications": history.get_all_notified_clusters(),
        }

    return render_or_error(
        "notifications.html",
        build,
        critical_count=0,
        warning_count=0,
        total_count=0,
    )


@bp.route("/metrics")
def metrics():
    """Prometheus metrics endpoint."""
    content_type = {"Content-Type": "text/plain; charset=utf-8"}

    try:
        # The deletion API in use is reported whether or not analytics is on,
        # but must not be allowed to fail the scrape if the cluster is down.
        try:
            api_mode = services().clusters.api_mode
        except Exception:
            api_mode = "unknown"

        analytics_service = None if settings().no_redis else services().analytics()
        service = PrometheusMetricsService(analytics_service, api_mode=api_mode)
        return service.generate_metrics(), 200, content_type
    except Exception as e:
        # A scrape must never fail: emit the error as a metric instead, so the
        # problem is visible in Prometheus rather than as a gap in the data.
        logger.warning("Falling back to error metrics: %s", e)
        return (
            PrometheusMetricsService().generate_error_metrics(str(e)),
            200,
            content_type,
        )
