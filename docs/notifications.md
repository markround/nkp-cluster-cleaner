# Notifications

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

If you are installing from the [Helm Chart](helm.md), a CronJob will be created that will run every hour to process and send notifications.

It uses Redis to keep track of sent notifications so will avoid duplicates.

Notifications are also sent when a cluster is deleted, and the notification history for that cluster will also be deleted.

## Backends
### Slack
#### Pre-requisites
See https://www.datacamp.com/tutorial/how-to-send-slack-messages-with-python for example on creating the oauth token

Permissions required:

- `chat:write`
- `chat:write.customize`

#### Configuration
See available parameters:

| CLI flag | Environment variable equivalent |
| ---------|-------------------------------- |
| `--slack-channel` | `SLACK_CHANNEL` |
| `--slack-icon-emoji` | `SLACK_ICON_EMOJI` |
| `--slack-token` | `SLACK_TOKEN` |
| `--slack-username` | `SLACK_USERNAME` |
