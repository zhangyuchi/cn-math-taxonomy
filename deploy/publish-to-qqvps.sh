#!/usr/bin/env bash
# Run on qqvps-cd as ubuntu. Publishes a git ref into /srv/cn-math-taxonomy.
set -euo pipefail

REF="${1:-}"
if [[ -z "$REF" ]]; then
  echo "用法: $0 <git-ref>   # 例如 deploy/qqvps-20261106 或完整 sha" >&2
  exit 1
fi

REPO_MIRROR="${REPO_MIRROR:-/home/ubuntu/Projects/cn-math-taxonomy}"
ROOT="${ROOT:-/srv/cn-math-taxonomy}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
SAFE_REF="$(echo "$REF" | tr '/:' '--')"
RELEASE="$ROOT/releases/${TS}-${SAFE_REF}"

mkdir -p "$ROOT/releases"
git -C "$REPO_MIRROR" fetch --all --tags --prune
# Prefer origin (fork); fall back to whatever ref resolves
git -C "$REPO_MIRROR" rev-parse --verify "$REF^{commit}" >/dev/null
SHA="$(git -C "$REPO_MIRROR" rev-parse "$REF^{commit}")"
mkdir -p "$RELEASE"
git -C "$REPO_MIRROR" archive --format=tar "$SHA" | tar -x -C "$RELEASE"
ln -sfn "$RELEASE" "$ROOT/current.new"
mv -Tf "$ROOT/current.new" "$ROOT/current"

# Point static server at /srv release if unit uses EnvironmentFile/drop-in;
# default: rewrite WorkingDirectory via drop-in if present, else remind operator.
if [[ -f /etc/systemd/system/cn-math.service.d/override.conf ]] || [[ -d /etc/systemd/system/cn-math.service.d ]]; then
  systemctl daemon-reload
  systemctl restart cn-math.service
else
  echo "已切换 current → $RELEASE (sha $SHA)"
  echo "请确认 cn-math.service 的 --directory 指向 $ROOT/current（见 deploy/systemd/cn-math.service.example）"
fi

echo "OK: $ROOT/current -> $RELEASE ($SHA)"
