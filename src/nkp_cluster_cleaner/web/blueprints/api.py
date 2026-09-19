"""
JSON endpoints.

These are called by the pages' own JavaScript. The two that reach into
Kubernetes deliberately re-check ownership on every call rather than trusting
the caller: the UI only ever offers the tool's own CronJobs, but the endpoints
are reachable directly.
"""

from __future__ import annotations

import logging
from datetime import datetime

from flask import Blueprint, jsonify, request
from kubernetes.client.rest import ApiException

from ..services import services, settings

logger = logging.getLogger(__name__)

bp = Blueprint("api", __name__, url_prefix="/api")

#: Namespace NKP's CronJobs live in.
CRONJOB_NAMESPACE = "kommander"

#: Label marking a CronJob as ours.
OWNED_BY = ("app", "nkp-cluster-cleaner")

#: How many log lines to return per container.
LOG_TAIL_LINES = 200


def _error(message: str, status: int = 400):
    """Build a JSON error response."""
    return jsonify(
        {
            "status": "error",
            "error": message,
            "timestamp": datetime.now().isoformat(),
        }
    ), status


def _is_our_job(cronjob_manager, job_name: str, namespace: str) -> bool:
    """
    Check that a Job was created by one of this tool's CronJobs.

    Args:
        cronjob_manager: Manager providing API access.
        job_name: Job to check.
        namespace: Namespace to look in.

    Returns:
        True if the Job traces back to a CronJob carrying our label.
    """
    job = cronjob_manager.batch_v1.read_namespaced_job(
        name=job_name, namespace=namespace
    )

    label_key, label_value = OWNED_BY
    for owner in job.metadata.owner_references or []:
        if owner.kind != "CronJob":
            continue
        try:
            cronjob = cronjob_manager.batch_v1.read_namespaced_cron_job(
                name=owner.name, namespace=namespace
            )
        except ApiException:
            continue
        if (cronjob.metadata.labels or {}).get(label_key) == label_value:
            return True

    return False


@bp.route("/job-logs")
def job_logs():
    """Return the logs for a Job created by one of our CronJobs."""
    job_name = request.args.get("job_name")
    namespace = request.args.get("namespace", CRONJOB_NAMESPACE)

    if not job_name:
        return _error("job_name parameter is required")

    try:
        cronjob_manager = services().cronjobs

        try:
            if not _is_our_job(cronjob_manager, job_name, namespace):
                return _error(
                    "Access denied: Job was not created by nkp-cluster-cleaner", 403
                )
        except ApiException as e:
            return _error(f"Job not found or access denied: {e}", 404)

        logs_data = []
        for pod in cronjob_manager.get_job_pods(job_name, namespace):
            containers = pod["container_statuses"] or [{"name": None}]
            for container in containers:
                logs = cronjob_manager.get_pod_logs(
                    pod["name"],
                    container["name"],
                    namespace,
                    tail_lines=LOG_TAIL_LINES,
                    job_name=job_name,
                )
                logs_data.append(
                    {
                        "pod_name": pod["name"],
                        "container_name": container["name"] or "default",
                        "logs": logs,
                    }
                )

        return jsonify(
            {
                "status": "success",
                "job_name": job_name,
                "namespace": namespace,
                "logs": logs_data,
                "timestamp": datetime.now().isoformat(),
            }
        )

    except Exception as e:
        logger.exception("Failed to fetch logs for job %s", job_name)
        return _error(str(e), 500)


@bp.route("/trigger-cronjob", methods=["POST"])
def trigger_cronjob():
    """Manually trigger one of our CronJobs."""
    data = request.get_json(silent=True) or {}

    cronjob_name = data.get("cronjob_name")
    if not cronjob_name:
        return _error("cronjob_name is required")

    namespace = data.get("namespace", CRONJOB_NAMESPACE)

    try:
        result = services().cronjobs.trigger_cronjob(cronjob_name, namespace)
    except Exception as e:
        logger.exception("Failed to trigger cronjob %s", cronjob_name)
        return _error(f"Unexpected error: {e}", 500)

    if not result["success"]:
        return _error(result["error"])

    return jsonify(
        {
            "status": "success",
            "message": result["message"],
            "job_name": result["job_name"],
            "cronjob_name": result["cronjob_name"],
            "namespace": result["namespace"],
            "timestamp": datetime.now().isoformat(),
        }
    )


@bp.route("/delete-notification", methods=["POST"])
def delete_notification():
    """Clear a cluster's notification history, so it can be alerted on again."""
    data = request.get_json(silent=True) or {}

    cluster_name = data.get("cluster_name")
    namespace = data.get("namespace")
    if not cluster_name or not namespace:
        return _error("cluster_name and namespace are required")

    if settings().no_redis:
        return _error("Notifications feature requires Redis/analytics to be enabled.")

    try:
        cleared = (
            services()
            .notification_history()
            .clear_cluster_history(cluster_name, namespace)
        )
    except Exception as e:
        logger.exception("Failed to clear notification history for %s", cluster_name)
        return _error(str(e), 500)

    if not cleared:
        return _error("Notification history not found or already deleted")

    return jsonify(
        {
            "status": "success",
            "message": (
                f"Notification history cleared for {cluster_name} in {namespace}"
            ),
        }
    )
