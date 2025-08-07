# nkp-cluster-cleaner

![Version: 0.13.0](https://img.shields.io/badge/Version-0.13.0-informational?style=flat-square) ![Type: application](https://img.shields.io/badge/Type-application-informational?style=flat-square) ![AppVersion: 0.13.0](https://img.shields.io/badge/AppVersion-0.13.0-informational?style=flat-square)

A simple tool to automatically delete Nutanix NKP clusters that do not meet a specific criteria. Note that this chart is designed to be installed directly into the NKP Management Cluster, so various settings/templates have been hard-coded or configured accordingly.

## Values

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| deployment.image | string | `"ghcr.io/markround/nkp-cluster-cleaner:0.13.0"` | Container image to use |
| deployment.replicas | int | `1` | Number of replicas to deploy |
| ingress.enabled | bool | `true` | Enables ingress through the Kommander Traefik deployment |
| ingress.prefix | string | `"/mdr/nkp-cluster-cleaner"` | URL Prefix for the dashboard |
| ingress.class | string | `"kommander-traefik"` | Ingress class to use |
| ingress.authentication.enabled | bool | `true` | If true, access to the dashboard will require logging in with an admin account. Setting to false will enable anonymous access. |
| analytics.enabled | bool | `true` | Enable the analytics service and components |
| analytics.schedule | string | `"@hourly"` | Schedule to run the collection job. Uses standard Kubernetes CronJob syntax. |
| analytics.failedJobsHistoryLimit | int | `1` | How many failed jobs to keep |
| analytics.successfulJobsHistoryLimit | int | `3` | How many successful jobs to keep |
| deletion.enabled | bool | `true` | Enable scheduled deletion CronJobs |
| deletion.delete | bool | `false` | Set to true to actually delete clusters, default is to operate in "dry-run" mode |
| deletion.schedule | string | `"@daily"` | Schedule to run the job. Uses standard Kubernetes CronJob syntax. |
| deletion.failedJobsHistoryLimit | int | `1` | How many failed jobs to keep |
| deletion.successfulJobsHistoryLimit | int | `3` | How many successful jobs to keep |
| app.kubeconfigSecretRef | string | `"kommander-self-attach-kubeconfig"` | Secret containing a valid kubeconfig for the management cluster |
| app.config | string | `"excluded_namespace_patterns:\n- ^default$\nprotected_cluster_patterns:\n- .*-prod-.*\nextra_labels:\n- name: owner\n  description: Cluster owner identifier\n"` | Default set of exclusion rules |
| monitoring.grafanaDashboard.enabled | bool | `false` | Deploy a Dashboard into the NKP Grafana instance |
| monitoring.serviceMonitor.enabled | bool | `false` | Enable ServiceMonitor for integration with NKP Prometheus monitoring |
| monitoring.serviceMonitor.interval | string | `"30s"` | Scrape interval for metrics collection |
| monitoring.serviceMonitor.scrapeTimeout | string | `"10s"` | Scrape timeout for metrics collection |
| monitoring.serviceMonitor.labels | object | `{"prometheus.kommander.d2iq.io/select":"true"}` | Additional labels for ServiceMonitor |
| monitoring.serviceMonitor.annotations | object | `{}` | Additional annotations for ServiceMonitor |
| monitoring.serviceMonitor.metricRelabelings | list | `[]` | Metric relabeling rules |
| monitoring.serviceMonitor.relabelings | list | `[]` | Relabeling rules |
| notifications.enabled | bool | `false` | Enable sending notifications from CronJob |
| notifications.backend | string | `"slack"` | Notification backend to use |
| notifications.schedule | string | `"@hourly"` | When to run the scheduled notifications CronJob |
| notifications.failedJobsHistoryLimit | int | `1` | How many failed CronJobs to keep |
| notifications.successfulJobsHistoryLimit | int | `3` | How many successful CronJobs to keep |
| notifications.warningThreshold | string | `"80"` | Warning threshold percentage of cluster time elapsed |
| notifications.criticalThreshold | string | `"95"` | Critical threshold percentage of cluster time elapsed |
| notifications.slack.tokenSecretName | string | `"slack-token"` | Secret name to retrieve the Slack OAuth token from |
| notifications.slack.tokenSecretKey | string | `"token"` | Secret key to retrieve the Slack OAuth token from |
| notifications.slack.channel | string | `"alerts"` | Slack channel to use for notifications |
| notifications.slack.username | string | `"NKP Cluster Cleaner"` | Username to display in alerts |
| notifications.slack.iconEmoji | string | `":broom:"` | Emjoi icon to use |
| redis.enabled | bool | `true` | Make use of Redis for persistence, required for notification history and analytics features |
| redis.hostname | string | `"nkp-cluster-cleaner-valkey"` | Hostname of the Redis/Valkey instance used by analytics and notifications |
| redis.port | int | `6379` | Port of the Redis/Valkey instance |
| redis.db | int | `0` | Redis database number |
| redis.authentication.enabled | bool | `false` | Use Redis authentication |
| redis.authentication.secretName | string | `"redis-secret"` | Secret name that contains the Redis credentials |
| redis.authentication.secretPasswordKey | string | `"username"` | Secret key that contains the Redis username |
| redis.authentication.secretUsernameKey | string | `"password"` | Secret key that contains the Redis password |
| valkey.enabled | bool | `true` | Deploy a Valkey Redis-compatible service for storing historical data and notifications |
| valkey.image | string | `"ghcr.io/valkey-io/valkey:8-alpine"` | Valkey container image to use |
| valkey.saveEnv | string | `"900 1"` | Valkey SAVE_ENV value to specify how often to ensure data is flushed to disk |
| valkey.secretName | string | `"nkp-cluster-cleaner-valkey-secret"` | Secret name that contains the default connection password |
| valkey.secretPasswordKey | string | `"valkey-password"` | Secret key that contains the default connection password |

----------------------------------------------
Autogenerated from chart metadata using [helm-docs v1.14.2](https://github.com/norwoodj/helm-docs/releases/v1.14.2)
