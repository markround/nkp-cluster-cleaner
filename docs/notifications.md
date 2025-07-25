
```bash
kubectl -n kommander \
  create secret generic slack-token \
  --from-literal=token=<YOUR SLACK OAUTH TOKEN>
```