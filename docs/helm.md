# Helm Chart


## Common configuration options

> [!TIP]
> You can view a full set of [Helm values](/charts/nkp-cluster-cleaner/README.md) to see all configuration options.

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

### Deletion Cron Job
The Helm chart will create a daily CronJob to handle the deletion of clusters. By default, this is set to run daily at midnight in "dry-run" mode, so will not actually delete anything without explicit configuration. See the [Chart documentation](/charts/nkp-cluster-cleaner/README.md) for values that can be set. For example, to enable the deletion  you would set the following value:

```yaml
cronjob:
  # Actually delete clusters!
  delete: true
```

You can view the status and logs of the enabled CronJobs in the Web UI:

<img src="/docs/cron.png" width="400">

### Redis/Valkey
The application uses a Redis-compatible database to store analytics data and track notifications to avoid duplicate alerts. The default settings will create a Valkey (Redis-compatible fork) StatefulSet for you, but if you have an alternative Redis-compatible service you wish to use, you can set the following values:

```yaml
# Disable the bundled Valkey
valkey:
  enabled: false
# Point at your own Redis service
redis:
  hostname: your-redis-host.example.com
  port: 6379
  db: 0
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
    channel: alerts
    # How the notifications will appear
    username: "NKP Cluster Cleaner"
    iconEmoji: ":broom:"
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
