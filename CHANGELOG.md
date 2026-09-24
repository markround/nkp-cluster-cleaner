# Changelog
## 1.0.1
- Fix for wrongly categorised deletion reasons when analysing pre-1.0.0 metrics in Redis.

## 1.0.0
### User facing changes:
- Removed legacy Helm repository, chart is now only available as an OCI artifact.
- Added support for `NKPCluster` objects in NKP 2.18+ (CAPI Cluster objects still supported)
- Redesigned / updated UI
- Dropped GitRepository and support for NKP < 2.16
### Internal changes
- Refactored codebase into modules, massive code tidy and clean-up
- Added extensive test suite and mock API tools
- Fixed job logs in the scheduled jobs view rendering as an escaped `b'...'` string instead of the log text

## 0.15.0
- Newly created clusters can be given a grace period during which they will not be deleted or generate notifications, even if they are missing required labels or have already expired. This gives cluster creators time to properly label their clusters after creation.

## 0.14.0
- Added support for NKP 2.16 OCI catalog-bundles. 

## 0.13.9
- Packaging change only. Changed workflow to push Helm package to helm.mdr.dev

## 0.13.8
- Added OCI Helm package

## 0.13.7
- Added ability to trigger scheduled CronJobs from web UI
