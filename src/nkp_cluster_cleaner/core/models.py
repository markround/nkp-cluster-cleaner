"""
Core domain model.

The point of this module is that a cluster's status is expressed as *data* —
an enum plus structured fields — rather than as a prose string that other
modules then have to re-parse. `Verdict.detail` exists only to be shown to a
human; nothing should ever branch on its contents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

# API coordinates for the resources this tool touches.
KOMMANDER_GROUP = "kommander.mesosphere.io"
KOMMANDER_VERSION = "v1beta1"
KOMMANDER_PLURAL = "kommanderclusters"

NKP_GROUP = "clusters.nkp.nutanix.com"
NKP_VERSION = "v1alpha1"
NKP_PLURAL = "nkpclusters"

CAPI_GROUP = "cluster.x-k8s.io"
CAPI_VERSION = "v1beta1"
CAPI_PLURAL = "clusters"

#: Namespace the management cluster's KommanderCluster lives in. Before NKP 2.18
#: it was additionally always named "host-cluster"; that is no longer true.
MANAGEMENT_NAMESPACE = "kommander"

#: Label NKP sets on the management cluster's KommanderCluster.
MANAGEMENT_LABEL = "kommander.d2iq.io/host"

#: Legacy name of the management cluster, kept as a fallback signal.
LEGACY_MANAGEMENT_NAME = "host-cluster"

#: Owner reported for a cluster with no `owner` label. Analytics groups by
#: owner, so this is a key those results are looked up under, not just display
#: text — hence a constant rather than a literal in two places.
UNKNOWN_OWNER = "unknown"


class ClusterState(Enum):
    """
    What the tool has decided about a cluster.

    Exactly one state applies. Everything downstream — the CLI tables, the web
    UI, notifications, metrics and analytics — branches on this rather than on
    message text.
    """

    #: Matches the deletion criteria and should be deleted.
    FOR_DELETION = "for_deletion"
    #: Deletion already in progress; the target has a deletionTimestamp.
    DELETING = "deleting"
    #: The NKP management cluster. Never deleted.
    MANAGEMENT = "management"
    #: Excluded by a protected-cluster or excluded-namespace pattern.
    PROTECTED = "protected"
    #: Too new to consider, per the configured grace period.
    IN_GRACE = "in_grace"
    #: Compliant and not yet expired.
    ACTIVE = "active"
    #: No resource left to delete. Excluded so the tool never guesses.
    NO_TARGET = "no_target"

    @property
    def is_deletable(self) -> bool:
        """True if a delete should actually be issued for this cluster."""
        return self is ClusterState.FOR_DELETION

    @property
    def label(self) -> str:
        """Short human-readable name, for tables and UI badges."""
        return {
            ClusterState.FOR_DELETION: "For deletion",
            ClusterState.DELETING: "Deleting",
            ClusterState.MANAGEMENT: "Management",
            ClusterState.PROTECTED: "Protected",
            ClusterState.IN_GRACE: "In grace",
            ClusterState.ACTIVE: "Active",
            ClusterState.NO_TARGET: "No target",
        }[self]


class DeletionReason(Enum):
    """Why a cluster is in ClusterState.FOR_DELETION."""

    MISSING_EXPIRES_LABEL = "missing_expires_label"
    MISSING_REQUIRED_LABEL = "missing_required_label"
    LABEL_PATTERN_MISMATCH = "label_pattern_mismatch"
    INVALID_EXPIRES_FORMAT = "invalid_expires_format"
    MISSING_CREATION_TIMESTAMP = "missing_creation_timestamp"
    EXPIRED = "expired"

    @property
    def label(self) -> str:
        """Short human-readable name, used as a metrics label and in reports."""
        return {
            DeletionReason.MISSING_EXPIRES_LABEL: "Missing expires label",
            DeletionReason.MISSING_REQUIRED_LABEL: "Missing required label",
            DeletionReason.LABEL_PATTERN_MISMATCH: "Label pattern mismatch",
            DeletionReason.INVALID_EXPIRES_FORMAT: "Invalid expires format",
            DeletionReason.MISSING_CREATION_TIMESTAMP: "Missing creation timestamp",
            DeletionReason.EXPIRED: "Cluster expired",
        }[self]

    @property
    def is_immediate(self) -> bool:
        """
        True if the cluster is deleted the moment it is seen, rather than on a
        timer. Notifications treat these as critical straight away, since there
        is no countdown to warn about.
        """
        return self is not DeletionReason.EXPIRED


@dataclass(frozen=True)
class ResourceRef:
    """A reference to a namespaced custom resource."""

    group: str
    version: str
    plural: str
    name: str
    namespace: str

    def __str__(self) -> str:
        return f"{self.namespace}/{self.name}"

    @property
    def kind_name(self) -> str:
        """Human-readable kind, e.g. 'NKPCluster' for display in dry-run output."""
        return {
            NKP_PLURAL: "NKPCluster",
            CAPI_PLURAL: "Cluster",
            KOMMANDER_PLURAL: "KommanderCluster",
        }.get(self.plural, self.plural)


def kommander_ref(name: str, namespace: str) -> ResourceRef:
    """Build a reference to a KommanderCluster."""
    return ResourceRef(
        KOMMANDER_GROUP, KOMMANDER_VERSION, KOMMANDER_PLURAL, name, namespace
    )


def nkp_ref(name: str, namespace: str) -> ResourceRef:
    """Build a reference to an NKPCluster."""
    return ResourceRef(NKP_GROUP, NKP_VERSION, NKP_PLURAL, name, namespace)


def capi_ref(name: str, namespace: str) -> ResourceRef:
    """Build a reference to a CAPI Cluster."""
    return ResourceRef(CAPI_GROUP, CAPI_VERSION, CAPI_PLURAL, name, namespace)


@dataclass
class Cluster:
    """
    A single cluster, assembled from the KommanderCluster and, on NKP 2.18+,
    the NKPCluster that owns it.

    `name` and `namespace` are the canonical identity used in output, metrics
    and Redis keys. In practice all three resources share a name, but the
    individual refs are kept so nothing has to assume that.
    """

    name: str
    namespace: str
    labels: dict[str, str]

    #: Creation time as timezone-aware UTC. None if the API omitted it.
    created_at: datetime | None

    kommander: ResourceRef
    nkp: ResourceRef | None = None
    capi: ResourceRef | None = None

    #: The resource a delete would be issued against. None means nothing to do.
    target: ResourceRef | None = None

    #: True if `target` already carries a metadata.deletionTimestamp.
    deleting: bool = False

    #: True if the KommanderCluster marks this as the management cluster.
    is_management: bool = False

    def __str__(self) -> str:
        return f"{self.namespace}/{self.name}"

    @property
    def owner(self) -> str:
        """Value of the `owner` label, or UNKNOWN_OWNER."""
        return self.labels.get("owner", UNKNOWN_OWNER)

    @property
    def expires_label(self) -> str | None:
        """Raw value of the `expires` label, if set."""
        return self.labels.get("expires")


@dataclass
class Verdict:
    """The outcome of evaluating a cluster against the deletion criteria."""

    state: ClusterState

    #: Set only when state is FOR_DELETION.
    reason: DeletionReason | None = None

    #: Human-readable explanation. For display only — never branch on this.
    detail: str = ""

    #: When the cluster expires, if that could be determined. UTC.
    expires_at: datetime | None = None

    #: Validation errors found on the cluster's labels, if any.
    label_errors: list[str] = field(default_factory=list)

    @property
    def should_delete(self) -> bool:
        """True if a delete should be issued for this cluster."""
        return self.state.is_deletable

    def elapsed_percentage(self, created_at: datetime | None, now: datetime) -> float:
        """
        How far through its lifetime the cluster is, as a percentage.

        Clusters marked for deletion are always 100%: they are due now,
        regardless of what fraction of any timer has elapsed.

        Args:
            created_at: The cluster's creation time (UTC).
            now: The current time (UTC).

        Returns:
            A value between 0 and 100. Returns 0 when there is no usable
            expiry to measure against.
        """
        if self.should_delete:
            return 100.0

        if not created_at or not self.expires_at:
            return 0.0

        total = (self.expires_at - created_at).total_seconds()
        if total <= 0:
            return 100.0

        elapsed = (now - created_at).total_seconds()
        return max(0.0, min(100.0, (elapsed / total) * 100))


@dataclass
class ClusterStatus:
    """A cluster together with the decision made about it."""

    cluster: Cluster
    verdict: Verdict

    @property
    def should_delete(self) -> bool:
        """True if a delete should be issued for this cluster."""
        return self.verdict.should_delete

    @property
    def state(self) -> ClusterState:
        """The cluster's state, for convenience when grouping."""
        return self.verdict.state

    def elapsed_percentage(self, now: datetime) -> float:
        """How far through its lifetime this cluster is, as a percentage."""
        return self.verdict.elapsed_percentage(self.cluster.created_at, now)
