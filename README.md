# nkp-cluster-cleaner

<p float="left">
  <img src="/docs/clusters.png" width="180">&nbsp; &nbsp; 
  <img src="/docs/rules.png" width="180">&nbsp; &nbsp; 
  <img src="/docs/cron.png" width="180">&nbsp; &nbsp; 
  <img src="/docs/analytics.png" width="180">
</p>
   
A simple yet comprehensive tool to automatically delete and report on Nutanix Kubernetes Platform (NKP) clusters that do not meet a specific criteria. Useful for cleaning up resources and managing costs in a lab/demo environment, similar to common "cloud cleaner" tools. Available as an [NKP Catalog Application](./docs/nkp.md), [Helm Chart](./charts/nkp-cluster-cleaner/README.md) and [Container Image](https://github.com/markround/nkp-cluster-cleaner/pkgs/container/nkp-cluster-cleaner).

![Platform](https://img.shields.io/badge/platform-Nutanix_NKP-blue)
![GitHub Actions Workflow Status](https://img.shields.io/github/actions/workflow/status/markround/nkp-cluster-cleaner/docker.yml)
![GitHub branch check runs](https://img.shields.io/github/check-runs/markround/nkp-cluster-cleaner/main)
![GitHub Release](https://img.shields.io/github/v/release/markround/nkp-cluster-cleaner)

## Features

- 🚀 Simple "one-click" catalog installation and tight integration with NKP features
- 📋 Flexible rulesets and custom criteria
- 🔔 Notifications (Slack currently supported, more to come)
- 📈 Trend analysis, compliance monitoring and historical data tracking
- 📊 Built-in web dashboard and administration console
- 🔥 Prometheus metrics, NKP monitoring integration and Grafana dashboard 
- 🖥️ Also runs as a standalone console application

> [!NOTE] 
> This is a personal project and is not supported/endorsed by, or otherwise connected to Nutanix

## Installation
See the documentation at [docs/nkp.md](./docs/nkp.md) for details on how to deploy the application as a NKP catalog application, running inside the NKP Management Cluster itself. This is the recommended way to run the application as it includes the scheduled tasks, web interface and analytics with no further configuration needed. 

You can however run the application from a Docker container or direct from the CLI. These options are discussed below.

## Strategy
- Any cluster without an `expires` label will be deleted.
- Any cluster that is older than the value specified in the `expires` label will be deleted. 
  - This label takes values of the format `n<unit>` , where unit is one of:
    - `h` - Hours
    - `d` - Days
    - `w` - Weeks
    - `y` - Years
  - For example, `12h` , `2d` , `1w`, `1y`
- A set of additional labels and acceptable regex patterns can be provided. Any cluster without matching labels will be deleted.
  - For example, the default [Helm Chart](./charts/nkp-cluster-cleaner/README.md) configuration defines a required `owner` label. Any cluster without an `owner` label will be deleted.

> [!NOTE]
> The default for both the CLI tool and the NKP Application is to run in "dry-run" mode, and will just show what _would_ be deleted. To actually delete the clusters you must pass in the `--delete` flag to the `delete-clusters` command, or explicitly enable the `cronjob.delete` value in the Helm chart / NKP application.

### Protected clusters
The management cluster is always excluded from deletion, and a configuration file can be provided that accepts a list of regex-based namespaces or cluster names that will be excluded. For example:

```yaml
excluded_namespace_patterns:
- ^default$
- .*-prod$
protected_cluster_patterns:
- ^production-.*
- .*-prod$
- critical-.*
```

### Extra Labels
In addition to the core `expires` label, any number of additional required labels can be defined in the configuration file. These are defined as a list with the following keys:

- `name` : Name of the label
- `description` : Optional description of the label
- `regex` : Optional regex to validate the label value against. If omitted, any value is accepted.

A cluster will be marked for deletion if:

- Any of these labels is not present,
- Or is in an incorrect format. 

Some examples provided in the example configuration file:

```yaml
extra_labels:
# Cluster owner. Note no regex is provided, so any value is accepted.
- description: Cluster owner identifier
  name: owner

# A numeric cost centre ID
- description: Numeric cost centre ID
  name: cost_centre
  regex: "^([0-9]+)$"

# A project ID
- description: Project identifier (alphanumeric with hyphens)
  name: project
  regex: "^[a-zA-Z0-9-]+$"

# An environment type which must be one of 4 values
- description: Environment type
  name: environment
  regex: "^(dev|test|staging|prod)$"
```

These can also be viewed in the Web UI, along with the other matching rules and list of clusters scheduled for deletion.

## General Usage
Although the preferred method of deployment and configuration is as an NKP application, you can still run the tool from the CLI or container image:

```
Usage: nkp-cluster-cleaner [OPTIONS] COMMAND [ARGS]...

  NKP Cluster Cleaner - Delete CAPI clusters based on label criteria.

Options:
  --version  Show the version and exit.
  --help     Show this message and exit.

Commands:
  collect-analytics  Collect analytics snapshot for historical tracking...
  delete-clusters    Delete CAPI clusters that match deletion criteria.
  generate-config    Generate an example configuration file.
  list-clusters      List CAPI clusters that match deletion criteria.
  serve              Start the web server for the cluster cleaner UI.
```

- You must pass in a valid `kubeconfig` context with admin privileges to the NKP management cluster. This can be done by e.g. setting the `KUBECONFIG` environment variable or using the `--kubeconfig` parameter to commands. 

- To pass in a custom configuration file, use the `--config /path/to/config.yaml` argument to any command. A sample configuration file can be created with `nkp-cluster-cleaner generate-config /path/to/config.yaml`.

### Environment variables

You can pass configuration values in using environment variables as well as CLI flags. Each variable accepted is simply the flag name, converted to uppercase and with dash characters changed to underscores:

| CLI flag | Environment variable equivalent |
| ---------|-------------------------------- |
| `--config` | `CONFIG` | 
| `--debug` | `DEBUG` | 
| `--delete` | `DELETE` | 
| `--host` | `HOST` | 
| `--keep-days` | `KEEP_DAYS` | 
| `--kubeconfig` | `KUBECONFIG` | 
| `--namespace` | `NAMESPACE` | 
| `--no-analytics` | `NO_ANALYTICS` | 
| `--no-exclusions` | `NO_EXCLUSIONS` | 
| `--port` | `PORT` | 
| `--prefix` | `PREFIX` | 
| `--redis-db` | `REDIS_DB` | 
| `--redis-host` | `REDIS_HOST` | 
| `--redis-port` | `REDIS_PORT` | 

### Web interface
There is a bundled web interface that displays the cluster deletion status, protection rules, analytics and general configuration. Start the built-in Flask-based webserver with the `serve` command that takes the usual arguments to specify port and bind host etc:

```
Usage: nkp-cluster-cleaner serve [OPTIONS]

  Start the web server for the cluster cleaner UI.

Options:
  --config PATH         Path to configuration file for protection rules
  --kubeconfig PATH     Path to kubeconfig file (default: ~/.kube/config or
                        $KUBECONFIG)
  --host TEXT           Host to bind to (default: 127.0.0.1)
  --port INTEGER        Port to bind to (default: 8080)
  --debug               Enable debug mode
  --prefix TEXT         URL prefix for all routes (e.g., /foo for
                        /foo/clusters)
  --redis-host TEXT     Redis host for analytics data (default: redis)
  --redis-port INTEGER  Redis port (default: 6379)
  --redis-db INTEGER    Redis database number (default: 0)
  --no-analytics        Disable analytics and do not connect to Redis
  --help                Show this message and exit.
```

### Analytics
The NKP Cluster Cleaner includes an analytics dashboard that provides historical tracking, trends analysis, and reporting capabilities. It uses a Redis-based data collector that creates periodic snapshots of cluster state. 

Data is collected by running `nkp-cluster-cleaner collect-analytics`. If you deploy the [NKP application](/docs/nkp.md) into your cluster, it will automatically configure a CronJob to collect data, along with a bundled [Valkey](https://valkey.io/) server. 

Historical data is stored with a configurable retention period. The default is to store data for 90 days, but you can change this by passing the `--keep-days` argument to the `collect-analytics` command. 

If you want to make use of an alternative Redis/Valkey service, you must provide connection details when running the `serve` or `collect-analytics` commands. They both accept the following arguments:

| Argument | Type | Description |
|----------|------|-------------|
|`--redis-host` | TEXT     | Redis host (default: redis) |
|`--redis-port` | INTEGER  | Redis port (default: 6379)  |
|`--redis-db`   | INTEGER  | Redis database number (default: 0) |

#### Prometheus Metrics
Prometheus metrics for all collected analytics data is exposed under the `/metrics` endpoint. A ServiceMonitor can be created using the Helm chart for automatic discovery and incorporation of data into the Prometheus stack used by NKP. 

A sample [Grafana dashboard](./charts/nkp-cluster-cleaner/grafana-dashboards/nkp-cluster-cleaner.json) is provided that can be integrated into the NKP Grafana stack. For more information, see the [Helm Chart](./charts/nkp-cluster-cleaner/README.md) and [NKP Application](./docs/nkp.md) documentation.

## Docker Usage
### Tags
- The container image can be pulled from GitHub Container Registry: `ghcr.io/markround/nkp-cluster-cleaner:<TAG>`
- Available tags are:
  - Branch (e.g. `main`, `feature/xxx`, etc.)
  - Release tag (e.g. `0.10.1`) 
  - Latest released version (e.g. `latest`)
- Full list on the [packages page](https://github.com/markround/nkp-cluster-cleaner/pkgs/container/nkp-cluster-cleaner)

### General
The `ENTRYPOINT` for the container is the application itself, so you only need to pass in the arguments. Any additional configuration files can be provided as volume mounts. For example, to list clusters with a custom configuration file and your default `kubeconfig` you'd run something like:

```bash
docker run --rm \
  -v ~/.kube/config:/app/config/kubeconfig:ro \
  -v ./my-config.yaml:/app/config/config.yaml:ro \
  ghcr.io/markround/nkp-cluster-cleaner:latest \
  list-clusters \
  --kubeconfig /app/config/kubeconfig \
  --config /app/config/config.yaml
```


## Installation / Development

```bash
pip install -r requirements.txt
pip install -e .
```
