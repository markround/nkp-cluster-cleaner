"""
Shared click options, and the settings objects they produce.

The Helm chart drives this tool entirely through environment variables, so
every option here keeps its `envvar`. Changing one is a breaking change for
deployed charts.
"""

from __future__ import annotations

import click

from ..core.settings import RedisSettings, SlackSettings

# Re-exported so command modules can pull their options and the settings
# objects those options build from a single place.
__all__ = [
    "RedisSettings",
    "SlackSettings",
    "common_options",
    "grace_option",
    "namespace_option",
    "notification_backend_option",
    "redis_options",
    "slack_options",
]


def common_options(f):
    """Add the kubeconfig and config-file options."""
    f = click.option(
        "--kubeconfig",
        envvar="KUBECONFIG",
        type=click.Path(exists=True),
        help="Path to kubeconfig file (default: ~/.kube/config or $KUBECONFIG)",
    )(f)
    f = click.option(
        "--config",
        envvar="CONFIG",
        type=click.Path(exists=True),
        help="Path to configuration file for protection rules",
    )(f)
    return f


def namespace_option(f):
    """Add the namespace filter option."""
    return click.option(
        "--namespace",
        envvar="NAMESPACE",
        help="Limit operation to specific namespace (default: examine all namespaces)",
    )(f)


def grace_option(help_suffix: str):
    """
    Build the --grace option, with wording specific to the command.

    Args:
        help_suffix: How the grace period affects this particular command.

    Returns:
        A click option decorator.
    """
    return click.option(
        "--grace",
        envvar="GRACE",
        help=(
            "Grace period for newly created clusters (e.g., 1d, 4h, 2w, 1y). "
            f"Clusters younger than this {help_suffix}."
        ),
    )


def redis_options(f):
    """Add the Redis connection options."""
    f = click.option(
        "--redis-host",
        envvar="REDIS_HOST",
        default="redis",
        help="Redis host (default: redis)",
    )(f)
    f = click.option(
        "--redis-port",
        envvar="REDIS_PORT",
        default=6379,
        type=int,
        help="Redis port (default: 6379)",
    )(f)
    f = click.option(
        "--redis-db",
        envvar="REDIS_DB",
        default=0,
        type=int,
        help="Redis database number (default: 0)",
    )(f)
    f = click.option(
        "--redis-username",
        envvar="REDIS_USERNAME",
        help="Redis username for authentication",
    )(f)
    f = click.option(
        "--redis-password",
        envvar="REDIS_PASSWORD",
        help="Redis password for authentication",
    )(f)
    return f


def slack_options(f):
    """Add the Slack notification options."""
    f = click.option(
        "--slack-token",
        envvar="SLACK_TOKEN",
        help="Slack Bot User OAuth Token (required for slack backend)",
    )(f)
    f = click.option(
        "--slack-channel",
        envvar="SLACK_CHANNEL",
        help="Slack channel to send notifications to (required for slack backend)",
    )(f)
    f = click.option(
        "--slack-username",
        envvar="SLACK_USERNAME",
        default="NKP Cluster Cleaner",
        help="Username to display in Slack messages (default: NKP Cluster Cleaner)",
    )(f)
    f = click.option(
        "--slack-icon-emoji",
        envvar="SLACK_ICON_EMOJI",
        default=":broom:",
        help="Emoji icon for Slack messages (default: :broom:)",
    )(f)
    return f


def notification_backend_option(f):
    """Add the notification backend selector."""
    return click.option(
        "--notify-backend",
        envvar="NOTIFY_BACKEND",
        help="Notification backend to use (supported: slack)",
    )(f)
