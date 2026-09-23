#!/usr/bin/env python3
"""
A read-only mock Kubernetes API server for exercising nkp-cluster-cleaner.

Serves a fixed set of minimal NKPCluster, KommanderCluster and CAPI Cluster
objects - just the fields discovery and the criteria actually read - so the
whole tool can be run end to end without an NKP management cluster:

    ./misc/mock_k8s_api.py
    nkp-cluster-cleaner list-clusters --kubeconfig misc/mock.kubeconfig \\
        --config tests/fixtures/config.yaml

The fixtures deliberately cover every ClusterState the tool can produce, plus
the two joins that are easy to get wrong (ownerReference-based and name-based)
and the attached clusters that must be skipped. Each one carries the state the
tool should reach for it, so the fixtures are a test oracle rather than just a
pile of YAML: run with --scenarios to print them. tests/test_k8s_over_http.py
asserts the tool reaches those states, and tests/test_cli.py asserts each one
is reported under the right heading, both against this server run in-process.

Writes are refused with a 403: this only ever pretends to be an API you read.
Everything is stdlib, so it runs without the package installed.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# API coordinates, mirrored from core.models so this script stays standalone.
KOMMANDER = ("kommander.mesosphere.io", "v1beta1", "kommanderclusters")
NKP = ("clusters.nkp.nutanix.com", "v1alpha1", "nkpclusters")
CAPI = ("cluster.x-k8s.io", "v1beta1", "clusters")
KOMMANDER_CORE = ("dkp.d2iq.io", "v1alpha1", "kommandercores")
CRONJOBS = ("batch", "v1", "cronjobs")
JOBS = ("batch", "v1", "jobs")
PODS = ("", "v1", "pods")

#: Plural -> Kind, for the List responses the client deserialises.
KINDS = {
    "kommanderclusters": "KommanderCluster",
    "nkpclusters": "NKPCluster",
    "clusters": "Cluster",
    "kommandercores": "KommanderCore",
    "cronjobs": "CronJob",
    "jobs": "Job",
    "pods": "Pod",
}

MANAGEMENT_NAMESPACE = "kommander"
MANAGEMENT_LABEL = "kommander.d2iq.io/host"
WORKSPACE_NAMESPACE = "kommander-default-workspace"

#: Deletion criteria the fixtures' expected states are stated in terms of.
#: Tracked in the repository, unlike the config.yaml at the root, which is
#: gitignored as an operator's own file and so is absent in a fresh clone.
CRITERIA_CONFIG = "tests/fixtures/config.yaml"

#: Namespaces served by /api/v1/namespaces.
NAMESPACES = [
    "default",
    MANAGEMENT_NAMESPACE,
    WORKSPACE_NAMESPACE,
    "team-alpha",
    "team-beta",
    "customer-prod",
]


def ts(offset: timedelta = timedelta(0)) -> str:
    """Build an RFC3339 timestamp relative to now, as the API formats them."""
    return (datetime.now(UTC) + offset).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# Object builders
# --------------------------------------------------------------------------


def kommander_cluster(
    name,
    namespace,
    labels=None,
    created=None,
    attached=False,
    owner_name=None,
    management=False,
    capi_name=None,
):
    """
    Build a KommanderCluster.

    Args:
        name: Resource name.
        namespace: Resource namespace.
        labels: Metadata labels.
        created: creationTimestamp; defaults to 30 days ago.
        attached: If True, omit spec.clusterRef.capiCluster, which is the only
            reliable marker of an attached cluster.
        owner_name: Name of the owning NKPCluster. None means no ownerReference,
            so discovery has to fall back to matching on name.
        management: If True, set the management-cluster label.
        capi_name: CAPI cluster name, if it differs from `name`.
    """
    labels = dict(labels or {})
    if management:
        labels[MANAGEMENT_LABEL] = "true"

    metadata = {
        "name": name,
        "namespace": namespace,
        "uid": f"kc-{namespace}-{name}",
        "labels": labels,
        "creationTimestamp": created or ts(timedelta(days=-30)),
    }
    if owner_name:
        metadata["ownerReferences"] = [
            {
                "apiVersion": f"{NKP[0]}/{NKP[1]}",
                "kind": "NKPCluster",
                "name": owner_name,
                "uid": f"nkp-{namespace}-{owner_name}",
            }
        ]

    obj = {
        "apiVersion": f"{KOMMANDER[0]}/{KOMMANDER[1]}",
        "kind": "KommanderCluster",
        "metadata": metadata,
        "spec": {},
        "status": {"phase": "Attached" if attached else "Joined"},
    }
    if not attached:
        obj["spec"]["clusterRef"] = {
            "capiCluster": {"name": capi_name or name, "namespace": namespace}
        }
    return obj


def nkp_cluster(name, namespace, labels=None, created=None, deleting=False):
    """Build an NKPCluster (NKP 2.18+), optionally mid-teardown."""
    metadata = {
        "name": name,
        "namespace": namespace,
        "uid": f"nkp-{namespace}-{name}",
        "labels": dict(labels or {}),
        "creationTimestamp": created or ts(timedelta(days=-30)),
        "finalizers": [
            "clusters.nkp.nutanix.com/capicluster-cleanup",
            "clusters.nkp.nutanix.com/kommandercluster-cleanup",
        ],
    }
    if deleting:
        metadata["deletionTimestamp"] = ts(timedelta(minutes=-5))

    return {
        "apiVersion": f"{NKP[0]}/{NKP[1]}",
        "kind": "NKPCluster",
        "metadata": metadata,
        "spec": {"version": "v2.18.0"},
        "status": {
            "phase": "Deleting" if deleting else "Reconciled",
            "platformVersion": "v2.18.0",
            "kommanderClusterRef": {"name": name, "namespace": namespace},
            "capiClusterRef": {"name": name, "namespace": namespace},
        },
    }


def capi_cluster(name, namespace, created=None, deleting=False):
    """Build a minimal CAPI Cluster."""
    metadata = {
        "name": name,
        "namespace": namespace,
        "uid": f"capi-{namespace}-{name}",
        "creationTimestamp": created or ts(timedelta(days=-30)),
    }
    if deleting:
        metadata["deletionTimestamp"] = ts(timedelta(minutes=-5))

    return {
        "apiVersion": f"{CAPI[0]}/{CAPI[1]}",
        "kind": "Cluster",
        "metadata": metadata,
        "spec": {},
        "status": {"phase": "Deleting" if deleting else "Provisioned"},
    }


def kommander_core(version):
    """Build the KommanderCore that reports the NKP version."""
    return {
        "apiVersion": f"{KOMMANDER_CORE[0]}/{KOMMANDER_CORE[1]}",
        "kind": "KommanderCore",
        "metadata": {"name": "kommandercore", "namespace": MANAGEMENT_NAMESPACE},
        "spec": {},
        "status": {"version": version},
    }


# --------------------------------------------------------------------------
# The scheduled jobs the web UI's CronJob views walk
# --------------------------------------------------------------------------

#: The CronJob -> Job -> Pod chain the scheduled jobs view follows, and the
#: names it joins them on. The web UI only shows a pod's logs once it has
#: walked ownerReferences back to a CronJob carrying our app label, so the
#: whole chain has to be served for the log endpoint to be reachable.
CRONJOB_NAME = "nkp-cluster-cleaner-analytics"
JOB_NAME = f"{CRONJOB_NAME}-29344800"
POD_NAME = f"{JOB_NAME}-x7k2p"
CONTAINER_NAME = "cluster-cleaner"
CONTAINER_IMAGE = "nkp-cluster-cleaner:mock"

#: The pod spec the Job and CronJob carry. Nothing reads it, but the typed
#: client refuses to deserialise a Job without one.
POD_TEMPLATE = {
    "template": {
        "spec": {
            "restartPolicy": "OnFailure",
            "containers": [{"name": CONTAINER_NAME, "image": CONTAINER_IMAGE}],
        }
    }
}

#: What the pod's container wrote. Non-ASCII on purpose: the bullets are how a
#: log that was decoded as anything other than UTF-8 gives itself away.
POD_LOG = (
    "2026-09-23T10:40:31.658077816Z Collecting analytics snapshot...\n"
    "2026-09-23T10:40:31.681275428Z Analytics snapshot collected successfully!\n"
    "2026-09-23T10:40:31.681294738Z Summary:\n"
    "2026-09-23T10:40:31.681299572Z   \u2022 Total clusters found: 2\n"
    "2026-09-23T10:40:31.681446552Z   \u2022 Clusters for deletion: 0\n"
    "2026-09-23T10:40:31.681521219Z   \u2022 Label compliance: 50.0%\n"
)


def owner_ref(kind, name, api_version):
    """Build the ownerReference the tool walks from pod to job to cronjob."""
    return {
        "apiVersion": api_version,
        "kind": kind,
        "name": name,
        "uid": f"uid-{name}",
        "controller": True,
    }


def cleaner_cronjob():
    """Build the CronJob the scheduled jobs view lists, with our app label."""
    return {
        "apiVersion": "batch/v1",
        "kind": "CronJob",
        "metadata": {
            "name": CRONJOB_NAME,
            "namespace": MANAGEMENT_NAMESPACE,
            "uid": f"uid-{CRONJOB_NAME}",
            "creationTimestamp": ts(timedelta(days=-7)),
            "labels": {"app": "nkp-cluster-cleaner"},
        },
        "spec": {
            "schedule": "*/10 * * * *",
            "suspend": False,
            "successfulJobsHistoryLimit": 3,
            "failedJobsHistoryLimit": 1,
            "jobTemplate": {"spec": POD_TEMPLATE},
        },
        "status": {"lastScheduleTime": ts(timedelta(minutes=-10))},
    }


def cleaner_job():
    """Build the completed Job that CronJob's last run created."""
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": JOB_NAME,
            "namespace": MANAGEMENT_NAMESPACE,
            "uid": f"uid-{JOB_NAME}",
            "creationTimestamp": ts(timedelta(minutes=-10)),
            "labels": {"job-name": JOB_NAME},
            "ownerReferences": [owner_ref("CronJob", CRONJOB_NAME, "batch/v1")],
        },
        "spec": POD_TEMPLATE,
        "status": {
            "startTime": ts(timedelta(minutes=-10)),
            "completionTime": ts(timedelta(minutes=-9)),
            "succeeded": 1,
            "conditions": [{"type": "Complete", "status": "True"}],
        },
    }


def cleaner_pod():
    """Build the Pod that Job ran, which is what holds the logs."""
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": POD_NAME,
            "namespace": MANAGEMENT_NAMESPACE,
            "uid": f"uid-{POD_NAME}",
            "creationTimestamp": ts(timedelta(minutes=-10)),
            "labels": {"job-name": JOB_NAME},
            "ownerReferences": [owner_ref("Job", JOB_NAME, "batch/v1")],
        },
        "spec": POD_TEMPLATE["template"]["spec"],
        "status": {
            "phase": "Succeeded",
            "startTime": ts(timedelta(minutes=-10)),
            "containerStatuses": [
                {
                    "name": CONTAINER_NAME,
                    "image": CONTAINER_IMAGE,
                    "imageID": f"docker://{CONTAINER_IMAGE}",
                    "ready": False,
                    "restartCount": 0,
                    "state": {
                        "terminated": {"reason": "Completed", "exitCode": 0},
                    },
                }
            ],
        },
    }


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

#: Each entry describes one cluster and the state the tool should reach for it,
#: assuming CRITERIA_CONFIG and no grace period. None of this is served; it is
#: the expected-results half of the fixture.
#:
#: `state` mirrors a core.models.ClusterState value, spelled out rather than
#: imported so this script stays standalone. None means the cluster should not
#: be listed at all. `reason` mirrors DeletionReason and is set only where the
#: state is "for_deletion". `grace_1h` overrides `state` for a run with
#: --grace 1h. `expect` is prose for --scenarios, and nothing asserts on it.
FIXTURES = [
    # -- The management cluster. Protected whatever else is true of it. -----
    {
        "name": "nkp-mgmt-cluster",
        "namespace": MANAGEMENT_NAMESPACE,
        "management": True,
        "labels": {},
        "state": "management",
        "expect": "Management - never deleted, and it has no labels at all",
    },
    # -- Compliant, in the default workspace. ------------------------------
    {
        "name": "demo-compliant",
        "namespace": WORKSPACE_NAMESPACE,
        "labels": {"expires": "30d", "owner": "mdr"},
        # A stale owner on the NKPCluster, to prove the KommanderCluster's
        # labels win when both carry the same key.
        "nkp_labels": {"expires": "30d", "owner": "stale-value"},
        "created": timedelta(days=-2),
        "state": "active",
        "expect": "Active - expires in ~28d, owner resolves to 'mdr'",
    },
    # -- No labels at all: the commonest reason for deletion. ---------------
    {
        "name": "demo-unlabelled",
        "namespace": WORKSPACE_NAMESPACE,
        "labels": {},
        "created": timedelta(days=-9),
        "state": "for_deletion",
        "reason": "missing_expires_label",
        "expect": "For deletion - missing 'expires' label",
    },
    # -- Labelled but past its expiry. --------------------------------------
    {
        "name": "demo-expired",
        "namespace": WORKSPACE_NAMESPACE,
        "labels": {"expires": "1d", "owner": "mdr"},
        "created": timedelta(days=-5),
        "state": "for_deletion",
        "reason": "expired",
        "expect": "For deletion - expired 4 days ago",
    },
    # -- Has expires but not the required extra label from the config. ------
    {
        "name": "demo-no-owner",
        "namespace": WORKSPACE_NAMESPACE,
        "labels": {"expires": "30d"},
        "created": timedelta(days=-1),
        "state": "for_deletion",
        "reason": "missing_required_label",
        "expect": "For deletion - missing required label 'owner'",
    },
    # -- Unparseable expires value. -----------------------------------------
    {
        "name": "demo-bad-expires",
        "namespace": WORKSPACE_NAMESPACE,
        "labels": {"expires": "next tuesday", "owner": "mdr"},
        "created": timedelta(days=-3),
        "state": "for_deletion",
        "reason": "invalid_expires_format",
        "expect": "For deletion - invalid 'expires' format",
    },
    # -- Brand new and unlabelled: the case --grace exists for. -------------
    {
        "name": "demo-fresh",
        "namespace": WORKSPACE_NAMESPACE,
        "labels": {},
        "created": timedelta(minutes=-10),
        "state": "for_deletion",
        "reason": "missing_expires_label",
        "grace_1h": "in_grace",
        "expect": "For deletion, but In grace with --grace 1h",
    },
    # -- Teardown already under way. ----------------------------------------
    {
        "name": "demo-deleting",
        "namespace": WORKSPACE_NAMESPACE,
        "labels": {"expires": "1d", "owner": "mdr"},
        "created": timedelta(days=-4),
        "deleting": True,
        "state": "deleting",
        "expect": "Deleting - target carries a deletionTimestamp",
    },
    # -- KommanderCluster with nothing behind it. ---------------------------
    {
        "name": "demo-orphaned",
        "namespace": WORKSPACE_NAMESPACE,
        "labels": {"expires": "1d", "owner": "mdr"},
        "created": timedelta(days=-12),
        "nkp": False,
        "capi": False,
        "state": "no_target",
        "expect": "No target - expired, but neither NKPCluster nor CAPI Cluster exists",
    },
    # -- Attached, with the NKPCluster wrapper NKP 2.18 gives it. -----------
    {
        "name": "attached-vsphere",
        "namespace": WORKSPACE_NAMESPACE,
        "labels": {},
        "attached": True,
        "state": None,
        "expect": "Not listed - attached, despite having an NKPCluster wrapper",
    },
    # -- Expired lab cluster in a team namespace. ---------------------------
    {
        "name": "alpha-lab",
        "namespace": "team-alpha",
        "labels": {"expires": "7d", "owner": "alice"},
        "created": timedelta(days=-8),
        "state": "for_deletion",
        "reason": "expired",
        "expect": "For deletion - expired 1 day ago",
    },
    # -- NKPCluster named differently, so only the ownerReference joins them.
    #    Its labels live only on the NKPCluster, which also exercises the
    #    label merge in the other direction.
    {
        "name": "alpha-renamed",
        "namespace": "team-alpha",
        "labels": {},
        "nkp_name": "alpha-renamed-h7k2p",
        "nkp_labels": {"expires": "90d", "owner": "alice"},
        "created": timedelta(days=-20),
        "state": "active",
        "expect": "Active - joined via ownerReference, labels inherited from the NKPCluster",
    },
    # -- Short-lived sandbox, part way through its life. --------------------
    {
        "name": "beta-sandbox",
        "namespace": "team-beta",
        "labels": {"expires": "12h", "owner": "bob"},
        "created": timedelta(hours=-9),
        "state": "active",
        "expect": "Active - ~75% elapsed, good for the UI progress bar",
    },
    # -- Protected by name: the config lists `workload-1` literally. --------
    {
        "name": "workload-1",
        "namespace": "team-beta",
        "labels": {},
        "created": timedelta(days=-40),
        "state": "protected",
        "expect": "Protected - matches the 'workload-1' name pattern",
    },
    # -- Protected by the `^production-.*` name pattern. --------------------
    {
        "name": "production-api",
        "namespace": "team-beta",
        "labels": {"expires": "1h", "owner": "bob"},
        "created": timedelta(days=-40),
        "state": "protected",
        "expect": "Protected - matches '^production-.*' despite being long expired",
    },
    # -- Attached the pre-2.18 way: no NKPCluster wrapper at all. -----------
    {
        "name": "attached-eks",
        "namespace": "team-beta",
        "labels": {"expires": "1d"},
        "attached": True,
        "nkp": False,
        "state": None,
        "expect": "Not listed - attached, with no NKPCluster at all",
    },
    # -- Protected by the `.*-prod$` namespace pattern. ---------------------
    {
        "name": "edge-cluster",
        "namespace": "customer-prod",
        "labels": {},
        "created": timedelta(days=-60),
        "state": "protected",
        "expect": "Protected - namespace matches '.*-prod$'",
    },
    # -- Protected by the `^default$` namespace pattern. --------------------
    {
        "name": "dev-scratch",
        "namespace": "default",
        "labels": {},
        "created": timedelta(days=-15),
        "state": "protected",
        "expect": "Protected - namespace matches '^default$'",
    },
]


def build_store(nkp_version: str) -> dict[tuple[str, str, str], list[dict]]:
    """
    Turn the fixtures into the resource lists the server hands out.

    Returns:
        A dict keyed by (group, version, plural).
    """
    kommanders, nkps, capis = [], [], []

    for f in FIXTURES:
        name = f["name"]
        namespace = f["namespace"]
        created = ts(f.get("created", timedelta(days=-30)))
        labels = f.get("labels", {})
        nkp_name = f.get("nkp_name", name)
        attached = f.get("attached", False)
        deleting = f.get("deleting", False)
        has_nkp = f.get("nkp", True)
        has_capi = f.get("capi", True) and not attached

        kommanders.append(
            kommander_cluster(
                name=name,
                namespace=namespace,
                labels=labels,
                created=created,
                attached=attached,
                # Only reference an NKPCluster that actually exists, so the
                # fixtures never trip discovery's dangling-owner warning.
                owner_name=nkp_name if has_nkp else None,
                management=f.get("management", False),
            )
        )

        if has_nkp:
            nkps.append(
                nkp_cluster(
                    name=nkp_name,
                    namespace=namespace,
                    labels=f.get("nkp_labels", labels),
                    # A couple of minutes before the KommanderCluster, as in
                    # reality: the NKPCluster is the cluster's true birth.
                    created=ts(
                        f.get("created", timedelta(days=-30)) - timedelta(minutes=2)
                    ),
                    deleting=deleting,
                )
            )

        if has_capi:
            capis.append(
                capi_cluster(
                    name=name,
                    namespace=namespace,
                    created=created,
                    deleting=deleting,
                )
            )

    return {
        KOMMANDER: kommanders,
        NKP: nkps,
        CAPI: capis,
        KOMMANDER_CORE: [kommander_core(nkp_version)],
        CRONJOBS: [cleaner_cronjob()],
        JOBS: [cleaner_job()],
        PODS: [cleaner_pod()],
    }


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------


def status_body(code: int, reason: str, message: str) -> dict:
    """Build a Kubernetes Status object, which is what the client expects."""
    return {
        "kind": "Status",
        "apiVersion": "v1",
        "metadata": {},
        "status": "Failure",
        "message": message,
        "reason": reason,
        "code": code,
    }


class MockApiHandler(BaseHTTPRequestHandler):
    """Serves the resource store over the subset of the API the tool uses."""

    protocol_version = "HTTP/1.1"

    # Set by serve().
    store: dict[tuple[str, str, str], list[dict]] = {}
    missing: set[tuple[str, str, str]] = set()
    verbose: bool = True

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's naming.
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        parts = [p for p in parsed.path.split("/") if p]

        try:
            body, code = self.route(parts, query)
        # A mock that 500s is easier to debug than one that drops the connection.
        except Exception as e:
            body, code = status_body(500, "InternalError", str(e)), 500

        self.respond(body, code)

    def do_POST(self):  # noqa: N802
        self.refuse_write()

    def do_PUT(self):  # noqa: N802
        self.refuse_write()

    def do_PATCH(self):  # noqa: N802
        self.refuse_write()

    def do_DELETE(self):  # noqa: N802
        self.refuse_write()

    def refuse_write(self):
        """Reject every mutating verb; this server is a read-only fixture."""
        self.drain_body()
        self.respond(
            status_body(
                403,
                "Forbidden",
                f"{self.command} {self.path} denied: this mock API is read-only",
            ),
            403,
        )

    def drain_body(self):
        """Read and discard any request body, so keep-alive stays in sync."""
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)

    def route(self, parts: list[str], query: dict) -> tuple[dict | str, int]:
        """
        Map a request path to a response body and status code.

        Args:
            parts: Non-empty path segments.
            query: Parsed query string.

        Returns:
            A (body, status code) pair.
        """
        if parts == ["version"]:
            return {
                "major": "1",
                "minor": "31",
                "gitVersion": "v1.31.0-mock",
                "platform": "linux/amd64",
            }, 200

        # Core API: namespaces, and the pod endpoints the CronJob views touch.
        if parts[:2] == ["api", "v1"]:
            return self.route_core(parts[2:], query)

        # Custom and built-in group APIs: /apis/{group}/{version}/...
        if parts[0] == "apis" and len(parts) >= 3:
            return self.route_group(parts[1], parts[2], parts[3:], query)

        return status_body(404, "NotFound", f"Unhandled path /{'/'.join(parts)}"), 404

    def route_core(self, parts: list[str], query: dict) -> tuple[dict | str, int]:
        """Handle /api/v1/... requests."""
        # /api/v1/namespaces
        if parts == ["namespaces"]:
            items = [
                {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {"name": ns, "uid": f"ns-{ns}"},
                    "status": {"phase": "Active"},
                }
                for ns in NAMESPACES
            ]
            return self.list_response("v1", "Namespace", items, query), 200

        # /api/v1/namespaces/{ns}/{resource}[/{name}[/log]]
        if len(parts) >= 3 and parts[0] == "namespaces":
            namespace, resource = parts[1], parts[2]
            name = parts[3] if len(parts) > 3 else None

            if resource == "pods":
                return self.route_pods(namespace, name, parts[4:], query)

            if name is None:
                kind = KINDS.get(resource, resource.rstrip("s").capitalize())
                return self.list_response("v1", kind, [], query), 200
            return (
                status_body(404, "NotFound", f"No {resource} named {name}"),
                404,
            )

        return status_body(404, "NotFound", f"Unhandled core path {parts}"), 404

    def route_pods(
        self, namespace: str, name: str | None, rest: list[str], query: dict
    ) -> tuple[dict | str, int]:
        """
        Handle /api/v1/namespaces/{ns}/pods[/{name}[/log]] requests.

        Args:
            namespace: Namespace from the path.
            name: Pod name, or None for a list request.
            rest: Path segments after the pod name, e.g. ["log"].
            query: Parsed query string.

        Returns:
            A (body, status code) pair, where the body is a plain string for
            the log sub-resource and a JSON-serialisable object otherwise.
        """
        pods = [
            p
            for p in self.store.get(PODS, [])
            if p["metadata"]["namespace"] == namespace
        ]

        if name is None:
            # Only the one selector the tool sends, job-name=, is understood.
            for selector in query.get("labelSelector", []):
                key, _, value = selector.partition("=")
                pods = [
                    p
                    for p in pods
                    if (p["metadata"].get("labels") or {}).get(key) == value
                ]
            return self.list_response("v1", "Pod", pods, query), 200

        pod = next((p for p in pods if p["metadata"]["name"] == name), None)
        if pod is None:
            return status_body(404, "NotFound", f"pods {name} not found"), 404

        # The log sub-resource is text/plain, not JSON - the one endpoint in
        # this server that does not hand back an API object.
        if rest == ["log"]:
            return POD_LOG, 200

        if not rest:
            return pod, 200

        return status_body(404, "NotFound", f"Unhandled pod path {rest}"), 404

    def route_group(
        self, group: str, version: str, rest: list[str], query: dict
    ) -> tuple[dict, int]:
        """Handle /apis/{group}/{version}/... requests."""
        # Namespaced: /namespaces/{ns}/{plural}[/{name}]
        if len(rest) >= 3 and rest[0] == "namespaces":
            namespace, plural = rest[1], rest[2]
            name = rest[3] if len(rest) > 3 else None
        elif len(rest) >= 1:
            namespace, plural = None, rest[0]
            name = rest[1] if len(rest) > 1 else None
        else:
            return status_body(404, "NotFound", "No resource in path"), 404

        key = (group, version, plural)

        # A CRD that is deliberately not installed, e.g. --legacy.
        if key in self.missing:
            return (
                status_body(
                    404,
                    "NotFound",
                    f"the server could not find the requested resource ({plural})",
                ),
                404,
            )

        items = self.store.get(key)
        if items is None:
            # An unknown group is served as empty rather than 404: a 404 means
            # "CRD not installed", which the tool treats as a real signal.
            items = []

        if namespace:
            items = [i for i in items if i["metadata"]["namespace"] == namespace]

        if name:
            for item in items:
                if item["metadata"]["name"] == name:
                    return item, 200
            return status_body(404, "NotFound", f"{plural} {name} not found"), 404

        kind = KINDS.get(plural, plural.rstrip("s").capitalize())
        return self.list_response(f"{group}/{version}", kind, items, query), 200

    @staticmethod
    def list_response(api_version: str, kind: str, items: list[dict], query: dict):
        """Wrap items in a List object, honouring ?limit=."""
        limit = query.get("limit")
        if limit:
            try:
                items = items[: int(limit[0])]
            except ValueError:
                pass
        return {
            "apiVersion": api_version,
            "kind": f"{kind}List",
            "metadata": {"resourceVersion": "1"},
            "items": items,
        }

    def respond(self, body: dict | str, code: int):
        """Send a response: JSON for API objects, text/plain for pod logs."""
        if isinstance(body, str):
            payload = body.encode("utf-8")
            content_type = "text/plain; charset=utf-8"
        else:
            payload = json.dumps(body).encode()
            content_type = "application/json"
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        """Log to stderr in a form that shows what the tool asked for."""
        if self.verbose:
            sys.stderr.write(f"  {self.command:6} {self.path}\n")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

KUBECONFIG_TEMPLATE = """\
apiVersion: v1
kind: Config
current-context: mock-nkp
clusters:
- name: mock-nkp
  cluster:
    server: http://{host}:{port}
contexts:
- name: mock-nkp
  context:
    cluster: mock-nkp
    user: mock-nkp
users:
- name: mock-nkp
  user:
    token: mock-token
"""


def write_kubeconfig(path: Path, host: str, port: int) -> Path:
    """Write a kubeconfig pointing at this server and return its path."""
    # 0.0.0.0 is a bind address, not something a client can connect to.
    connect_host = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
    path.write_text(KUBECONFIG_TEMPLATE.format(host=connect_host, port=port))
    return path


def print_scenarios():
    """Print each fixture and the state the tool should reach for it."""
    refs = {f["name"]: f"{f['namespace']}/{f['name']}" for f in FIXTURES}
    ref_width = max(len(r) for r in refs.values())
    state_width = max(len(f["state"] or "-") for f in FIXTURES)

    print(f"\nFixtures (expected results with {CRITERIA_CONFIG}, no grace):\n")
    for f in FIXTURES:
        state = f["state"] or "-"
        print(
            f"  {refs[f['name']]:<{ref_width}}  {state:<{state_width}}  {f['expect']}"
        )
    print("\n  '-' means the cluster should not be listed at all.")
    print("  These states are asserted by tests/test_k8s_over_http.py.\n")


def main():
    parser = argparse.ArgumentParser(
        description="Read-only mock Kubernetes API serving NKP cluster fixtures.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  ./misc/mock_k8s_api.py &\n"
            "  nkp-cluster-cleaner list-clusters \\\n"
            f"      --kubeconfig misc/mock.kubeconfig --config {CRITERIA_CONFIG}\n"
        ),
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind address")
    parser.add_argument("--port", type=int, default=8001, help="Bind port")
    parser.add_argument(
        "--kubeconfig",
        type=Path,
        default=Path(__file__).parent / "mock.kubeconfig",
        help="Where to write the generated kubeconfig",
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="Serve 404 for NKPCluster, simulating NKP older than 2.18 so the "
        "CAPI deletion strategy is exercised instead",
    )
    parser.add_argument(
        "--no-kommander-crd",
        action="store_true",
        help="Serve 404 for KommanderCluster, simulating a cluster without "
        "Kommander installed",
    )
    parser.add_argument(
        "--nkp-version", default="v2.18.0", help="Version reported by KommanderCore"
    )
    parser.add_argument(
        "--scenarios",
        action="store_true",
        help="Print the fixtures and their expected verdicts, then exit",
    )
    parser.add_argument("--quiet", action="store_true", help="Do not log requests")
    args = parser.parse_args()

    if args.scenarios:
        print_scenarios()
        return

    MockApiHandler.store = build_store(args.nkp_version)
    MockApiHandler.missing = set()
    if args.legacy:
        MockApiHandler.missing.add(NKP)
    if args.no_kommander_crd:
        MockApiHandler.missing.add(KOMMANDER)
    MockApiHandler.verbose = not args.quiet

    kubeconfig = write_kubeconfig(args.kubeconfig, args.host, args.port)
    counts = {
        plural: len(items) for (_g, _v, plural), items in MockApiHandler.store.items()
    }

    server = ThreadingHTTPServer((args.host, args.port), MockApiHandler)
    print(f"Mock Kubernetes API on http://{args.host}:{args.port} (read-only)")
    print(
        f"  mode:       {'legacy / CAPI' if args.legacy else 'NKP 2.18+ / NKPCluster'}"
    )
    print("  serving:    " + ", ".join(f"{n} {p}" for p, n in counts.items()))
    if MockApiHandler.missing:
        print("  404s for:   " + ", ".join(p for _g, _v, p in MockApiHandler.missing))
    print(f"  kubeconfig: {kubeconfig}")
    print()
    print("Try:")
    common = f"--kubeconfig {kubeconfig} --config {CRITERIA_CONFIG}"
    for command in (
        f"nkp-cluster-cleaner list-clusters {common}",
        f"nkp-cluster-cleaner list-clusters {common} --grace 1h",
        f"nkp-cluster-cleaner delete-clusters {common}",
        f"nkp-cluster-cleaner serve {common} --no-redis",
        f"{Path(__file__).name} --scenarios   # expected result per cluster",
    ):
        print(f"  {command}")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
