# Changelog
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
