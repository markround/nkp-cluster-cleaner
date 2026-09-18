"""
Serve command implementation for the NKP Cluster Cleaner tool.
"""

from __future__ import annotations

import click
from colorama import Fore, Style

from .options import RedisSettings


def execute_serve_command(
    kubeconfig: str | None,
    config: str | None,
    host: str,
    port: int,
    debug: bool,
    prefix: str,
    grace: str | None,
    redis: RedisSettings,
    no_redis: bool,
):
    """
    Execute the serve command.

    Args:
        kubeconfig: Path to kubeconfig file.
        config: Path to configuration file.
        host: Address to bind to.
        port: Port to bind to.
        debug: Enable Flask debug mode.
        prefix: URL prefix for all routes.
        grace: Grace period for newly created clusters.
        redis: Where analytics and notification history live.
        no_redis: Run without Redis, disabling analytics and notifications.
    """
    # Imported here rather than at module scope so that `--help` and the other
    # commands do not pay for importing Flask.
    from ..web.app import run_server

    try:
        run_server(
            host=host,
            port=port,
            debug=debug,
            kubeconfig_path=kubeconfig,
            config_path=config,
            url_prefix=prefix,
            grace_period=grace,
            redis=redis,
            no_redis=no_redis,
        )
    except KeyboardInterrupt:
        click.echo(f"\n{Fore.YELLOW}Server stopped by user.{Style.RESET_ALL}")
    except Exception as e:
        click.echo(f"{Fore.RED}Error starting server: {e}{Style.RESET_ALL}")
        raise click.Abort() from e
