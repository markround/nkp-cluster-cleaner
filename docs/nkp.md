# NKP Catalog Application
![](catalog.png)

The Web UI can be installed as a NKP Catalog Application and makes use of features enabled in the Management Cluster:

- Traefik ingress controller (with optional authentication, enabled by default)
- The default kommander kubeconfig secret for self-attachment. 

The Helm Chart used by the application will also install a CronJob to handle the automated deletion of clusters. This is set to dry-run by default, and must be explicitly enabled before any destructive actions will be carried out.

## Installation

To install the custom catalog, run the following command:

```bash
nkp create catalog nkp-cluster-cleaner \
    -w kommander-workspace \
    --tag 0.8.0 \
    --url https://github.com/markround/nkp-cluster-cleaner
```

You can then select the application in the Management Cluster Workspace and enable it. After it has been deployed, the dashboard can be accessed in the usual way, e.g. by browsing to the Management Cluster and selecting the Application Dashboards tab.

## Configuration

Assuming a standard installation of NKP Ultimate[https://github.com/markround/nkp-cluster-cleaner/issues/8], the application will work without any further configuration required. For a full reference of the Helm values, see the included [Chart documentation](/charts/nkp-cluster-cleaner/README.md). Note that the defaults will require an admin account to log-in and view the dashboard. More granular RBAC will be added in a future release - see https://github.com/markround/nkp-cluster-cleaner/issues/4.

### Default rules

The default rules provided exclude the `default` namespace and any cluster with `-prod-` in the name. It will also require an `owner` label to be set on each cluster. 

To change this, set the `app.config` key when deploying the application, e.g.

```yaml
app:
  config: |
    excluded_namespace_patterns:
    - ^default$
    - ^some-other-namespace$ 
    protected_cluster_patterns:
    - .*-prod-.*
    - .*-production-.*
    - ^critical-.*
```

### Cron Job
The Helm chart will create a daily CronJob to handle the deletion of clusters. By default, this is set to run in "dry-run" mode, so will not actually delete anything without explicit configuration. See the [Chart documentation](/charts/nkp-cluster-cleaner/README.md) for values that can be set. For example, to enable the deletion and run every hour you would set the following overrides:

```yaml
cronjob:
  # Actually delete clusters!
  delete: true
  # Run every hour. See https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/#schedule-syntax
  schedule: "@hourly"
```

You can view the status and logs of the enabled CronJobs in the Web UI:

<img src="/docs/cron.png" width="400">


## Upgrading

If you have an old version of the application installed, you can upgrade to the current version by first updating the catalog repository to point to the latest release:

```bash
kubectl patch \
  --type merge \
  -n kommander \
  gitrepository nkp-cluster-cleaner \
  --patch '{"spec": {"ref":{"tag":"0.8.0"}}}'
```

And then updating your AppDeployment to the latest release:

```bash
kubectl patch \
  --type merge \
  -n kommander \
  AppDeployment nkp-cluster-cleaner \
  --patch '{"spec":{"appRef":{"name":"nkp-cluster-cleaner-0.8.0"}}}'
```