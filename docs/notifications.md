# Notifications

The NKP Cluster Cleaner includes a notification system that sends alerts when clusters are approaching their expiration time. This helps ensure teams are aware of upcoming cluster deletions and can take appropriate action.

## Overview

The notification system monitors clusters with `expires` labels and sends warnings at configurable threshold percentages. Notifications are tracked using Redis to prevent duplicate alerts, and notification history is automatically cleaned up when clusters are deleted.

## Thresholds

The default settings are to send a warning at 80% of the cluster lifetime (as configured with the `expires` label) and a second warning at 95%. This equates to the following values:

| Severity | Default Percentage | 1 Day      | 1 Week    | 1 Month   |
|----------|--------------------|------------|-----------|-----------|
| Warning  | 80%                | 19.2 Hours | 5.6 Days  | 24 Days   |
| Critical | 95%                | 22.8 Hours | 6.65 Days | 28.5 Days |

These can be changed with the following flags or environment variables when calling the `notify` command:

| CLI flag | Environment variable equivalent |
| ---------|-------------------------------- |
| `--critical-threshold` | `CRITICAL_THRESHOLD` |
| `--warning-threshold`  | `WARNING_THRESHOLD` |

## Scheduling

If you are installing from the [Helm Chart](helm.md), a CronJob will be created that will run every hour to process and send notifications. The schedule can be customized using the `notifications.schedule` value in the Helm chart. The default schedule is `@hourly` (which works well with the default thresholds), but this can be changed to any valid [cron expression](https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/#schedule-syntax). 

## Duplicate Prevention

The notification system uses Redis to keep track of sent notifications and avoid duplicates. Each cluster's notification history is stored with the following key structure:

```
notifications:cluster:<namespace>:<cluster-name>
```

The system tracks which severity levels (warning/critical) have been sent for each cluster. Once a notification is sent at a particular severity level, it will not be sent again for that cluster.

Notification records are automatically expired after 30 days to prevent Redis from growing indefinitely. When a cluster is deleted, its notification history is also automatically cleaned up.

## Deletion Notifications

Notifications are also sent when a cluster is deleted, and the notification history for that cluster is then removed from Redis.

## Command Line Usage

The notification system can be used from the command line with the `notify` command:

```bash
nkp-cluster-cleaner notify \
  --kubeconfig ~/.kube/config \
  --notify-backend slack \
  --slack-token <YOUR-SLACK-TOKEN-HERE> \
  --slack-channel alerts \
  --warning-threshold 75 \
  --critical-threshold 90
```

### Redis Configuration

The notification system requires Redis for tracking sent notifications. You can configure the Redis connection with the following parameters:

| CLI flag | Environment variable | Default | Description |
|----------|---------------------|---------|-------------|
| `--redis-host` | `REDIS_HOST` | `redis` | Redis hostname |
| `--redis-port` | `REDIS_PORT` | `6379` | Redis port |
| `--redis-db` | `REDIS_DB` | `0` | Redis database number |

## Notification Backends

### Slack

Slack is currently the only supported backend for notifications. The system sends rich notifications with cluster details, ownership information, and remaining time:

<img src="/docs/slack.png" width="400">

#### Prerequisites

To use Slack notifications, you need to create a Slack app with the required permissions. See the [DataCamp tutorial](https://www.datacamp.com/tutorial/how-to-send-slack-messages-with-python) for a detailed example of creating the OAuth token. Just follow step 1!

Required permissions:
- `chat:write` - Send messages to channels
- `chat:write.customize` - Customize message appearance (username, emoji)

#### Configuration

The following parameters are available for Slack configuration:

| CLI flag | Environment variable | Required | Description |
|----------|---------------------|----------|-------------|
| `--slack-token` | `SLACK_TOKEN` | Yes | Slack app OAuth token |
| `--slack-channel` | `SLACK_CHANNEL` | Yes | Channel name or ID for notifications |
| `--slack-username` | `SLACK_USERNAME` | No | Display name for the bot (default: "NKP Cluster Cleaner") |
| `--slack-icon-emoji` | `SLACK_ICON_EMOJI` | No | Emoji icon for the bot (default: ":broom:") |

#### Message Format

Slack notifications include:
- Cluster name and namespace
- Owner information (from the `owner` label)
- Expiration time and date
- Percentage of lifetime elapsed
- Time remaining before deletion
- Severity level (Warning/Critical)

## Helm Chart Integration

See the [Helm Chart documentation](helm.md#notifications) for details on how to enable and configure the notifications service. 

## Web Interface

The notification system includes a web interface that displays:

- Current clusters requiring notifications
- Notification thresholds and timing
- Notification history statistics
- Redis connection status

The interface shows both warning and critical notifications in separate tables, with color-coded severity indicators and time remaining calculations.

