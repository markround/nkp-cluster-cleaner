"""
Redis-based data collector for NKP Cluster Cleaner analytics.

Takes a point-in-time snapshot of the estate and stores it for the analytics
dashboard and the Prometheus endpoint to read back.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from ..core.config import ConfigManager
from ..core.models import ClusterState, ClusterStatus
from ..core.settings import RedisSettings
from ..core.timeparse import now
from ..k8s.clusters import ClusterManager
from .client import build_redis_client

logger = logging.getLogger(__name__)

#: Buckets used for the expiry distribution, as (label, upper bound).
_EXPIRY_BUCKETS = [
    ("expires_soon", timedelta(days=1)),
    ("expires_this_week", timedelta(days=7)),
    ("expires_this_month", timedelta(days=30)),
]

#: Buckets used for the cluster age distribution, as (label, upper bound).
_AGE_BUCKETS = [
    ("0-1_days", timedelta(days=1)),
    ("1-7_days", timedelta(days=7)),
    ("1-4_weeks", timedelta(days=28)),
    ("1-12_months", timedelta(days=365)),
]


class RedisDataCollector:
    """Collects and stores cluster analytics data using Redis."""

    def __init__(
        self,
        kubeconfig_path: str | None = None,
        config_manager: ConfigManager | None = None,
        redis: RedisSettings | None = None,
        debug: bool = False,
        cluster_manager: ClusterManager | None = None,
    ):
        """
        Args:
            kubeconfig_path: Path to kubeconfig file.
            config_manager: Supplies protection rules and required labels.
            redis: Where to store the snapshots.
            debug: Emit progress output during collection.
            cluster_manager: A pre-built cluster manager. Mainly for tests.
        """
        self.debug = debug
        self.redis_client = build_redis_client(redis)
        self.config_manager = config_manager or ConfigManager()
        self.cluster_manager = cluster_manager or ClusterManager(
            kubeconfig_path, self.config_manager
        )

    def collect_snapshot(self, retention_days: int = 90) -> dict[str, Any]:
        """
        Capture the current cluster state and store it in Redis.

        Args:
            retention_days: How long the snapshot should be kept.

        Returns:
            The snapshot that was stored.
        """
        timestamp = now()
        logger.debug("Collecting analytics snapshot at %s", timestamp.isoformat())

        statuses = self.cluster_manager.get_cluster_statuses()
        logger.debug("Found %d clusters", len(statuses))

        snapshot = self._build_snapshot_data(statuses, timestamp)
        self._store_snapshot(snapshot, timestamp, retention_days)

        cleaned = self._cleanup_old_data(retention_days)
        if cleaned:
            logger.debug("Cleaned up %d old snapshots", cleaned)

        # The caller reports the summary; this records where it landed.
        logger.info("Stored analytics snapshot %s", self._snapshot_key(timestamp))

        return snapshot

    @staticmethod
    def _snapshot_key(timestamp: datetime) -> str:
        """Redis key for a snapshot at the given time."""
        return f"analytics:snapshot:{timestamp.strftime('%Y-%m-%d:%H:%M:%S')}"

    def _store_snapshot(
        self, snapshot: dict[str, Any], timestamp: datetime, retention_days: int
    ):
        """Store the snapshot and its summary, both with a TTL."""
        ttl_seconds = retention_days * 24 * 60 * 60
        score = timestamp.timestamp()

        snapshot_key = self._snapshot_key(timestamp)
        summary_key = f"analytics:summary:{timestamp.strftime('%Y-%m-%d:%H:%M:%S')}"
        counts = snapshot["cluster_counts"]

        summary = {
            "timestamp": timestamp.isoformat(),
            "total_clusters": counts["total"],
            "for_deletion": counts["for_deletion"],
            "deleting": counts["deleting"],
            "protected": counts["protected"],
            "compliance_rate": snapshot["label_compliance"]["overall_compliance_rate"],
        }

        # SET with an expiry rather than SETEX: redis-py deprecated the latter
        # in 2.6.12, and Redis itself has considered it superseded since 2.6.12
        # too. The two are the same command to the server.
        pipe = self.redis_client.pipeline()
        pipe.set(snapshot_key, json.dumps(snapshot), ex=ttl_seconds)
        pipe.zadd("analytics:snapshots:index", {snapshot_key: score})
        pipe.set(summary_key, json.dumps(summary), ex=ttl_seconds)
        pipe.zadd("analytics:summaries:index", {summary_key: score})
        pipe.execute()

    def _cleanup_old_data(self, retention_days: int) -> int:
        """
        Remove snapshots older than the retention window.

        Redis TTLs already expire the payloads; this also prunes the sorted-set
        indexes, which have no TTL of their own.

        Args:
            retention_days: How many days of data to keep.

        Returns:
            How many snapshots were removed.
        """
        cutoff = (now() - timedelta(days=retention_days)).timestamp()

        old_snapshots = self.redis_client.zrangebyscore(
            "analytics:snapshots:index", 0, cutoff
        )
        old_summaries = self.redis_client.zrangebyscore(
            "analytics:summaries:index", 0, cutoff
        )

        if not old_snapshots and not old_summaries:
            return 0

        pipe = self.redis_client.pipeline()
        for key in [*old_snapshots, *old_summaries]:
            pipe.delete(key)
        pipe.zremrangebyscore("analytics:snapshots:index", 0, cutoff)
        pipe.zremrangebyscore("analytics:summaries:index", 0, cutoff)
        pipe.execute()

        return len(old_snapshots)

    def get_historical_data(self, days: int = 30) -> list[dict[str, Any]]:
        """
        Read back stored snapshots.

        Args:
            days: How far back to look.

        Returns:
            Snapshots from the period, oldest first.
        """
        current = now()
        cutoff = (current - timedelta(days=days)).timestamp()

        keys = self.redis_client.zrangebyscore(
            "analytics:snapshots:index", cutoff, current.timestamp()
        )
        if not keys:
            return []

        snapshots = []
        for payload in self.redis_client.mget(keys):
            if not payload:
                continue
            try:
                snapshots.append(json.loads(payload))
            except json.JSONDecodeError as e:
                logger.warning("Skipping unparseable snapshot: %s", e)

        snapshots.sort(key=lambda s: s.get("timestamp", ""))
        return snapshots

    def get_database_stats(self) -> dict[str, Any]:
        """
        Report Redis health and how much analytics data is stored.

        Returns:
            Statistics, or a dict with an "error" key if Redis is unreachable.
        """
        try:
            info = self.redis_client.info()

            oldest = self.redis_client.zrange(
                "analytics:snapshots:index", 0, 0, withscores=True
            )
            newest = self.redis_client.zrange(
                "analytics:snapshots:index", -1, -1, withscores=True
            )

            return {
                "total_snapshots": self.redis_client.zcard("analytics:snapshots:index"),
                "earliest_snapshot": (
                    datetime.fromtimestamp(oldest[0][1]).isoformat() if oldest else None
                ),
                "latest_snapshot": (
                    datetime.fromtimestamp(newest[0][1]).isoformat() if newest else None
                ),
                "redis_version": info.get("redis_version"),
                "redis_memory_used": info.get("used_memory_human"),
                "redis_memory_peak": info.get("used_memory_peak_human"),
                "redis_connected_clients": info.get("connected_clients"),
                "redis_uptime_days": info.get("uptime_in_days"),
            }
        except Exception as e:
            return {"error": str(e)}

    #
    # Snapshot construction
    #
    def _build_snapshot_data(
        self, statuses: list[ClusterStatus], timestamp: datetime
    ) -> dict[str, Any]:
        """Assemble the full snapshot structure."""
        namespaces = {s.cluster.namespace for s in statuses}
        by_state = Counter(s.state.value for s in statuses)

        return {
            "timestamp": timestamp.isoformat(),
            "collection_metadata": {
                "tool_version": self._get_tool_version(),
                "total_clusters_found": len(statuses),
                "namespaces_scanned": len(namespaces),
                "nkp_version": self.cluster_manager.get_nkp_version(),
                "api_mode": self.cluster_manager.api_mode,
            },
            "cluster_counts": {
                "for_deletion": by_state.get(ClusterState.FOR_DELETION.value, 0),
                "deleting": by_state.get(ClusterState.DELETING.value, 0),
                # "protected" means everything not queued for deletion, which is
                # what the dashboards have always charted under that name.
                "protected": sum(1 for s in statuses if not s.should_delete),
                "total": len(statuses),
            },
            "clusters_by_state": dict(by_state),
            "clusters_by_namespace": self._group_by(
                statuses, lambda s: s.cluster.namespace
            ),
            "clusters_by_owner": self._group_by(statuses, lambda s: s.cluster.owner),
            "expiration_analysis": self._analyze_expiration(statuses, timestamp),
            "label_compliance": self._calculate_label_compliance(statuses),
            "protection_rule_effectiveness": self._analyze_protection_rules(statuses),
            "cluster_age_distribution": self._calculate_age_distribution(
                statuses, timestamp
            ),
            "deletion_reasons": self._analyze_deletion_reasons(statuses),
        }

    @staticmethod
    def _get_tool_version() -> str:
        """The running tool version, or "unknown"."""
        try:
            import nkp_cluster_cleaner

            return nkp_cluster_cleaner.__version__
        except ImportError:
            return "unknown"

    @staticmethod
    def _group_by(statuses: list[ClusterStatus], key) -> dict[str, dict[str, int]]:
        """
        Count clusters by some attribute, split into deletion and excluded.

        Args:
            statuses: The clusters to group.
            key: Callable returning the grouping key for a status.

        Returns:
            A dict of key to {"deletion", "excluded", "total"} counts.
        """
        grouped: dict[str, dict[str, int]] = defaultdict(
            lambda: {"deletion": 0, "excluded": 0, "total": 0}
        )
        for status in statuses:
            bucket = grouped[key(status)]
            bucket["deletion" if status.should_delete else "excluded"] += 1
            bucket["total"] += 1
        return dict(grouped)

    @staticmethod
    def _analyze_expiration(
        statuses: list[ClusterStatus], current_time: datetime
    ) -> dict[str, Any]:
        """
        Bucket clusters by how soon they expire.

        Driven by the computed expiry time rather than, as it once was, by
        pattern-matching the text of the reason message.
        """
        buckets = {
            "expired": 0,
            "expires_soon": 0,
            "expires_this_week": 0,
            "expires_this_month": 0,
            "expires_later": 0,
            "no_expiration": 0,
        }
        expires_values = []

        for status in statuses:
            expires_label = status.cluster.expires_label
            if not expires_label:
                buckets["no_expiration"] += 1
                continue

            expires_values.append(expires_label)
            expires_at = status.verdict.expires_at

            if expires_at is None or expires_at <= current_time:
                buckets["expired"] += 1
                continue

            remaining = expires_at - current_time
            for label, upper_bound in _EXPIRY_BUCKETS:
                if remaining < upper_bound:
                    buckets[label] += 1
                    break
            else:
                buckets["expires_later"] += 1

        return {
            "buckets": buckets,
            "common_expires_values": dict(Counter(expires_values).most_common(10)),
            "total_with_expires": len(expires_values),
            "total_without_expires": buckets["no_expiration"],
        }

    def _calculate_label_compliance(
        self, statuses: list[ClusterStatus]
    ) -> dict[str, Any]:
        """Work out how many clusters carry every required label."""
        total = len(statuses)
        required = [
            label.name for label in self.config_manager.get_criteria().extra_labels
        ]
        required.append("expires")

        if not total:
            return {
                "total_clusters": 0,
                "fully_compliant": 0,
                "overall_compliance_rate": 0,
                "label_stats": {},
                "required_labels": required,
            }

        label_stats = {}
        for name in required:
            present = sum(1 for s in statuses if s.cluster.labels.get(name))
            label_stats[name] = {
                "present": present,
                "missing": total - present,
                "compliance_rate": (present / total) * 100,
            }

        fully_compliant = sum(
            1 for s in statuses if all(s.cluster.labels.get(name) for name in required)
        )

        return {
            "total_clusters": total,
            "fully_compliant": fully_compliant,
            "overall_compliance_rate": (fully_compliant / total) * 100,
            "label_stats": label_stats,
            "required_labels": required,
        }

    @staticmethod
    def _analyze_protection_rules(statuses: list[ClusterStatus]) -> dict[str, int]:
        """Count why clusters were spared, keyed by state rather than by text."""
        return dict(
            Counter(
                status.state.label for status in statuses if not status.should_delete
            )
        )

    @staticmethod
    def _calculate_age_distribution(
        statuses: list[ClusterStatus], current_time: datetime
    ) -> dict[str, int]:
        """Bucket clusters by age."""
        buckets = {label: 0 for label, _ in _AGE_BUCKETS}
        buckets["over_1_year"] = 0
        buckets["unknown_age"] = 0

        for status in statuses:
            created_at = status.cluster.created_at
            if not created_at:
                buckets["unknown_age"] += 1
                continue

            age = current_time - created_at
            for label, upper_bound in _AGE_BUCKETS:
                if age <= upper_bound:
                    buckets[label] += 1
                    break
            else:
                buckets["over_1_year"] += 1

        return buckets

    @staticmethod
    def _analyze_deletion_reasons(statuses: list[ClusterStatus]) -> dict[str, int]:
        """Count deletion reasons, keyed by the enum rather than by text."""
        return dict(
            Counter(
                status.verdict.reason.label
                for status in statuses
                if status.should_delete and status.verdict.reason
            )
        )
