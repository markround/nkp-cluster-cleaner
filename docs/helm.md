# Helm Chart
This Helm chart provides a complete deployment solution for the NKP Cluster Cleaner tool. The chart includes web interface, scheduled jobs, analytics, notifications, and monitoring integration, with defaults requiring little additional configuration.

## Prerequisites

- Helm 3.x installed
- Access to the NKP Management Cluster
- Admin privileges in the `kommander` namespace

## Installation

### Install from the official Helm repository:

```bash
helm repo add mdr https://helm.mdr.dev
helm repo update
helm install -n kommander nkp-cluster-cleaner mdr/nkp-cluster-cleaner
```

> [!IMPORTANT]
> This chart must be installed in the `kommander` namespace of the NKP Management Cluster and will *not* work elsewhere.

### From Git Repository
You can also install directly from the source repository using a specific version:

```bash
# Clone the repository
git clone https://github.com/markround/nkp-cluster-cleaner.git
cd nkp-cluster-cleaner

# Install from local chart directory
helm install -n kommander nkp-cluster-cleaner ./charts/nkp-cluster-cleaner

# Or install from a specific tag
git checkout 0.15.0
helm install -n kommander nkp-cluster-cleaner ./charts/nkp-cluster-cleaner
```

### From OCI registry

You can also install from an OCI-based registry:

```bash
helm -n kommander nkp-cluster-cleaner oci://ghcr.io/markround/helm/nkp-cluster-cleaner --version 0.15.0
```

> [!NOTE]
> Prior to Helm v3.8.0, OCI support is experimental and must be enabled. For more information, see [https://helm.sh/docs/topics/registries/](https://helm.sh/docs/topics/registries/)


### Version-Specific Installation
To install a specific version from the Helm repository:

```bash
helm install -n kommander nkp-cluster-cleaner mdr/nkp-cluster-cleaner --version 0.15.0
```


## Upgrading
### From Helm Repository
To upgrade an existing installation to the latest version:
```bash
helm repo update
helm upgrade -n kommander nkp-cluster-cleaner mdr/nkp-cluster-cleaner
```
### To Specific Version
To upgrade to a specific version:
```bash
helm upgrade -n kommander nkp-cluster-cleaner mdr/nkp-cluster-cleaner --version 0.15.0
```

### Upgrade Considerations
When upgrading:

- Analytics data and notification history are preserved across upgrades
- CronJob schedules may be reset to default values if not explicitly configured
- Backup your current values before upgrading: `helm get values -n kommander nkp-cluster-cleaner > my-values.yaml`

## Usage

After installation, the application dashboard can be accessed in the usual way, e.g. by browsing to the Management Cluster and selecting the Application Dashboards tab:

<img src="/docs/dashboard.png" width="400">


## Configuration

You can view a full set of configuration options in the charts [README.md](/charts/nkp-cluster-cleaner/README.md), or browse the default [values.yaml](../charts/nkp-cluster-cleaner/values.yaml).

### Common Configuration Scenarios

#### Default rules

The default rules exclude the default namespace and any cluster with -prod- in the name, and require an owner label on each cluster:

```yaml
app:
  config: |
    excluded_namespace_patterns:
    - ^default$
    protected_cluster_patterns:
    - .*-prod-.*
    extra_labels:
    - name: owner
      description: Cluster owner identifier
```

You can modify this to suit your requirements. See the main [README.md](../README.md) for details on creating your own [protection rules](../README.md#protected-clusters) or adding [required labels](../README.md#extra-labels).

#### Grace Period

By default, newly created clusters have a **2 hour grace period** during which they will not be deleted or generate notifications, even if they are missing required labels or have already expired. This gives cluster creators time to properly label their clusters after creation.

The grace period can be customized or disabled:

```yaml
app:
  # Set a custom grace period (examples: 1h, 4h, 1d, 2w)
  gracePeriod: "4h"

  # Or disable the grace period entirely
  gracePeriod: ""
```

The grace period applies to:
- **Deletion checks**: Clusters within the grace period will not be deleted
- **Notifications**: No warning or critical notifications will be sent for clusters within the grace period
- **Web UI**: Clusters within the grace period appear in the "excluded clusters" list with the reason "Cluster is within grace period"

> [!NOTE]
> The grace period is calculated from the cluster's `creationTimestamp`. Clusters that have proper labels are not affected by the grace period - they will still be protected based on their expiration date.

### Deletion Cron Job
The Helm chart will create a daily CronJob to handle the deletion of clusters. By default, this is set to run daily at midnight in "dry-run" mode, so will not actually delete anything without explicit configuration. To enable the deletion, set the following value:

```yaml
deletion:
  # Actually delete clusters!
  delete: true
```

> [!WARNING]
> Enabling deletion will permanently remove clusters that match the deletion criteria. Ensure your protection rules are correctly configured - I strongly suggest running in dry-run mode with notifications enabled before you consider enabling deletion!

You can view the status and logs of the enabled CronJobs in the Web UI:

<img src="/docs/cron.png" width="400">

### Redis/Valkey
The application uses a Redis-compatible database to store analytics data and track notifications to avoid duplicate alerts. The default settings will deploy a Valkey (Redis-compatible fork) StatefulSet for you with a randomly-generated password. If you would like to use an alternative Redis-compatible service, you can set the following values:

```yaml
# Disable the bundled Valkey
valkey:
  enabled: false
# Point at your own Redis service
redis:
  hostname: your-redis-host.example.com
  port: 6379
  db: 0
  # Enable authentication - optional
  authentication:
    enabled: true
    # A secret that contains your Redis credentials
    secretName: "my-redis-secret"
    secretPasswordKey: "username"
    secretUsernameKey: "password"
```

### Notifications
The [notification service](notifications.md) sends alerts to a configured service (currently Slack is supported) so that you will be notified before clusters are deleted. In order to enable this integration, first create a secret that holds your Slack App OAuth token:

```bash
# Run against the NKP Management Cluster
kubectl -n kommander \
  create secret generic slack-token \
  --from-literal=token=<YOUR SLACK OAUTH TOKEN>
```

And then you can set the following example values:

```yaml
notifications:
  # Enable notifications and the hourly CronJob
  enabled: true
  backend: slack
  slack:
    # Channel to send notifications to
    channel: "alerts"
    # How the notifications will appear
    username: "NKP Cluster Cleaner"
    iconEmoji: ":broom:"
```

#### Notification Thresholds
Customize when notifications are sent:

```yaml
notifications:
  warningThreshold: "75"   # First warning at 75% of cluster lifetime
  criticalThreshold: "90"  # Critical alert at 90% of cluster lifetime
```

### NKP Monitoring Integration
Although the application has its own built-in dashboard and reporting capabilities, you may wish to integrate it with the standard NKP monitoring and metrics stack. If you would like to enable this feature, you can enable the ServiceMonitor which is configured with the appropriate labels for auto-discovery:

```yaml
monitoring:
  serviceMonitor:
    enabled: true
```

The `nkp_cluster_cleaner_*` metrics will then start to populate the Management Cluster's Prometheus instance. A simple Grafana dashboard is also included which you might like to use. 

<img src="/docs/grafana.png" width="400">

This is also configured with the appropriate labels and settings for discovery by the Management Cluster's Grafana. Enable this with the following settings:

```yaml
monitoring:
  grafanaDashboard:
    enabled: true
```

> [!IMPORTANT] 
> The Grafana dashboard will be deployed to the Management Cluster Grafana (accessed through viewing the cluster details/dashboards page), and not the fleet-wide global Grafana instance (accessed through the Global workspace/Centralized Monitoring & Alerts).
