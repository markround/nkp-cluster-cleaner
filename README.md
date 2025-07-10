# nkp-cluster-cleaner
<img src="/docs/webui.png" width="200">

A simple CLI tool (with optional web interface) to automatically delete Nutanix NKP clusters that do not meet a specific criteria. Useful for cleaning up resources in a lab/demo environment, similar to common "cloud cleaner" tools. Also available as a [Helm Chart](./charts/nkp-cluster-cleaner/README.md) and [NKP Catalog Application](./docs/nkp.md).

![Platform](https://img.shields.io/badge/platform-Nutanix_NKP-blue)
![GitHub Actions Workflow Status](https://img.shields.io/github/actions/workflow/status/markround/nkp-cluster-cleaner/docker.yml)
![GitHub branch check runs](https://img.shields.io/github/check-runs/markround/nkp-cluster-cleaner/main)

_Disclaimer: This is a personal project and is in no way supported/endorsed by, or otherwise connected to Nutanix_

## Strategy
- Any cluster without an `owner` or `expires` label will be deleted.
- Any cluster that is older than the value specified in the `expires` label will be deleted. 
  - This label takes values of the format `n<unit>` , where unit is one of:
    - `h` - Hours
    - `d` - Days
    - `w` - Weeks
    - `y` - Years
  - For example, `12h` , `2d` , `1w`, `1y`

## Protected clusters
By default the management cluster is excluded from deletion, and a configuration file can be provided that accepts a list of regex-based namespaces or cluster names that will be excluded. For example:

```yaml
excluded_namespace_patterns:
- ^default$
- .*-prod$
protected_cluster_patterns:
- ^production-.*
- .*-prod$
- critical-.*
```

## General Usage
```
Usage: nkp-cluster-cleaner [OPTIONS] COMMAND [ARGS]...

  NKP Cluster Cleaner - Delete CAPI clusters based on label criteria.

Options:
  --version  Show the version and exit.
  --help     Show this message and exit.

Commands:
  delete-clusters  Delete CAPI clusters that match deletion criteria.
  generate-config  Generate an example configuration file.
  list-clusters    List CAPI clusters that match deletion criteria.
  serve            Start the web server for the cluster cleaner UI.
```

You must pass in a valid `kubeconfig` context with admin privileges to the NKP management cluster. This can be done by e.g. setting the `KUBECONFIG` environment variable or using the `--kubeconfig` parameter to commands. 

### Web interface
There is a simple read-only web interface that displays the cluster deletion status, protection rules and general configuration. Start the built-in Flask-based webserver with the `serve` command that takes the usual arguments to specify port and bind host etc:

```
Usage: nkp-cluster-cleaner serve [OPTIONS]

  Start the web server for the cluster cleaner UI.

Options:
  --config PATH      Path to configuration file for protection rules
  --kubeconfig PATH  Path to kubeconfig file (default: ~/.kube/config or
                     $KUBECONFIG)
  --host TEXT        Host to bind to (default: 127.0.0.1)
  --port INTEGER     Port to bind to (default: 8080)
  --debug            Enable debug mode
  --prefix TEXT      URL prefix for all routes (e.g., /foo for /foo/clusters)
  --help             Show this message and exit.
```

#### NKP Catalog Application
See the documentation at [docs/nkp.md](./docs/nkp.md) for details on how to deploy the web interface as a NKP catalog application, running inside the NKP Management Cluster itself.

### list-clusters
`list-clusters` - Show a list of clusters that would be deleted. Optional `--namespace` can be passed in to limit the scan to a particular namespace (e.g. a specific Kommander workspace):

```
$ nkp-cluster-cleaner list-clusters
Listing CAPI clusters for deletion across all namespaces...

Found 2 clusters for deletion:
+----------------+--------------------------+---------+-----------+-----------------------+
| Cluster Name   | Namespace                | Owner   | Expires   | Reason                |
+================+==========================+=========+===========+=======================+
| ahv-nkp-01     | hybrid-cloud-dh5nj-lvdq7 | N/A     | N/A       | Missing 'owner' label |
+----------------+--------------------------+---------+-----------+-----------------------+
| ndk-local      | ndk-test-8jq94-wg7st     | N/A     | N/A       | Missing 'owner' label |
+----------------+--------------------------+---------+-----------+-----------------------+

Found 3 clusters excluded from deletion:
+----------------+-----------------------+---------+-----------+-------------------------------------------------------------+
| Cluster Name   | Namespace             | Owner   | Expires   | Exclusion Reason                                            |
+================+=======================+=========+===========+=============================================================+
| emea-prod-01   | emea-prod-w5pb8-fn9dg | eric    | 1y        | KommanderCluster emea-prod-01 is protected by configuration |
+----------------+-----------------------+---------+-----------+-------------------------------------------------------------+
| mdr-test       | emea-prod-w5pb8-fn9dg | mdr     | 1d        | Cluster has not expired yet (expires in ~23h)               |
+----------------+-----------------------+---------+-----------+-------------------------------------------------------------+
| nkp            | default               | N/A     | N/A       | Cluster is a management cluster                             |
+----------------+-----------------------+---------+-----------+-------------------------------------------------------------+


$ nkp-cluster-cleaner list-clusters --namespace emea-prod-w5pb8-fn9dg
Listing CAPI clusters for deletion in namespace 'emea-prod-w5pb8-fn9dg'...

No clusters found matching deletion criteria.

Found 2 clusters excluded from deletion:
+----------------+-----------------------+---------+-----------+-------------------------------------------------------------+
| Cluster Name   | Namespace             | Owner   | Expires   | Exclusion Reason                                            |
+================+=======================+=========+===========+=============================================================+
| emea-prod-01   | emea-prod-w5pb8-fn9dg | eric    | 1y        | KommanderCluster emea-prod-01 is protected by configuration |
+----------------+-----------------------+---------+-----------+-------------------------------------------------------------+
| mdr-test       | emea-prod-w5pb8-fn9dg | mdr     | 1d        | Cluster has not expired yet (expires in ~23h)               |
+----------------+-----------------------+---------+-----------+-------------------------------------------------------------+
```

For any clusters that have an `expires` label, it will display how long until they are considered for deletion:

```
+----------------+-----------------------------+---------+-----------+----------------------------------------------+
| workload-1     | kommander-default-workspace | mdr     | 12d       | Cluster has not expired yet (expires in ~1d) |
+----------------+-----------------------------+---------+-----------+----------------------------------------------+
```

And if they have expired will show the creation date along with the expiry policy:

```
+----------------+-----------------------------+---------+-----------+--------------------------------------------------------------+
| Cluster Name   | Namespace                   | Owner   | Expires   | Reason                                                       |
+================+=============================+=========+===========+==============================================================+
| workload-1     | kommander-default-workspace | mdr     | 9d        | Cluster has expired (created: 2025-06-23, expires after: 9d) |
+----------------+-----------------------------+---------+-----------+--------------------------------------------------------------+
```

### delete-clusters
Default is to just show what would be deleted:
```
$ nkp-cluster-cleaner delete-clusters --namespace emea-prod-w5pb8-fn9dg
[DRY RUN MODE] Simulating cluster deletion in namespace 'emea-prod-w5pb8-fn9dg'...
Note: Running in dry-run mode. Use --delete to actually delete clusters.

Found 1 clusters that would be deleted:
+----------------+-----------------------+---------+-----------+-------------------------+
| Cluster Name   | Namespace             | Owner   | Expires   | Reason                  |
+================+=======================+=========+===========+=========================+
| mdr-test       | emea-prod-w5pb8-fn9dg | mdr     | N/A       | Missing 'expires' label |
+----------------+-----------------------+---------+-----------+-------------------------+
[DRY RUN] Would delete: mdr-test in emea-prod-w5pb8-fn9dg (Missing 'expires' label)
```

To actually delete the clusters you must pass in the `--delete` flag:

```
$ nkp-cluster-cleaner delete-clusters --namespace emea-prod-w5pb8-fn9dg --delete
Found 1 clusters for deletion:
+----------------+-----------------------+---------+-----------+-------------------------+
| Cluster Name   | Namespace             | Owner   | Expires   | Reason                  |
+================+=======================+=========+===========+=========================+
| mdr-test       | emea-prod-w5pb8-fn9dg | mdr     | N/A       | Missing 'expires' label |
+----------------+-----------------------+---------+-----------+-------------------------+
Successfully deleted cluster: mdr-test in namespace emea-prod-w5pb8-fn9dg

Deletion completed. 1 clusters deleted successfully.
```


## Docker Usage
### Tags
`ghcr.io/markround/nkp-cluster-cleaner:<TAG>`

- Branch (e.g. `main`, `dev`, etc.)
- Release tag (e.g. `0.4.2`) 
- Latest released version (e.g. `latest`)
- Full list on the [packages page](https://github.com/markround/nkp-cluster-cleaner/pkgs/container/nkp-cluster-cleaner)

### Show help
```bash
docker run --rm ghcr.io/markround/nkp-cluster-cleaner:latest
```

### List clusters (requires kubeconfig volume mount)
```bash
docker run --rm \
  -v ~/.kube/config:/app/config/kubeconfig:ro \
  ghcr.io/markround/nkp-cluster-cleaner:latest \
  list-clusters --kubeconfig /app/config/kubeconfig
```

### With custom config file
```bash
docker run --rm \
  -v ~/.kube/config:/app/config/kubeconfig:ro \
  -v ./my-config.yaml:/app/config/config.yaml:ro \
  ghcr.io/markround/nkp-cluster-cleaner:latest \
  list-clusters \
  --kubeconfig /app/config/kubeconfig \
  --config /app/config/config.yaml
```

### Delete clusters (dry-run by default)
```bash
docker run --rm \
  -v ~/.kube/config:/app/config/kubeconfig:ro \
  ghcr.io/markround/nkp-cluster-cleaner:latest \
  delete-clusters --kubeconfig /app/config/kubeconfig
```

### Actually delete clusters
```bash
docker run --rm \
  -v ~/.kube/config:/app/config/kubeconfig:ro \
  ghcr.io/markround/nkp-cluster-cleaner:latest \
  delete-clusters --kubeconfig /app/config/kubeconfig --delete
```

### Generate example config file
```bash
docker run --rm \
  -v $(pwd):/app/output \
  ghcr.io/markround/nkp-cluster-cleaner:latest \
  generate-config /app/output/my-config.yaml
```

### Start webserver
```bash
docker run --rm \
  -v ~/.kube/config:/app/config/kubeconfig:ro \
  -v ./my-config.yaml:/app/config/config.yaml:ro \
  -p 8080:8080 \
  ghcr.io/markround/nkp-cluster-cleaner:latest \
  serve \
  --kubeconfig /app/config/kubeconfig \
  --config /app/config/config.yaml \
  --host 0.0.0.0
```

## Installation / Development

```bash
pip install -r requirements.txt
pip install -e .
```
