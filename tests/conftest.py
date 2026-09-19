"""
Shared fixtures for the test suite. Object builders live in factories.py.

Most of the suite mocks at a seam — a MagicMock CustomObjectsApi, a faked
analytics service. The fixtures below exist for the tests that deliberately do
not: `mock_api` serves the real Kubernetes wire protocol over a socket, and
`fake_redis` serves the real Redis protocol in memory, so the transport, the
connection setup and the API coordinates are all exercised rather than assumed.
"""

from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import fakeredis
import mock_k8s_api
import pytest
import redis as redis_module

from nkp_cluster_cleaner.core.config import ConfigManager
from nkp_cluster_cleaner.core.settings import RedisSettings

#: Deletion criteria the mock estate's expected states are stated in terms of,
#: so tests against it must load this file rather than a default, ruleless
#: ConfigManager. Deliberately not the config.yaml at the repository root: that
#: one is gitignored as an operator's own file, and so is absent in CI.
CRITERIA_CONFIG = str(Path(__file__).resolve().parent / "fixtures" / "config.yaml")


@pytest.fixture
def config_manager():
    """A ConfigManager with no rules loaded (the default, permissive state)."""
    return ConfigManager()


@pytest.fixture
def owner_label_config(tmp_path):
    """A ConfigManager requiring an `owner` label and protecting `*-prod-*`."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "protected_cluster_patterns:\n"
        "- .*-prod-.*\n"
        "excluded_namespace_patterns:\n"
        "- ^default$\n"
        "extra_labels:\n"
        "- name: owner\n"
        "  description: Cluster owner identifier\n"
        "- name: cost_centre\n"
        '  regex: "^([0-9]+)$"\n'
    )
    return ConfigManager(str(config_file))


@pytest.fixture(scope="session")
def criteria_config():
    """Path to the config file the mock estate's expected states assume."""
    return CRITERIA_CONFIG


#
# Mock Kubernetes API
#


class MockCluster:
    """
    A mock Kubernetes API running in this process, and a kubeconfig reaching it.

    Every response is recorded, so a test can assert on what the tool asked the
    API to do. That is the only way to prove a dry run wrote nothing: an
    assertion that no delete was *reported* would still pass if the request was
    made and refused.
    """

    def __init__(self, server: ThreadingHTTPServer, kubeconfig: Path):
        self._server = server
        self.kubeconfig = str(kubeconfig)

    @property
    def requests(self) -> list[tuple[str, str]]:
        """Every (method, path) served since the last clear_requests()."""
        return list(self._server.RequestHandlerClass.requests)

    def clear_requests(self):
        """Forget the recorded requests, so one test only sees its own."""
        self._server.RequestHandlerClass.requests.clear()

    def paths(self, method: str) -> list[str]:
        """Paths requested with the given HTTP method."""
        return [path for verb, path in self.requests if verb == method]

    def shutdown(self):
        """Stop serving and release the port."""
        self._server.shutdown()
        self._server.server_close()


def _recording_respond(self, body: dict, code: int):
    """Record the request, then respond as the mock normally would."""
    type(self).requests.append((self.command, self.path))
    mock_k8s_api.MockApiHandler.respond(self, body, code)


def _start_mock_api(
    kubeconfig: Path, missing=(), nkp_version: str = "v2.18.0"
) -> MockCluster:
    """
    Start a mock Kubernetes API on an ephemeral port.

    Args:
        kubeconfig: Where to write a kubeconfig pointing at the new server.
        missing: (group, version, plural) tuples to serve 404 for, simulating a
            CRD that is not installed.
        nkp_version: Version the fake KommanderCore reports.

    Returns:
        A handle on the running server.
    """
    handler = type(
        "RecordingMockApiHandler",
        (mock_k8s_api.MockApiHandler,),
        {
            # Subclassed rather than configured in place: MockApiHandler keeps
            # its store in class attributes, so two servers in one process
            # would otherwise share — and overwrite — each other's fixtures.
            "store": mock_k8s_api.build_store(nkp_version),
            "missing": set(missing),
            "verbose": False,
            "requests": [],
            "respond": _recording_respond,
        },
    )

    # Port 0 so the tests never collide with a mock server a developer is
    # already running on the script's default port.
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    mock_k8s_api.write_kubeconfig(kubeconfig, "127.0.0.1", server.server_address[1])
    return MockCluster(server, kubeconfig)


@pytest.fixture(scope="session")
def mock_api(tmp_path_factory) -> MockCluster:
    """A mock NKP 2.18+ management cluster: the NKPCluster CRD is present."""
    cluster = _start_mock_api(tmp_path_factory.mktemp("mock-api") / "kubeconfig")
    yield cluster
    cluster.shutdown()


@pytest.fixture(scope="session")
def legacy_mock_api(tmp_path_factory) -> MockCluster:
    """A pre-2.18 cluster: NKPCluster 404s, so the CAPI path is exercised."""
    cluster = _start_mock_api(
        tmp_path_factory.mktemp("legacy-mock-api") / "kubeconfig",
        missing={mock_k8s_api.NKP},
    )
    yield cluster
    cluster.shutdown()


@pytest.fixture
def recorded(mock_api) -> MockCluster:
    """The mock cluster, with its request log cleared for this test."""
    mock_api.clear_requests()
    return mock_api


#
# Redis
#


#: The INFO fields the tool reads, and nothing more. fakeredis implements no
#: INFO command at all, and get_database_stats() catches the resulting error, so
#: without this its whole success path would sit untested behind an `except`.
FAKE_REDIS_INFO = {
    "redis_version": "7.4.0-fake",
    "used_memory_human": "1.00M",
    "used_memory_peak_human": "1.50M",
    "connected_clients": 1,
    "uptime_in_days": 0,
}


def install_fake_redis(monkeypatch) -> RedisSettings:
    """
    Redirect every Redis connection in the process to an in-memory server.

    Patched at `redis.Redis` rather than at `build_redis_client`, so the real
    connection options, the startup ping and the error handling in
    storage/client.py are all still exercised.

    Exposed as a function as well as the fixture below, so a test module can
    install it for a wider scope with `pytest.MonkeyPatch.context()` when
    building a fixture store is too expensive to repeat per test.

    Args:
        monkeypatch: The patcher whose lifetime the redirection should follow.

    Returns:
        The settings to pass to anything that opens a connection. Host and port
        are ignored, but they appear in output the CLI prints.
    """
    server = fakeredis.FakeServer()

    def connect(**options):
        client = fakeredis.FakeRedis(
            server=server,
            db=options.get("db", 0),
            decode_responses=options.get("decode_responses", True),
        )
        client.info = lambda *_args, **_kwargs: dict(FAKE_REDIS_INFO)
        return client

    monkeypatch.setattr(redis_module, "Redis", connect)
    return RedisSettings(host="fake-redis", port=6379)


@pytest.fixture
def fake_redis(monkeypatch) -> RedisSettings:
    """An in-memory Redis for the duration of one test."""
    return install_fake_redis(monkeypatch)
