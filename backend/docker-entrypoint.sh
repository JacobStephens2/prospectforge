#!/bin/sh
# Container entrypoint. Runs as root briefly to chown the mounted state
# volume to the `app` user, then drops privileges and exec's the real
# command. Without this, Docker named volumes mount as root and our
# non-root container can't write exports / archive into them.
set -e

mkdir -p /var/lib/prospectforge/exports /var/lib/prospectforge/archive
chown -R app:app /var/lib/prospectforge

exec gosu app "$@"
