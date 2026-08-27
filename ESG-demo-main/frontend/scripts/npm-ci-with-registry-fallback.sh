#!/bin/sh

# npm accepts one registry per invocation, so try registries in an explicit
# order instead of repeating registry= entries in .npmrc.
set -u

DEFAULT_NPM_REGISTRIES="
https://registry.npmjs.org/
https://registry.npmmirror.com/
https://mirrors.cloud.tencent.com/npm/
https://repo.huaweicloud.com/repository/npm/
"

registry_candidates=${NPM_REGISTRY_CANDIDATES:-$DEFAULT_NPM_REGISTRIES}
attempts_per_registry=${NPM_CI_ATTEMPTS_PER_REGISTRY:-1}

case "$attempts_per_registry" in
    ''|*[!0-9]*)
        echo "NPM_CI_ATTEMPTS_PER_REGISTRY must be a positive integer." >&2
        exit 2
        ;;
esac

if [ "$attempts_per_registry" -lt 1 ]; then
    echo "NPM_CI_ATTEMPTS_PER_REGISTRY must be at least 1." >&2
    exit 2
fi

# Accept either whitespace- or comma-separated overrides.
registry_candidates=$(printf '%s' "$registry_candidates" | tr ',' ' ')
registry_count=0
last_exit_code=1
log_file=$(mktemp)
trap 'rm -f "$log_file"' EXIT HUP INT TERM

for registry in $registry_candidates; do
    registry_count=$((registry_count + 1))
    attempt=1

    while [ "$attempt" -le "$attempts_per_registry" ]; do
        echo "[npm-ci] Trying registry ${registry} (attempt ${attempt}/${attempts_per_registry})"

        # package-lock.json was generated from npmjs. Rewrite only the npmjs
        # host so unrelated third-party tarball URLs are never redirected.
        if npm ci \
            --no-audit \
            --no-fund \
            --registry="$registry" \
            --replace-registry-host=npmjs >"$log_file" 2>&1; then
            cat "$log_file"
            sha256sum package-lock.json | awk '{print $1}' \
                > node_modules/.euleresg-package-lock.sha256
            echo "[npm-ci] Dependencies installed successfully from ${registry}"
            exit 0
        else
            last_exit_code=$?
        fi

        cat "$log_file" >&2
        echo "[npm-ci] Registry ${registry} failed." >&2

        if ! grep -Eiq \
            'ECONNRESET|ETIMEDOUT|EAI_AGAIN|ENOTFOUND|ENETUNREACH|ECONNREFUSED|ERR_SOCKET_TIMEOUT|network (aborted|timeout)|socket hang up|fetch failed|E50[0234]' \
            "$log_file"; then
            echo "[npm-ci] Failure is not a recognized network error; registry fallback stopped." >&2
            exit "$last_exit_code"
        fi

        attempt=$((attempt + 1))
    done
done

if [ "$registry_count" -eq 0 ]; then
    echo "[npm-ci] No npm registries were configured." >&2
else
    echo "[npm-ci] All configured npm registries failed." >&2
fi
exit "$last_exit_code"
