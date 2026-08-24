#!/bin/sh
# Builds a crontab from COLLECTION_INTERVAL_MINUTES and starts cron in
# the foreground. Runs both directions every cycle; the actual window
# restriction (COLLECTION_WINDOW_START_HOUR/END_HOUR) is enforced inside
# collect_eta.py itself, so the cron schedule can stay a simple
# every-N-minutes rule regardless of what window hours are configured.
set -eu

INTERVAL="${COLLECTION_INTERVAL_MINUTES:-15}"

# cron jobs run in a stripped environment. Snapshot the container's env
# (as provided via `docker run --env-file .env`) so each job can source
# it before running the collector script.
printenv | sed "s/^\(.*\)$/export \1/" > /app/collector/env.sh
chmod 600 /app/collector/env.sh

CRON_LOG=/proc/1/fd/1

cat > /etc/cron.d/eta-collector <<EOF
*/${INTERVAL} * * * * root . /app/collector/env.sh && cd /app && python collector/collect_eta.py --direction home_to_work >> ${CRON_LOG} 2>&1
*/${INTERVAL} * * * * root . /app/collector/env.sh && cd /app && python collector/collect_eta.py --direction work_to_home >> ${CRON_LOG} 2>&1
EOF
chmod 0644 /etc/cron.d/eta-collector
crontab /etc/cron.d/eta-collector

echo "Starting cron with interval=${INTERVAL}min, window=${COLLECTION_WINDOW_START_HOUR:-0}-${COLLECTION_WINDOW_END_HOUR:-24}h local time"

exec cron -f
