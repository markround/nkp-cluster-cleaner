#!/usr/bin/env bash
if [ $# -ne 1 ]; then
    echo "Usage: $0 <new_version>"
    echo "Example: $0 0.9.1"
    exit 1
fi

OLD_VERSION="$(cat src/nkp_cluster_cleaner/__init__.py | grep __version__ | cut -d\" -f2)"
NEW_VERSION="$1"

# In-place editing needs GNU sed: that is plain sed on Linux, gsed on macOS
# (brew install gnu-sed).
if [ "$(uname -s)" = "Darwin" ]; then
    SED=gsed
else
    SED=sed
fi
if ! command -v "$SED" >/dev/null 2>&1; then
    echo "Error: $SED not found"
    exit 1
fi

FILES=(
  README.md
  charts/nkp-cluster-cleaner/Chart.yaml 
  charts/nkp-cluster-cleaner/README.md
  charts/nkp-cluster-cleaner/values.yaml
  charts/nkp-cluster-cleaner/templates/dashboard.yaml
  docs/nkp.md
  docs/helm.md
  src/nkp_cluster_cleaner/__init__.py
  applications/nkp-cluster-cleaner/$OLD_VERSION/helmrelease/helmrelease.yaml
)


echo "Updating version from $OLD_VERSION to $NEW_VERSION..."

# Process each file
for file in "${FILES[@]}"; do
    if [ -f "$file" ]; then
        echo "Processing: $file"
        "$SED" -i "s/$OLD_VERSION/$NEW_VERSION/g" "$file"
        if [ $? -eq 0 ]; then
            echo "  ✓ Updated $file"
        else
            echo "  ✗ Failed to update $file"
        fi
    else
        echo "  - Skipping $file (not found)"
    fi
done

mv applications/nkp-cluster-cleaner/$OLD_VERSION applications/nkp-cluster-cleaner/$NEW_VERSION

git add .

echo "Version bump complete!"
