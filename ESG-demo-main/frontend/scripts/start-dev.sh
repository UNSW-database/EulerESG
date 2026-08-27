#!/bin/sh

set -eu

app_dir=$(pwd -P)
dev_dist_dir=.next-dev
cache_dir="${app_dir}/${dev_dist_dir}"

# This script is allowed to invalidate only the generated development cache.
# Refuse an unexpected path before performing any recursive cleanup.
if [ "$app_dir" != "/app" ] || [ "$cache_dir" != "/app/.next-dev" ]; then
    echo "Refusing to manage unexpected Next.js cache path: ${cache_dir}" >&2
    exit 2
fi

expected_lock=$(sha256sum package-lock.json | awk '{print $1}')
installed_lock=$(cat node_modules/.euleresg-package-lock.sha256 2>/dev/null || true)

if [ ! -d node_modules ] || [ -z "$(ls -A node_modules 2>/dev/null)" ] || [ "$expected_lock" != "$installed_lock" ]; then
    sh /usr/local/bin/npm-ci-with-registry-fallback
fi

mkdir -p "$cache_dir"
cache_signature=$(
    {
        sha256sum package.json
        sha256sum package-lock.json
        sha256sum next.config.js
        sha256sum scripts/start-dev.sh
        node --version
    } | sha256sum | awk '{print $1}'
)
# Next may replace files inside its distDir during startup. Keep the signature
# in the separately persisted dependency volume so a healthy cache survives a
# normal frontend restart.
cache_marker="${app_dir}/node_modules/.euleresg-next-dev-cache-signature"
stored_signature=$(cat "$cache_marker" 2>/dev/null || true)

if [ "$stored_signature" != "$cache_signature" ]; then
    echo "[frontend-dev] Dependency/config signature changed; rebuilding generated Next.js cache."
    find "$cache_dir" -mindepth 1 -depth -delete
    printf '%s\n' "$cache_signature" > "$cache_marker"
fi

warm_frontend_routes() {
    warmup_origin="http://127.0.0.1:3001"
    attempts=0
    until wget -q -O /dev/null "${warmup_origin}/" 2>/dev/null; do
        attempts=$((attempts + 1))
        if [ "$attempts" -ge 120 ]; then
            echo "[frontend-dev] Route warmup skipped: dev server did not become ready." >&2
            return
        fi
        sleep 1
    done

    # Compile high-frequency destinations sequentially so the first real
    # navigation never has to wait for a cold page build. Sequential requests
    # also avoid competing with the dashboard's first browser render.
    for route in \
        /dashboard \
        /dashboard/chat \
        /dashboard/favourite \
        /dashboard/standards-library \
        /dashboard/graph \
        /dashboard/company/__warmup__ \
        /cross-analysis \
        '/cross-analysis/evidence?file_id=__warmup__'
    do
        if wget -q -O /dev/null "${warmup_origin}${route}" 2>/dev/null; then
            echo "[frontend-dev] Warmed ${route}"
        else
            echo "[frontend-dev] Could not warm ${route}; it will compile on first visit." >&2
        fi
    done
}

case "${FRONTEND_ROUTE_WARMUP:-1}" in
    0|false|FALSE|no|NO|off|OFF)
        ;;
    *)
        warm_frontend_routes &
        ;;
esac

exec npm run dev -- -H 0.0.0.0
