#!/usr/bin/env python3
"""
Command-line entry point.

This module is deliberately only click wiring: option declarations and
delegation. Each command's logic lives in its own module alongside this one.
"""

from __future__ import annotations

import logging

import click
from colorama import Fore, Style, init

from ..core.config import ConfigManager
from .collect_analytics import execute_collect_analytics_command
from .delete_clusters import execute_delete_clusters_command
from .list_clusters import execute_list_clusters_command
from .notify import execute_notify_command
from .options import (
    RedisSettings,
    SlackSettings,
    common_options,
    grace_option,
    namespace_option,
    notification_backend_option,
    redis_options,
    slack_options,
)
from .serve import execute_serve_command

init()


def _configure_logging(debug: bool = False):
    """
    Route library logging to the console.

    Library modules log rather than print, so that the web server and the CLI
    can each present messages their own way. For the CLI, plain messages on
    stderr sit alongside click's own output without competing with it.
    """
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(levelname)s: %(message)s",
    )


@click.group()
@click.version_option()
@click.option("--verbose", is_flag=True, help="Enable debug logging")
def cli(verbose):
    """NKP Cluster Cleaner - Delete NKP clusters based on label criteria."""
    _configure_logging(debug=verbose)


#
# List
#
@cli.command()
@common_options
@namespace_option
@click.option(
    "--no-exclusions",
    envvar="NO_EXCLUSIONS",
    is_flag=True,
    help="Skip showing excluded clusters (only show clusters for deletion)",
)
@grace_option("will not be considered for deletion")
def list_clusters(kubeconfig, config, namespace, no_exclusions, grace):
    """List NKP clusters that match deletion criteria."""
    execute_list_clusters_command(
        kubeconfig=kubeconfig,
        config=config,
        namespace=namespace,
        no_exclusions=no_exclusions,
        grace=grace,
    )


#
# Delete
#
@cli.command()
@common_options
@namespace_option
@click.option(
    "--delete",
    envvar="DELETE",
    is_flag=True,
    help="Actually delete clusters (default: dry-run mode)",
)
@grace_option("will not be deleted")
@notification_backend_option
@slack_options
@redis_options
def delete_clusters(
    kubeconfig, config, namespace, delete, grace, notify_backend, **kwargs
):
    """Delete NKP clusters that match deletion criteria."""
    execute_delete_clusters_command(
        kubeconfig=kubeconfig,
        config=config,
        namespace=namespace,
        delete=delete,
        grace=grace,
        notify_backend=notify_backend,
        slack=SlackSettings.from_kwargs(**kwargs),
    )


#
# Notify
#
@cli.command()
@common_options
@namespace_option
@click.option(
    "--warning-threshold",
    envvar="WARNING_THRESHOLD",
    default=80,
    type=int,
    help="Warning threshold percentage (0-100) of time elapsed (default: 80)",
)
@click.option(
    "--critical-threshold",
    envvar="CRITICAL_THRESHOLD",
    default=95,
    type=int,
    help="Critical threshold percentage (0-100) of time elapsed (default: 95)",
)
@grace_option("will not receive notifications")
@notification_backend_option
@slack_options
@redis_options
def notify(
    kubeconfig,
    config,
    namespace,
    warning_threshold,
    critical_threshold,
    grace,
    notify_backend,
    **kwargs,
):
    """Send notifications for clusters approaching deletion."""
    execute_notify_command(
        kubeconfig=kubeconfig,
        config=config,
        namespace=namespace,
        warning_threshold=warning_threshold,
        critical_threshold=critical_threshold,
        grace=grace,
        notify_backend=notify_backend,
        redis=RedisSettings.from_kwargs(**kwargs),
        slack=SlackSettings.from_kwargs(**kwargs),
    )


#
# Example config
#
@cli.command()
@click.argument("output_file", type=click.Path())
def generate_config(output_file):
    """Generate an example configuration file."""
    ConfigManager().save_example_config(output_file)
    click.echo(
        f"{Fore.GREEN}Example configuration saved to {output_file}{Style.RESET_ALL}"
    )


#
# Web server
#
@cli.command()
@common_options
@click.option(
    "--host",
    envvar="HOST",
    default="127.0.0.1",
    help="Host to bind to (default: 127.0.0.1)",
)
@click.option(
    "--port", envvar="PORT", default=8080, help="Port to bind to (default: 8080)"
)
@click.option("--debug", envvar="DEBUG", is_flag=True, help="Enable debug mode")
@click.option(
    "--prefix",
    envvar="PREFIX",
    default="",
    help="URL prefix for all routes (e.g., /foo for /foo/clusters)",
)
@grace_option("will be excluded from the web UI")
@redis_options
@click.option(
    "--no-redis",
    envvar="NO_REDIS",
    is_flag=True,
    help="Do not connect to Redis and disable notification history/analytics",
)
def serve(kubeconfig, config, host, port, debug, prefix, grace, no_redis, **kwargs):
    """Start the web server for the cluster cleaner UI."""
    execute_serve_command(
        kubeconfig=kubeconfig,
        config=config,
        host=host,
        port=port,
        debug=debug,
        prefix=prefix,
        grace=grace,
        redis=RedisSettings.from_kwargs(**kwargs),
        no_redis=no_redis,
    )


#
# Analytics
#
@cli.command()
@common_options
@click.option(
    "--keep-days",
    envvar="KEEP_DAYS",
    default=90,
    type=int,
    help="Number of days of analytics data to retain (default: 90)",
)
@click.option(
    "--debug",
    envvar="DEBUG",
    is_flag=True,
    help="Enable debug output during collection",
)
@redis_options
def collect_analytics(kubeconfig, config, keep_days, debug, **kwargs):
    """Collect analytics snapshot for historical tracking and reporting."""
    execute_collect_analytics_command(
        kubeconfig=kubeconfig,
        config=config,
        keep_days=keep_days,
        debug=debug,
        redis=RedisSettings.from_kwargs(**kwargs),
    )


if __name__ == "__main__":
    cli()
