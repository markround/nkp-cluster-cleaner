# NKP Catalog Application
> [!IMPORTANT]  
> The catalog installation method detailed below is only supported on NKP Ultimate and above license tiers. If you are running NKP Pro or lower, you can still use the application but must install it manually from the [Helm Chart](./helm.md)

<img src="/docs/catalog.png" width="200">

The NKP Cluster Cleaner tool can be installed as a NKP Catalog Application and makes use of features enabled in the Management Cluster:

- Traefik ingress controller (with optional authentication, enabled by default)
- The default kommander kubeconfig secret for self-attachment. 
- Optional integration with the NKP Prometheus & Grafana stack

The Helm Chart used by the application will also install a CronJob to handle the automated deletion of clusters. This is set to dry-run by default, and must be explicitly enabled before any destructive actions will be carried out.

## Installation

To install the custom catalog, run the following command:

```bash
nkp create catalog nkp-cluster-cleaner \
    -w kommander-workspace \
    --tag 0.10.1 \
    --url https://github.com/markround/nkp-cluster-cleaner
```

You can then select the application in the Management Cluster Workspace and enable it. After it has been deployed, the dashboard can be accessed in the usual way, e.g. by browsing to the Management Cluster and selecting the Application Dashboards tab:

<img src="/docs/dashboard.png" width="400">

## Configuration

Assuming a standard installation of NKP Ultimate[https://github.com/markround/nkp-cluster-cleaner/issues/8], the application will work without any further configuration required. 

Additional configuration can be carried out by setting the Application Configuration Override within the NKP interface:

<img src="/docs/config.png" width="400">

For a full reference of the Helm values, see the included [Chart documentation](helm.md), or the full set of [Helm values](/charts/nkp-cluster-cleaner/README.md). 


> [!TIP]
> The default settings will require an admin account to log-in and view the dashboard. More granular RBAC will be added in a future release - see https://github.com/markround/nkp-cluster-cleaner/issues/4.

## Upgrading

If you have an old version of the application installed, you can upgrade to the current version by first updating the catalog repository to point to the latest release:

```bash
kubectl patch \
  --type merge \
  -n kommander \
  gitrepository nkp-cluster-cleaner \
  --patch '{"spec": {"ref":{"tag":"0.10.1"}}}'
```

And then updating your AppDeployment to the latest release:

```bash
kubectl patch \
  --type merge \
  -n kommander \
  AppDeployment nkp-cluster-cleaner \
  --patch '{"spec":{"appRef":{"name":"nkp-cluster-cleaner-0.10.1"}}}'
```
