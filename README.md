# nkp-cluster-cleaner

A simple tool to automatically delete NKP clusters that do not meet a specific criteria in a lab/demo environment.

## Strategy
- Any cluster without an `owner` or `expires` label will be deleted.
- Any cluster that is older than the value specified in the `expires` label will be deleted. 
  - This label takes values of the format `n<unit>` , where unit is one of:
    - `h` - Hours
    - `d` - Days
    - `w` - Weeks
  - For example, `12h` , `2d` , `1w`

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
```

## Docker Usage
### Show help
```bash
docker run --rm nkp-cluster-cleaner:latest
```

### List clusters (requires kubeconfig volume mount)
```bash
docker run --rm \
  -v ~/.kube/config:/app/config/kubeconfig:ro \
  nkp-cluster-cleaner:latest \
  list-clusters --kubeconfig /app/config/kubeconfig
```

### With custom config file
```bash
docker run --rm \
  -v ~/.kube/config:/app/config/kubeconfig:ro \
  -v ./my-config.yaml:/app/config/config.yaml:ro \
  nkp-cluster-cleaner:latest \
  list-clusters \
  --kubeconfig /app/config/kubeconfig \
  --config /app/config/config.yaml
```

### Delete clusters (dry-run by default)
```bash
docker run --rm \
  -v ~/.kube/config:/app/config/kubeconfig:ro \
  nkp-cluster-cleaner:latest \
  delete-clusters --kubeconfig /app/config/kubeconfig
```

### Actually delete clusters
```bash
docker run --rm \
  -v ~/.kube/config:/app/config/kubeconfig:ro \
  nkp-cluster-cleaner:latest \
  delete-clusters --kubeconfig /app/config/kubeconfig --delete --confirm
```

### Generate example config file
```bash
docker run --rm \
  -v $(pwd):/app/output \
  nkp-cluster-cleaner:latest \
  generate-config /app/output/my-config.yaml
```

## Installation / Development

```bash
pip install -r requirements.txt
pip install -e .
```