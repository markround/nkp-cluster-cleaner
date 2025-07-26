#!/usr/bin/env bash
if [ $# -ne 2 ]; then
    echo "Usage: $0 <old_version> <new_version>"
    echo "Example: $0 0.9.0 0.9.1"
    exit 1
fi

OLD_VERSION="$1"
NEW_VERSION="$2"

FILES=(
  README.md
  charts/nkp-cluster-cleaner/Chart.yaml 
  charts/nkp-cluster-cleaner/README.md
  charts/nkp-cluster-cleaner/values.yaml
  charts/nkp-cluster-cleaner/templates/dashboard.yaml
  docs/nkp.md
  docs/helm.md
  services/nkp-cluster-cleaner/$OLD_VERSION/helm.yaml
  services/nkp-cluster-cleaner/$OLD_VERSION/defaults/cm.yaml
  src/nkp_cluster_cleaner/__init__.py
)


echo "Updating version from $OLD_VERSION to $NEW_VERSION..."

# Process each file
for file in "${FILES[@]}"; do
    if [ -f "$file" ]; then
        echo "Processing: $file"
        gsed -i "s/$OLD_VERSION/$NEW_VERSION/g" "$file"
        if [ $? -eq 0 ]; then
            echo "  ✓ Updated $file"
        else
            echo "  ✗ Failed to update $file"
        fi
    else
        echo "  - Skipping $file (not found)"
    fi
done

mv services/nkp-cluster-cleaner/$OLD_VERSION services/nkp-cluster-cleaner/$NEW_VERSION

git add .

echo "Version bump complete!"
