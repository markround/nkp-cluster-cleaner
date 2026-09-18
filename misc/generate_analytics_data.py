#!/usr/bin/env python3
"""
Generate a month of fake analytics snapshots in Redis/Valkey.

Populates the analytics dashboard with a plausible estate history so the charts
can be worked on without a cluster, a scheduled CronJob, or a month of waiting:

    ./misc/generate_analytics_data.py
    nkp-cluster-cleaner serve --config config.yaml --redis-host localhost

Snapshots are written by the real RedisDataCollector, with `now()` moved back
in time for each one. That means the keys, TTLs, sorted-set indexes, summary
records and every field inside a snapshot are produced by the same code the
CronJob runs, and stay correct if that code changes. What this script fakes is
only the estate itself: it simulates the life of each cluster — created,
labelled or not, expiring, deleted — and lets the real criteria decide what
state each one was in at each point in time.

Only keys under `analytics:` are ever written or deleted. Generating starts by
clearing the previous run so repeated runs give a fresh, self-consistent
history; pass --keep-existing to add to what is already there, or --clear to
wipe without generating.
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Support running straight from a checkout, without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nkp_cluster_cleaner.core.config import ConfigManager  # noqa: E402
from nkp_cluster_cleaner.core.criteria import evaluate  # noqa: E402
from nkp_cluster_cleaner.core.models import (  # noqa: E402
    Cluster,
    ClusterStatus,
    capi_ref,
    kommander_ref,
    nkp_ref,
)
from nkp_cluster_cleaner.core.settings import RedisSettings  # noqa: E402
from nkp_cluster_cleaner.storage import collector as collector_module  # noqa: E402
from nkp_cluster_cleaner.storage.client import build_redis_client  # noqa: E402
from nkp_cluster_cleaner.storage.collector import RedisDataCollector  # noqa: E402

#: Every key this script touches lives under one of these.
ANALYTICS_PATTERNS = ["analytics:snapshot:*", "analytics:summary:*"]
ANALYTICS_INDEXES = ["analytics:snapshots:index", "analytics:summaries:index"]

#: Namespaces clusters are created in, with relative weights.
NAMESPACES = {
    "kommander-default-workspace": 5,
    "team-alpha": 3,
    "team-beta": 3,
    "team-gamma": 2,
    "customer-demo": 2,
}

#: Owners, with relative weights. Clusters with no owner label at all are
#: generated separately, and report as "unknown".
OWNERS = {
    "mdr": 4,
    "alice": 3,
    "bob": 3,
    "carol": 2,
    "dave": 2,
    "erin": 1,
}

#: `expires` values, with relative weights. Short values dominate, as in a lab.
EXPIRES_VALUES = {
    "4h": 1,
    "12h": 2,
    "1d": 4,
    "2d": 3,
    "1w": 4,
    "2w": 2,
    "30d": 2,
    "90d": 1,
}

#: Malformed values, so the "Invalid expires format" reason shows up.
BAD_EXPIRES_VALUES = ["soon", "forever", "1 day", "7", "1m"]

#: Clusters that exist for the whole window, covering the states a purely
#: random estate would rarely produce. Names match the protection patterns in
#: the repository's config.yaml.
PERMANENT = [
    {"name": "nkp-mgmt-cluster", "namespace": "kommander", "management": True},
    {"name": "workload-1", "namespace": "team-beta", "expires": "90d", "owner": "mdr"},
    {"name": "production-api", "namespace": "team-beta", "owner": "bob"},
    {"name": "edge-gateway", "namespace": "customer-prod", "owner": "carol"},
    {"name": "dev-scratch", "namespace": "default"},
    # Nothing left to delete: shows up as "No target".
    {
        "name": "ghost-cluster",
        "namespace": "kommander-default-workspace",
        "expires": "1d",
        "owner": "dave",
        "no_target": True,
    },
]


@dataclass
class ClusterLife:
    """
    One cluster's whole existence, from creation to removal.

    The generator decides these up front for every cluster; a snapshot is then
    just a question asked of each life: did you exist at time T, and in what
    shape?
    """

    name: str
    namespace: str
    created_at: datetime
    labels: dict[str, str] = field(default_factory=dict)

    #: When the cluster disappeared. None means it is still there at the end.
    removed_at: datetime | None = None

    #: When teardown started, so the cluster reports as Deleting.
    deleting_from: datetime | None = None

    is_management: bool = False

    #: If True, no NKPCluster is resolved, so the cluster has no deletion target.
    no_target: bool = False

    def exists_at(self, when: datetime) -> bool:
        """Whether the cluster is in the estate at this moment."""
        if when < self.created_at:
            return False
        return self.removed_at is None or when < self.removed_at

    def snapshot_at(self, when: datetime) -> Cluster:
        """Build the domain Cluster as it looked at this moment."""
        target = None if self.no_target else nkp_ref(self.name, self.namespace)
        return Cluster(
            name=self.name,
            namespace=self.namespace,
            labels=dict(self.labels),
            created_at=self.created_at,
            kommander=kommander_ref(self.name, self.namespace),
            nkp=target,
            capi=capi_ref(self.name, self.namespace),
            target=target,
            deleting=bool(self.deleting_from and when >= self.deleting_from),
            is_management=self.is_management,
        )


def weighted(rng: random.Random, weights: dict[str, int]) -> str:
    """Pick one key, in proportion to its weight."""
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def parse_expires(value: str) -> timedelta | None:
    """
    Turn an `expires` label into a duration, or None if it is not valid.

    Deliberately a local reimplementation: the generator needs to know when a
    cluster *will* become deletable in order to schedule its removal, which is
    a different question from the one core.timeparse answers.
    """
    units = {"h": "hours", "d": "days", "w": "weeks", "y": "days"}
    try:
        amount, unit = int(value[:-1]), value[-1]
    except (ValueError, IndexError):
        return None
    if unit not in units:
        return None
    if unit == "y":
        amount *= 365
    return timedelta(**{units[unit]: amount})


def make_life(
    rng: random.Random,
    index: int,
    birth_start: datetime,
    window_start: datetime,
    end: datetime,
) -> ClusterLife:
    """
    Invent one cluster's life.

    Labelling improves as the window progresses — a later birth means better
    odds of a complete label set — so the compliance chart has a direction to
    show rather than being flat noise.

    Args:
        rng: Seeded source of randomness.
        index: Used to keep cluster names unique.
        birth_start: Earliest possible birth. Before the reporting window, so
            the first snapshots see an estate that is already running.
        window_start: Start of the reporting window.
        end: End of the reporting window.

    Returns:
        A fully determined ClusterLife.
    """
    created_at = birth_start + timedelta(
        seconds=rng.uniform(0, (end - birth_start).total_seconds())
    )

    # 0.0 at the start of the reporting window, 1.0 at its end. Clamped, so
    # everything born beforehand shares the same starting standard of labelling
    # and the improvement is visible within the charted period rather than
    # mostly having happened before it.
    window_span = (end - window_start).total_seconds()
    maturity = min(
        1.0, max(0.0, (created_at - window_start).total_seconds() / window_span)
    )

    namespace = weighted(rng, NAMESPACES)
    labels = {}

    # Missing labels get steadily rarer, from about 2 in 5 to about 1 in 8.
    miss_chance = 0.45 - 0.33 * maturity

    if rng.random() > miss_chance:
        labels["owner"] = weighted(rng, OWNERS)

    if rng.random() > miss_chance:
        labels["expires"] = weighted(rng, EXPIRES_VALUES)
    elif rng.random() < 0.3:
        # Tried to set an expiry and got the format wrong.
        labels["expires"] = rng.choice(BAD_EXPIRES_VALUES)

    life = ClusterLife(
        name=f"{namespace.split('-')[0]}-{rng.choice(SUFFIXES)}-{index:02d}",
        namespace=namespace,
        created_at=created_at,
        labels=labels,
    )

    # When the cleaner would first pick this cluster up. A cluster missing a
    # required label is deletable from birth; a labelled one only once it
    # outlives its `expires` value.
    duration = parse_expires(labels["expires"]) if labels.get("expires") else None
    if "owner" not in labels or duration is None:
        deletable_at = created_at
    else:
        deletable_at = created_at + duration

    # Most are removed within a day or so, by the next run of the deletion job.
    # The rest linger for days: deletion is running in dry-run, someone keeps
    # re-labelling them, or teardown is stuck. That mix is what gives the "for
    # deletion" series a standing backlog instead of a spike per cluster.
    if rng.random() < 0.7:
        delay = timedelta(hours=rng.uniform(2, 40))
    else:
        delay = timedelta(days=rng.uniform(3, 10))

    removed_at = deletable_at + delay
    # A removal past the end of the window just means the cluster is still
    # there when the history stops.
    if removed_at < end:
        life.removed_at = removed_at
        # NKPCluster teardown is slow, so clusters are visibly Deleting for a
        # while before they vanish.
        life.deleting_from = removed_at - timedelta(hours=rng.uniform(2, 8))

    return life


#: Name fragments, to keep the generated estate readable rather than uuid soup.
SUFFIXES = [
    "lab",
    "demo",
    "sandbox",
    "poc",
    "training",
    "test",
    "bench",
    "pilot",
    "review",
    "workshop",
]


def build_estate(
    rng: random.Random, count: int, start: datetime, end: datetime
) -> list[ClusterLife]:
    """
    Build the full set of cluster lives covering the window.

    Args:
        rng: Seeded source of randomness.
        count: How many ephemeral clusters to invent.
        start: Start of the reporting window.
        end: End of the reporting window.

    Returns:
        Every cluster that exists at any point in the window.
    """
    # Births start well before the window so the estate is already populated,
    # with a spread of ages, when the first snapshot is taken.
    birth_start = start - timedelta(days=45)

    lives = [make_life(rng, i, birth_start, start, end) for i in range(count)]

    for spec in PERMANENT:
        labels = {}
        if spec.get("expires"):
            labels["expires"] = spec["expires"]
        if spec.get("owner"):
            labels["owner"] = spec["owner"]
        lives.append(
            ClusterLife(
                name=spec["name"],
                namespace=spec["namespace"],
                created_at=birth_start - timedelta(days=rng.uniform(30, 200)),
                labels=labels,
                is_management=spec.get("management", False),
                no_target=spec.get("no_target", False),
            )
        )

    return lives


class SimulatedClusterManager:
    """
    Stands in for ClusterManager, answering for a moment in simulated time.

    RedisDataCollector only asks a cluster manager three things, so this is all
    it takes to drive the real collection path with invented data.
    """

    def __init__(self, lives: list[ClusterLife], config_manager: ConfigManager):
        self.lives = lives
        self.config_manager = config_manager
        self.when = datetime.now(UTC)
        self.api_mode = "nkpcluster"

    def get_cluster_statuses(self, namespace: str | None = None) -> list[ClusterStatus]:
        """Evaluate the estate as it stood at `self.when`, using the real criteria."""
        statuses = []
        for life in self.lives:
            if not life.exists_at(self.when):
                continue
            cluster = life.snapshot_at(self.when)
            statuses.append(
                ClusterStatus(
                    cluster=cluster,
                    verdict=evaluate(cluster, self.config_manager, None, self.when),
                )
            )
        statuses.sort(key=lambda s: (s.cluster.namespace, s.cluster.name))
        return statuses

    @staticmethod
    def get_nkp_version() -> str:
        """The version the fake management cluster reports."""
        return "v2.18.0"


def clear_analytics(client) -> int:
    """
    Delete every analytics key, including the sorted-set indexes.

    Scans for the payload keys rather than trusting the indexes, so entries
    whose index row was lost are cleaned up too.

    Args:
        client: A Redis client.

    Returns:
        How many keys were deleted.
    """
    keys = list(ANALYTICS_INDEXES)
    for pattern in ANALYTICS_PATTERNS:
        keys.extend(client.scan_iter(match=pattern, count=500))

    deleted = 0
    batch = []
    for key in keys:
        batch.append(key)
        if len(batch) >= 500:
            deleted += client.delete(*batch)
            batch = []
    if batch:
        deleted += client.delete(*batch)

    return deleted


def generate(
    collector: RedisDataCollector,
    manager: SimulatedClusterManager,
    timestamps: list[datetime],
    retention_days: int,
    quiet: bool,
) -> list[dict]:
    """
    Write one snapshot per timestamp, with the collector's clock moved back.

    Args:
        collector: The real collector, wired to Redis.
        manager: Supplies the estate as it stood at each timestamp.
        timestamps: Snapshot times, oldest first.
        retention_days: TTL applied to each snapshot.
        quiet: Suppress the per-day progress output.

    Returns:
        The snapshots that were stored.
    """
    original_now = collector_module.now
    snapshots = []
    last_day = None

    try:
        for when in timestamps:
            manager.when = when
            # The collector stamps and keys each snapshot with now(), and that
            # is the only thing standing between this and a month of identical
            # timestamps. Patched per snapshot, restored in the finally below.
            collector_module.now = lambda when=when: when

            snapshot = collector.collect_snapshot(retention_days)
            snapshots.append(snapshot)

            day = when.date()
            if not quiet and day != last_day:
                counts = snapshot["cluster_counts"]
                rate = snapshot["label_compliance"]["overall_compliance_rate"]
                print(
                    f"  {day}  total {counts['total']:>3}"
                    f"  for deletion {counts['for_deletion']:>3}"
                    f"  deleting {counts['deleting']:>2}"
                    f"  compliance {rate:5.1f}%"
                )
                last_day = day
    finally:
        collector_module.now = original_now

    return snapshots


def summarise(snapshots: list[dict]):
    """Print what the generated history looks like overall."""
    if not snapshots:
        return

    totals = [s["cluster_counts"]["total"] for s in snapshots]
    deletions = [s["cluster_counts"]["for_deletion"] for s in snapshots]
    rates = [s["label_compliance"]["overall_compliance_rate"] for s in snapshots]

    def span(values, fmt="{:.0f}"):
        lo, hi = fmt.format(min(values)), fmt.format(max(values))
        avg = fmt.format(sum(values) / len(values))
        return f"{lo}–{hi} (avg {avg})"

    print()
    print(f"Stored {len(snapshots)} snapshots")
    print(f"  from:         {snapshots[0]['timestamp']}")
    print(f"  to:           {snapshots[-1]['timestamp']}")
    print(f"  clusters:     {span(totals)}")
    print(f"  for deletion: {span(deletions)}")
    print(f"  compliance:   {span(rates, '{:.1f}')}%")

    reasons = snapshots[-1]["deletion_reasons"]
    if reasons:
        print("  latest deletion reasons:")
        for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"      {count:>3}  {reason}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate fake analytics snapshots in Redis for UI testing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ./misc/generate_analytics_data.py\n"
            "  ./misc/generate_analytics_data.py --days 90 --interval-hours 6\n"
            "  ./misc/generate_analytics_data.py --clear\n"
        ),
    )
    parser.add_argument("--redis-host", default="localhost", help="Redis host")
    parser.add_argument("--redis-port", type=int, default=6379, help="Redis port")
    parser.add_argument("--redis-db", type=int, default=0, help="Redis database number")
    parser.add_argument("--redis-username", help="Redis username")
    parser.add_argument("--redis-password", help="Redis password")
    parser.add_argument(
        "--days", type=int, default=30, help="Days of history to generate (default: 30)"
    )
    parser.add_argument(
        "--interval-hours",
        type=float,
        default=1,
        help="Hours between snapshots; the chart's CronJob runs hourly (default: 1)",
    )
    parser.add_argument(
        "--clusters",
        type=int,
        default=420,
        help="Ephemeral cluster lifetimes to simulate. They come and go, so the\n"
        "live estate is much smaller than this (default: 420)",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=90,
        help="TTL applied to each snapshot, as collect-analytics does (default: 90)",
    )
    parser.add_argument(
        "--config",
        default=str(Path(__file__).resolve().parent.parent / "config.yaml"),
        help="Config file supplying protection rules and required labels",
    )
    parser.add_argument(
        "--seed", type=int, default=1979, help="Seed, so runs are reproducible"
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Delete all analytics data and exit without generating",
    )
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="Add to the existing analytics data instead of replacing it",
    )
    parser.add_argument("--quiet", action="store_true", help="Only print the summary")
    args = parser.parse_args()

    redis_settings = RedisSettings(
        host=args.redis_host,
        port=args.redis_port,
        db=args.redis_db,
        username=args.redis_username,
        password=args.redis_password,
    )

    try:
        client = build_redis_client(redis_settings)
    except Exception as e:
        sys.exit(f"ERROR: {e}")

    print(f"Redis: {redis_settings}")

    if args.clear:
        print(f"Cleared {clear_analytics(client)} analytics keys")
        return

    if not args.keep_existing:
        print(f"Cleared {clear_analytics(client)} existing analytics keys")

    config_path = Path(args.config)
    if config_path.is_file():
        config_manager = ConfigManager(str(config_path))
        print(f"Config: {config_path}")
    else:
        # Without extra_labels the `owner` label is not required, so compliance
        # and deletion reasons would both be less interesting. Say so.
        config_manager = ConfigManager()
        print(f"Config: {config_path} not found, using defaults (no required labels)")

    end = datetime.now(UTC)
    start = end - timedelta(days=args.days)
    step = timedelta(hours=args.interval_hours)

    timestamps = []
    when = start
    while when <= end:
        timestamps.append(when)
        when += step

    rng = random.Random(args.seed)
    lives = build_estate(rng, args.clusters, start, end)

    collector = RedisDataCollector(
        config_manager=config_manager,
        redis=redis_settings,
        cluster_manager=SimulatedClusterManager(lives, config_manager),
    )

    print(
        f"Generating {len(timestamps)} snapshots every {args.interval_hours}h "
        f"across {args.days} days, from {len(lives)} simulated clusters"
    )
    print()

    snapshots = generate(
        collector,
        collector.cluster_manager,
        timestamps,
        args.retention_days,
        args.quiet,
    )
    summarise(snapshots)

    print()
    print("View it with:")
    print(
        "  nkp-cluster-cleaner serve --config config.yaml "
        f"--redis-host {args.redis_host} --redis-port {args.redis_port}"
    )
    print("  then open /analytics")


if __name__ == "__main__":
    main()
