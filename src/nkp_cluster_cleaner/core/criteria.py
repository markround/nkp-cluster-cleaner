"""
Evaluation of clusters against the deletion criteria.

This module is deliberately pure: it takes a Cluster and some configuration and
returns a Verdict. It performs no I/O and talks to no API, which makes the most
consequential logic in the tool straightforward to test.
"""

from __future__ import annotations

from datetime import datetime

from .config import ConfigManager
from .models import Cluster, ClusterState, DeletionReason, Verdict
from .timeparse import expiry_from, format_duration, now


def evaluate(
    cluster: Cluster,
    config_manager: ConfigManager,
    grace_period: str | None = None,
    current_time: datetime | None = None,
) -> Verdict:
    """
    Decide what should happen to a cluster.

    Checks run in order of decreasing authority, so a cluster protected for more
    than one reason reports the most important one:

    1. Management cluster — never deleted, whatever else is true of it.
    2. Deletion already in progress — nothing useful left to do.
    3. Protected by configuration — an explicit operator decision.
    4. No deletion target — nothing to delete, so say so rather than guess.
    5. Grace period — too new to judge.
    6. Label compliance, then expiry.

    Args:
        cluster: The cluster to evaluate.
        config_manager: Supplies protection patterns and required labels.
        grace_period: Optional duration; clusters younger than this are spared.
        current_time: Override for "now", for testing. Must be timezone-aware.

    Returns:
        A Verdict describing the outcome.
    """
    current_time = current_time or now()

    if cluster.is_management:
        return Verdict(
            state=ClusterState.MANAGEMENT,
            detail="Cluster is the NKP management cluster",
        )

    if cluster.deleting:
        return Verdict(
            state=ClusterState.DELETING,
            detail="Deletion is already in progress",
        )

    if config_manager.is_cluster_protected(cluster.name, cluster.namespace):
        return Verdict(
            state=ClusterState.PROTECTED,
            detail=f"Cluster {cluster.name} is protected by configuration",
        )

    if cluster.target is None:
        return Verdict(
            state=ClusterState.NO_TARGET,
            detail="No NKPCluster or CAPI cluster found to delete",
        )

    grace_verdict = _check_grace_period(cluster, grace_period, current_time)
    if grace_verdict:
        return grace_verdict

    label_verdict = _check_labels(cluster, config_manager)
    if label_verdict:
        return label_verdict

    return _check_expiry(cluster, current_time)


def _check_grace_period(
    cluster: Cluster, grace_period: str | None, current_time: datetime
) -> Verdict | None:
    """Spare clusters younger than the configured grace period."""
    if not grace_period or not cluster.created_at:
        return None

    try:
        grace_ends = expiry_from(cluster.created_at, grace_period)
    except ValueError:
        # A malformed --grace value must not silently protect everything, so
        # fall through and evaluate the cluster normally. The CLI validates the
        # value up front, so reaching here means it was bad in config.
        return None

    if current_time >= grace_ends:
        return None

    remaining = format_duration(grace_ends - current_time)
    return Verdict(
        state=ClusterState.IN_GRACE,
        detail=f"Cluster is within grace period (ends in ~{remaining})",
    )


def _check_labels(cluster: Cluster, config_manager: ConfigManager) -> Verdict | None:
    """Require the `expires` label plus any configured extra labels."""
    if "expires" not in cluster.labels:
        return Verdict(
            state=ClusterState.FOR_DELETION,
            reason=DeletionReason.MISSING_EXPIRES_LABEL,
            detail="Missing 'expires' label",
        )

    errors = config_manager.validate_extra_labels(cluster.labels)
    if errors:
        # A missing label and a malformed one are different problems, and are
        # worth distinguishing in metrics and reports.
        reason = (
            DeletionReason.MISSING_REQUIRED_LABEL
            if errors[0].startswith("Missing required label")
            else DeletionReason.LABEL_PATTERN_MISMATCH
        )
        return Verdict(
            state=ClusterState.FOR_DELETION,
            reason=reason,
            detail=errors[0],
            label_errors=errors,
        )

    return None


def _check_expiry(cluster: Cluster, current_time: datetime) -> Verdict:
    """Compare the cluster's age against its `expires` label."""
    expires_value = cluster.labels["expires"]

    if not cluster.created_at:
        return Verdict(
            state=ClusterState.FOR_DELETION,
            reason=DeletionReason.MISSING_CREATION_TIMESTAMP,
            detail="Missing creationTimestamp in cluster metadata",
        )

    try:
        expires_at = expiry_from(cluster.created_at, expires_value)
    except ValueError as e:
        return Verdict(
            state=ClusterState.FOR_DELETION,
            reason=DeletionReason.INVALID_EXPIRES_FORMAT,
            detail=f"Invalid 'expires' label format: {expires_value} ({e})",
        )

    if current_time >= expires_at:
        created = cluster.created_at.strftime("%Y-%m-%d")
        return Verdict(
            state=ClusterState.FOR_DELETION,
            reason=DeletionReason.EXPIRED,
            detail=(
                f"Cluster has expired (created: {created}, "
                f"expires after: {expires_value})"
            ),
            expires_at=expires_at,
        )

    remaining = format_duration(expires_at - current_time)
    return Verdict(
        state=ClusterState.ACTIVE,
        detail=f"Cluster has not expired yet (expires in ~{remaining})",
        expires_at=expires_at,
    )
