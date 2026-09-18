"""
Builders for the Kubernetes objects under test.

Shaped to match what the API actually returns — see dev/nkpcluster.yaml and
dev/kommanderclusters.yaml for the real resources these are modelled on.
"""

from datetime import UTC, datetime, timedelta

from nkp_cluster_cleaner.core.models import (
    CAPI_GROUP,
    KOMMANDER_GROUP,
    MANAGEMENT_LABEL,
    NKP_GROUP,
    Cluster,
    capi_ref,
    kommander_ref,
    nkp_ref,
)

#: Namespace the management cluster's KommanderCluster lives in.
MANAGEMENT_NAMESPACE = "kommander"

#: Namespace most workload clusters land in by default.
WORKSPACE_NAMESPACE = "kommander-default-workspace"


def ts(offset: timedelta = timedelta(0)) -> str:
    """
    Build an RFC3339 timestamp relative to now, in the format the API returns.

    Args:
        offset: Shift from the current time. Negative values are in the past.

    Returns:
        Timestamp string such as "2026-09-11T15:20:08Z".
    """
    return (datetime.now(UTC) + offset).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_kommander_cluster(
    name="workload-1",
    namespace=WORKSPACE_NAMESPACE,
    labels=None,
    created=None,
    attached=False,
    capi_name=None,
    capi_namespace=None,
    owned_by_nkp=True,
    management=False,
):
    """
    Build a KommanderCluster object.

    Args:
        name: Resource name.
        namespace: Resource namespace.
        labels: Metadata labels; defaults to a compliant set.
        created: creationTimestamp; defaults to 30 days ago.
        attached: If True, omit spec.clusterRef.capiCluster so the cluster looks
            like an attached (externally managed) cluster.
        capi_name: CAPI cluster name; defaults to `name`.
        capi_namespace: CAPI cluster namespace; defaults to `namespace`.
        owned_by_nkp: If True, add the NKPCluster ownerReference NKP 2.18 sets.
        management: If True, add the management-cluster label.

    Returns:
        KommanderCluster dict as returned by the Kubernetes API.
    """
    if labels is None:
        labels = {"expires": "365d", "owner": "mdr"}
    labels = dict(labels)
    if management:
        labels[MANAGEMENT_LABEL] = "true"

    metadata = {
        "name": name,
        "namespace": namespace,
        "labels": labels,
        "creationTimestamp": created or ts(timedelta(days=-30)),
    }
    if owned_by_nkp:
        metadata["ownerReferences"] = [
            {
                "apiVersion": f"{NKP_GROUP}/v1alpha1",
                "kind": "NKPCluster",
                "name": name,
                "uid": "eb6d074e-a880-41f8-8eb0-bb1f07f66133",
            }
        ]

    kc = {
        "apiVersion": f"{KOMMANDER_GROUP}/v1beta1",
        "kind": "KommanderCluster",
        "metadata": metadata,
        "spec": {},
    }

    if not attached:
        kc["spec"]["clusterRef"] = {
            "capiCluster": {
                "name": capi_name or name,
                "namespace": capi_namespace or namespace,
            }
        }

    return kc


def make_nkp_cluster(
    name="workload-1",
    namespace=WORKSPACE_NAMESPACE,
    labels=None,
    created=None,
    deleting=False,
):
    """
    Build an NKPCluster object (NKP 2.18+).

    Args:
        name: Resource name.
        namespace: Resource namespace.
        labels: Metadata labels; defaults to a compliant set.
        created: creationTimestamp; defaults to 30 days ago.
        deleting: If True, set metadata.deletionTimestamp so the object looks
            like it is mid-teardown.

    Returns:
        NKPCluster dict as returned by the Kubernetes API.
    """
    if labels is None:
        labels = {"expires": "365d", "owner": "mdr"}

    metadata = {
        "name": name,
        "namespace": namespace,
        "labels": dict(labels),
        "creationTimestamp": created or ts(timedelta(days=-30)),
        "finalizers": [
            "clusters.nkp.nutanix.com/capicluster-cleanup",
            "clusters.nkp.nutanix.com/kommandercluster-cleanup",
        ],
    }
    if deleting:
        metadata["deletionTimestamp"] = ts(timedelta(minutes=-5))

    return {
        "apiVersion": f"{NKP_GROUP}/v1alpha1",
        "kind": "NKPCluster",
        "metadata": metadata,
        "spec": {"version": "v2.18.0"},
        "status": {
            "phase": "Reconciled",
            "platformVersion": "v2.18.0",
            "kommanderClusterRef": {"name": name, "namespace": namespace},
            "capiClusterRef": {"name": name, "namespace": namespace},
        },
    }


def make_capi_cluster(name="workload-1", namespace=WORKSPACE_NAMESPACE, deleting=False):
    """Build a minimal CAPI Cluster object."""
    metadata = {"name": name, "namespace": namespace}
    if deleting:
        metadata["deletionTimestamp"] = ts(timedelta(minutes=-5))
    return {
        "apiVersion": f"{CAPI_GROUP}/v1beta1",
        "kind": "Cluster",
        "metadata": metadata,
        "status": {"phase": "Provisioned"},
    }


#: Distinguishes "caller did not specify" from an explicit None, which is a
#: meaningful value here: it models an API object with no creationTimestamp.
UNSET = object()


def make_cluster(
    name="workload-1",
    namespace=WORKSPACE_NAMESPACE,
    labels=None,
    created=UNSET,
    deleting=False,
    is_management=False,
    has_target=True,
):
    """
    Build a domain Cluster directly, for testing the criteria in isolation.

    Args:
        name: Cluster name.
        namespace: Cluster namespace.
        labels: Labels; defaults to a compliant set.
        created: Creation time as an aware datetime. Defaults to 30 days ago;
            pass None explicitly to model a missing creationTimestamp.
        deleting: Whether teardown is already in progress.
        is_management: Whether this is the management cluster.
        has_target: Whether a deletion target was resolved.

    Returns:
        A Cluster instance.
    """
    if labels is None:
        labels = {"expires": "365d", "owner": "mdr"}

    return Cluster(
        name=name,
        namespace=namespace,
        labels=dict(labels),
        created_at=(
            datetime.now(UTC) - timedelta(days=30) if created is UNSET else created
        ),
        kommander=kommander_ref(name, namespace),
        nkp=nkp_ref(name, namespace) if has_target else None,
        capi=capi_ref(name, namespace),
        target=nkp_ref(name, namespace) if has_target else None,
        deleting=deleting,
        is_management=is_management,
    )
