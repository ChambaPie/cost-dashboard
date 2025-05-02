#!/bin/bash
set -e

echo "Container starting at $(date)"

# Run the data collection immediately on startup
echo "Running initial data collection on startup..."
/app/execute.sh

# Start the cron service to handle scheduled runs
echo "Starting cron service for scheduled collection..."
crontab /app/cronjob
crond -f -L /dev/stdout