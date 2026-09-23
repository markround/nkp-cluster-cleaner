"""
Tests for the commands themselves, run end to end against the mock cluster.

The CLI is the tool as an operator meets it, and was until now the only layer
with no tests at all: option wiring, the envvar names the Helm chart depends
on, the tables, and - most consequentially - whether a dry run is really dry.
That last one is checked against the mock server's request log rather than
against what the command prints, since a delete that was issued and refused
would still report as a failure rather than as a write.

Commands are invoked through click's CliRunner, so this covers the argument
parsing and the exit codes as well as the output.
"""

from __future__ import annotations

import json
import re
import socket

import mock_k8s_api
import pytest
from click.testing import CliRunner

from nkp_cluster_cleaner.cli.main import cli
from nkp_cluster_cleaner.core.config import ConfigManager
from nkp_cluster_cleaner.core.models import ClusterState

pytestmark = pytest.mark.integration

#: Fixtures the tool should list, and those it should skip.
LISTED = [f for f in mock_k8s_api.FIXTURES if f["state"]]
UNLISTED = [f for f in mock_k8s_api.FIXTURES if not f["state"]]

#: Which table each state is rendered under by list-clusters.
SECTION_FOR_STATE = {
    "for_deletion": "for_deletion",
    "deleting": "deleting",
    "management": "excluded",
    "protected": "excluded",
    "in_grace": "excluded",
    "active": "excluded",
    "no_target": "excluded",
}

#: Every environment variable the CLI reads. The tests clear all of them: the
#: Helm chart drives the tool entirely through envvars, so a developer's shell
#: - or mise.toml, which sets REDIS_HOST - would otherwise leak in and change
#: what is being tested.
CLI_ENVVARS = [
    "KUBECONFIG",
    "CONFIG",
    "NAMESPACE",
    "GRACE",
    "DELETE",
    "NO_EXCLUSIONS",
    "WARNING_THRESHOLD",
    "CRITICAL_THRESHOLD",
    "NOTIFY_BACKEND",
    "SLACK_TOKEN",
    "SLACK_CHANNEL",
    "SLACK_USERNAME",
    "SLACK_ICON_EMOJI",
    "REDIS_HOST",
    "REDIS_PORT",
    "REDIS_DB",
    "REDIS_USERNAME",
    "REDIS_PASSWORD",
    "HOST",
    "PORT",
    "DEBUG",
    "PREFIX",
    "NO_REDIS",
    "KEEP_DAYS",
]


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """Run every command with none of the CLI's environment variables set."""
    for name in CLI_ENVVARS:
        monkeypatch.delenv(name, raising=False)


#
# Helpers
#

#: colorama only strips escape codes from the stream it wrapped at import time,
#: which is not the one CliRunner installs, so they arrive in the output raw.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

#: A grid-table data row. Cluster names are lowercase, which is what
#: distinguishes a row from the "| Cluster Name |" header above it.
_ROW = re.compile(r"^\|\s*([a-z0-9][\w.-]*)\s*\|")

#: The heading above each table list-clusters prints.
_HEADINGS = (
    ("for_deletion", re.compile(r"^Found \d+ clusters for deletion:")),
    ("deleting", re.compile(r"^\d+ clusters are currently being deleted:")),
    ("excluded", re.compile(r"^Found \d+ excluded clusters:")),
)


def run(*args) -> tuple[int, str]:
    """
    Invoke the CLI.

    Returns:
        The exit code, and the output with colour codes stripped.
    """
    result = CliRunner().invoke(cli, list(args))
    return result.exit_code, _ANSI.sub("", result.output)


def rows(text: str) -> dict[str, list[str]]:
    """Every grid-table row in the text, as {cluster name: [cell, ...]}."""
    found = {}
    for line in text.splitlines():
        if _ROW.match(line):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            found[cells[0]] = cells
    return found


def sections(text: str) -> dict[str, str]:
    """Split list-clusters output into {section name: the text under it}."""
    collected: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        for name, heading in _HEADINGS:
            if heading.match(line):
                current = name
                collected[current] = []
                break
        else:
            if current:
                collected[current].append(line)
    return {name: "\n".join(lines) for name, lines in collected.items()}


def section_of(text: str, name: str) -> str | None:
    """Which section a cluster was listed under, or None if it was not."""
    for section, body in sections(text).items():
        if name in rows(body):
            return section
    return None


def closed_port() -> int:
    """A port nothing is listening on, for the unreachable-cluster tests."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


#
# list-clusters
#


@pytest.fixture(scope="module")
def listing(mock_api, criteria_config) -> str:
    """The full output of a plain list-clusters run, produced once."""
    code, output = run(
        "list-clusters",
        "--kubeconfig",
        mock_api.kubeconfig,
        "--config",
        criteria_config,
    )
    assert code == 0, output
    return output


class TestListClusters:
    def test_reports_the_deletion_api(self, listing):
        assert "Deletion API: nkpcluster" in listing

    @pytest.mark.parametrize("fixture", LISTED, ids=[f["name"] for f in LISTED])
    def test_cluster_is_listed_under_the_right_heading(self, listing, fixture):
        assert (
            section_of(listing, fixture["name"]) == SECTION_FOR_STATE[fixture["state"]]
        )

    @pytest.mark.parametrize(
        "fixture",
        [f for f in LISTED if SECTION_FOR_STATE[f["state"]] == "excluded"],
        ids=[f["name"] for f in LISTED if SECTION_FOR_STATE[f["state"]] == "excluded"],
    )
    def test_excluded_cluster_names_its_state(self, listing, fixture):
        """The excluded table mixes states, so it carries a State column."""
        row = rows(sections(listing)["excluded"])[fixture["name"]]
        assert row[4] == ClusterState(fixture["state"]).label

    @pytest.mark.parametrize("fixture", UNLISTED, ids=[f["name"] for f in UNLISTED])
    def test_attached_cluster_appears_nowhere(self, listing, fixture):
        assert fixture["name"] not in listing

    def test_deletion_candidates_name_their_target(self, listing):
        """
        The operator needs to see whether a delete would remove an NKPCluster
        or a CAPI Cluster, since the two tear down very differently.
        """
        targets = {
            name: row[4]
            for name, row in rows(sections(listing)["for_deletion"]).items()
        }
        assert set(targets.values()) == {"NKPCluster"}

    def test_no_exclusions_drops_the_excluded_table(self, mock_api, criteria_config):
        code, output = run(
            "list-clusters",
            "--kubeconfig",
            mock_api.kubeconfig,
            "--config",
            criteria_config,
            "--no-exclusions",
        )
        assert code == 0
        assert "excluded clusters" not in output
        assert "demo-expired" in output
        assert "dev-scratch" not in output

    def test_namespace_limits_the_listing(self, mock_api, criteria_config):
        code, output = run(
            "list-clusters",
            "--kubeconfig",
            mock_api.kubeconfig,
            "--config",
            criteria_config,
            "--namespace",
            "team-alpha",
        )
        assert code == 0
        assert "namespace 'team-alpha'" in output
        assert set(rows(output)) == {"alpha-lab", "alpha-renamed"}

    def test_grace_period_is_announced_and_applied(self, mock_api, criteria_config):
        code, output = run(
            "list-clusters",
            "--kubeconfig",
            mock_api.kubeconfig,
            "--config",
            criteria_config,
            "--grace",
            "1h",
        )
        assert code == 0
        assert "Grace period: 1h" in output
        assert section_of(output, "demo-fresh") == "excluded"
        assert rows(sections(output)["excluded"])["demo-fresh"][4] == "In grace"

    def test_without_a_config_nothing_is_protected(self, mock_api):
        """
        The protection rules come from the config file, so dropping it must
        move every protected cluster into the deletion queue. This is the check
        that --config is really being read rather than defaulted somewhere.
        """
        code, output = run("list-clusters", "--kubeconfig", mock_api.kubeconfig)
        assert code == 0
        assert section_of(output, "dev-scratch") == "for_deletion"
        assert section_of(output, "workload-1") == "for_deletion"


class TestListClustersFailures:
    def test_missing_kubeconfig_is_rejected_before_connecting(self, tmp_path):
        code, output = run("list-clusters", "--kubeconfig", str(tmp_path / "nope"))
        assert code == 2
        assert "does not exist" in output

    def test_missing_config_file_is_rejected(self, mock_api, tmp_path):
        code, output = run(
            "list-clusters",
            "--kubeconfig",
            mock_api.kubeconfig,
            "--config",
            str(tmp_path / "nope.yaml"),
        )
        assert code == 2
        assert "does not exist" in output

    def test_unreachable_cluster_aborts_with_an_error(self, tmp_path):
        kubeconfig = mock_k8s_api.write_kubeconfig(
            tmp_path / "kubeconfig", "127.0.0.1", closed_port()
        )
        code, output = run("list-clusters", "--kubeconfig", str(kubeconfig))
        assert code == 1
        assert "Error:" in output


#
# delete-clusters
#


class TestDeleteClusters:
    def test_dry_run_is_the_default_and_writes_nothing(self, recorded, criteria_config):
        code, output = run(
            "delete-clusters",
            "--kubeconfig",
            recorded.kubeconfig,
            "--config",
            criteria_config,
        )

        assert code == 0
        assert "[DRY RUN MODE]" in output
        assert "Dry run completed. 6 clusters would be deleted." in output
        # The point of the whole exercise: not "no delete was reported", but no
        # delete was sent.
        assert [verb for verb, _ in recorded.requests if verb != "GET"] == []

    def test_dry_run_lists_the_same_clusters_list_clusters_does(
        self, recorded, criteria_config, listing
    ):
        _, output = run(
            "delete-clusters",
            "--kubeconfig",
            recorded.kubeconfig,
            "--config",
            criteria_config,
        )
        assert set(rows(output)) == set(rows(sections(listing)["for_deletion"]))

    def test_delete_issues_one_request_per_nkp_cluster(self, recorded, criteria_config):
        code, output = run(
            "delete-clusters",
            "--kubeconfig",
            recorded.kubeconfig,
            "--config",
            criteria_config,
            "--delete",
        )

        assert code == 0
        expected = {
            f"/apis/clusters.nkp.nutanix.com/v1alpha1/namespaces"
            f"/{f['namespace']}/nkpclusters/{f['name']}"
            for f in LISTED
            if f["state"] == "for_deletion"
        }
        assert set(recorded.paths("DELETE")) == expected

    def test_rejected_deletes_are_reported_not_swallowed(
        self, recorded, criteria_config
    ):
        """The mock refuses every write, so all six deletions must fail loudly."""
        code, output = run(
            "delete-clusters",
            "--kubeconfig",
            recorded.kubeconfig,
            "--config",
            criteria_config,
            "--delete",
        )
        assert code == 0
        assert "Deletion requested for 0 clusters." in output
        assert "6 clusters failed to delete." in output

    def test_namespace_limits_what_would_be_deleted(self, recorded, criteria_config):
        code, output = run(
            "delete-clusters",
            "--kubeconfig",
            recorded.kubeconfig,
            "--config",
            criteria_config,
            "--namespace",
            "team-alpha",
            "--delete",
        )
        assert code == 0
        assert recorded.paths("DELETE") == [
            "/apis/clusters.nkp.nutanix.com/v1alpha1/namespaces"
            "/team-alpha/nkpclusters/alpha-lab"
        ]

    def test_clusters_already_being_deleted_are_skipped(
        self, recorded, criteria_config
    ):
        _, output = run(
            "delete-clusters",
            "--kubeconfig",
            recorded.kubeconfig,
            "--config",
            criteria_config,
        )
        assert "Skipping 1 clusters already being deleted" in output

    def test_unsupported_backend_aborts_before_any_request(
        self, recorded, criteria_config
    ):
        code, output = run(
            "delete-clusters",
            "--kubeconfig",
            recorded.kubeconfig,
            "--config",
            criteria_config,
            "--notify-backend",
            "carrier-pigeon",
        )
        assert code == 1
        assert "Unsupported notification backend 'carrier-pigeon'" in output
        assert recorded.requests == []

    def test_slack_backend_requires_a_token(self, recorded, criteria_config):
        code, output = run(
            "delete-clusters",
            "--kubeconfig",
            recorded.kubeconfig,
            "--config",
            criteria_config,
            "--notify-backend",
            "slack",
            "--slack-channel",
            "#clusters",
        )
        assert code == 1
        assert "--slack-token is required" in output


#
# notify
#


class TestNotify:
    def test_clusters_due_for_deletion_are_critical(self, mock_api, criteria_config):
        code, output = run(
            "notify", "--kubeconfig", mock_api.kubeconfig, "--config", criteria_config
        )

        assert code == 0
        assert "CRITICAL: 6 clusters" in output
        assert "• Critical notifications: 6" in output
        assert "• Warning notifications: 0" in output

    def test_lowering_the_warning_threshold_catches_a_live_cluster(
        self, mock_api, criteria_config
    ):
        """beta-sandbox is 9h into a 12h life, so 70% catches it and 80% does not."""
        code, output = run(
            "notify",
            "--kubeconfig",
            mock_api.kubeconfig,
            "--config",
            criteria_config,
            "--warning-threshold",
            "70",
        )

        assert code == 0
        assert "• Warning notifications: 1" in output
        assert "beta-sandbox" in output

    def test_protected_and_deleting_clusters_are_never_alerted_on(
        self, mock_api, criteria_config
    ):
        code, output = run(
            "notify",
            "--kubeconfig",
            mock_api.kubeconfig,
            "--config",
            criteria_config,
            "--warning-threshold",
            "1",
        )
        assert code == 0
        for quiet in ("production-api", "demo-deleting", "nkp-mgmt-cluster"):
            assert quiet not in output

    def test_inverted_thresholds_abort(self, mock_api, criteria_config):
        code, output = run(
            "notify",
            "--kubeconfig",
            mock_api.kubeconfig,
            "--config",
            criteria_config,
            "--warning-threshold",
            "95",
            "--critical-threshold",
            "80",
        )
        assert code == 1
        assert "Warning threshold must be less than critical threshold" in output

    def test_unsupported_backend_aborts(self, mock_api, criteria_config):
        code, output = run(
            "notify",
            "--kubeconfig",
            mock_api.kubeconfig,
            "--config",
            criteria_config,
            "--notify-backend",
            "smoke-signal",
        )
        assert code == 1
        assert "Unsupported notification backend" in output


class TestNotifyWithHistory:
    """
    Delivery through a backend, and the Redis record that stops the same alert
    going out on every run of the CronJob.

    Only the HTTP call to Slack is stubbed; the dedup, the cleanup and the
    history records are the real ones, against the in-memory Redis.
    """

    @pytest.fixture
    def slack(self, monkeypatch) -> list[dict]:
        """Capture what would have been posted to Slack."""
        from nkp_cluster_cleaner.notifications import manager

        posted = []

        class Accepted:
            status_code = 200

            @staticmethod
            def json():
                return {"ok": True}

        def post(_url, data=None, **_kwargs):
            posted.append(json.loads(data))
            return Accepted()

        monkeypatch.setattr(manager.requests, "post", post)
        return posted

    def notify(self, mock_api, criteria_config, fake_redis, *extra) -> tuple[int, str]:
        """Run notify with the slack backend against the in-memory history."""
        return run(
            "notify",
            "--kubeconfig",
            mock_api.kubeconfig,
            "--config",
            criteria_config,
            "--notify-backend",
            "slack",
            "--slack-token",
            "xoxb-test",
            # Bare, without a leading "#": the command prints "#{channel}", so
            # passing "#clusters" here would have it report "##clusters".
            "--slack-channel",
            "clusters",
            "--redis-host",
            fake_redis.host,
            *extra,
        )

    def test_alerts_are_delivered_and_recorded(
        self, mock_api, criteria_config, fake_redis, slack
    ):
        code, output = self.notify(mock_api, criteria_config, fake_redis)

        assert code == 0, output
        assert f"Connected to notification history at {fake_redis}" in output
        assert "Sent critical notification for 6 clusters to #clusters" in output
        assert len(slack) == 1
        assert slack[0]["channel"] == "clusters"

    def test_the_same_alert_is_not_sent_twice(
        self, mock_api, criteria_config, fake_redis, slack
    ):
        """
        The whole point of the history: the CronJob runs on a schedule, and
        without this every run would re-alert on the same six clusters.
        """
        self.notify(mock_api, criteria_config, fake_redis)
        slack.clear()

        code, output = self.notify(mock_api, criteria_config, fake_redis)

        assert code == 0
        assert "Filtered out 6 notifications (already sent)" in output
        assert "all 6 clusters have already been notified" in output
        assert slack == []

    def test_a_cluster_back_in_compliance_is_forgotten(
        self, mock_api, criteria_config, fake_redis, slack
    ):
        """
        Otherwise a cluster alerted on for a missing label, then fixed, would
        stay marked as notified and never be alerted on when it did expire.
        """
        from nkp_cluster_cleaner.storage.notification_history import NotificationHistory

        self.notify(mock_api, criteria_config, fake_redis)

        history = NotificationHistory(fake_redis)
        history.mark_as_notified("long-gone", "team-alpha", "warning")

        code, output = self.notify(mock_api, criteria_config, fake_redis)

        assert code == 0
        assert "Cleaned up notifications for 1 compliant clusters" in output
        assert not history.has_been_notified("long-gone", "team-alpha", "warning")

    def test_a_slack_failure_aborts_rather_than_recording_a_send(
        self, mock_api, criteria_config, fake_redis, monkeypatch
    ):
        from nkp_cluster_cleaner.notifications import manager
        from nkp_cluster_cleaner.storage.notification_history import NotificationHistory

        class Rejected:
            status_code = 500
            text = "internal error"

        monkeypatch.setattr(manager.requests, "post", lambda *a, **k: Rejected())

        code, output = self.notify(mock_api, criteria_config, fake_redis)

        assert code == 1
        assert "Failed to send notifications: HTTP 500" in output
        assert NotificationHistory(fake_redis).get_active_notification_count() == 0


#
# collect-analytics
#


class TestCollectAnalytics:
    def test_snapshot_is_collected_and_summarised(
        self, mock_api, criteria_config, fake_redis
    ):
        code, output = run(
            "collect-analytics",
            "--kubeconfig",
            mock_api.kubeconfig,
            "--config",
            criteria_config,
            "--redis-host",
            fake_redis.host,
        )

        assert code == 0, output
        assert f"Total clusters found: {len(LISTED)}" in output
        assert "Clusters for deletion: 6" in output
        assert "Clusters being deleted: 1" in output
        assert f"Protected clusters: {len(LISTED) - 6}" in output
        assert "Deletion API: nkpcluster" in output
        assert "Total snapshots in Redis: 1" in output
        assert "Redis memory usage: 1.00M" in output

    def test_the_snapshot_is_actually_in_redis(
        self, mock_api, criteria_config, fake_redis
    ):
        from nkp_cluster_cleaner.storage.client import build_redis_client

        run(
            "collect-analytics",
            "--kubeconfig",
            mock_api.kubeconfig,
            "--config",
            criteria_config,
            "--redis-host",
            fake_redis.host,
        )

        client = build_redis_client(fake_redis)
        keys = client.zrange("analytics:snapshots:index", 0, -1)
        assert len(keys) == 1

        snapshot = json.loads(client.get(keys[0]))
        assert snapshot["cluster_counts"]["total"] == len(LISTED)
        assert snapshot["collection_metadata"]["nkp_version"] == "v2.18.0"

    def test_retention_is_passed_through_as_a_ttl(
        self, mock_api, criteria_config, fake_redis
    ):
        from nkp_cluster_cleaner.storage.client import build_redis_client

        run(
            "collect-analytics",
            "--kubeconfig",
            mock_api.kubeconfig,
            "--config",
            criteria_config,
            "--redis-host",
            fake_redis.host,
            "--keep-days",
            "7",
        )

        client = build_redis_client(fake_redis)
        key = client.zrange("analytics:snapshots:index", 0, -1)[0]
        assert 0 < client.ttl(key) <= 7 * 24 * 3600


#
# generate-config and serve
#


class TestGenerateConfig:
    def test_the_generated_file_loads(self, tmp_path):
        target = tmp_path / "generated.yaml"
        code, output = run("generate-config", str(target))

        assert code == 0
        assert str(target) in output
        # Loading it is the real assertion: an example config the tool cannot
        # read is worse than none.
        assert ConfigManager(str(target)).get_criteria() is not None


class TestServe:
    def test_options_reach_the_server(self, monkeypatch, mock_api, criteria_config):
        """
        serve blocks forever, so the server itself is stubbed. What is being
        checked is the wiring: every option arriving where it should, spelled
        the way run_server expects it.
        """
        from nkp_cluster_cleaner.web import app as web_app

        captured = {}
        monkeypatch.setattr(web_app, "run_server", lambda **kw: captured.update(kw))

        code, _ = run(
            "serve",
            "--kubeconfig",
            mock_api.kubeconfig,
            "--config",
            criteria_config,
            "--host",
            "0.0.0.0",
            "--port",
            "9999",
            "--prefix",
            "/cleaner",
            "--grace",
            "4h",
            "--no-redis",
            "--redis-host",
            "somewhere",
        )

        assert code == 0
        assert captured["host"] == "0.0.0.0"
        assert captured["port"] == 9999
        assert captured["url_prefix"] == "/cleaner"
        assert captured["grace_period"] == "4h"
        assert captured["no_redis"] is True
        assert captured["kubeconfig_path"] == mock_api.kubeconfig
        assert captured["config_path"] == criteria_config
        assert captured["redis"].host == "somewhere"

    def test_a_failure_to_start_aborts(self, monkeypatch):
        from nkp_cluster_cleaner.web import app as web_app

        def explode(**_kwargs):
            raise OSError("address already in use")

        monkeypatch.setattr(web_app, "run_server", explode)

        code, output = run("serve")
        assert code == 1
        assert "Error starting server: address already in use" in output


class TestEnvironmentContract:
    def test_every_declared_envvar_is_isolated_by_these_tests(self):
        """
        Catches an envvar added to options.py without being added above, which
        would leave a test silently reading the developer's environment.
        """
        declared = {
            param.envvar
            for command in cli.commands.values()
            for param in command.params
            if param.envvar
        }
        assert declared <= set(CLI_ENVVARS), sorted(declared - set(CLI_ENVVARS))
