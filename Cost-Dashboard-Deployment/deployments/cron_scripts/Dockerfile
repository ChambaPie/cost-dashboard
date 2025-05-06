#nstarx.azurecr.io/cost-data-collector:v1

# Use your base image
FROM nstarx.azurecr.io/cost-data-collector-base:latest

WORKDIR /app

# Copy application code
COPY . .

# Make scripts executable
RUN chmod +x /app/execute.sh
RUN chmod +x /app/startup.sh

# Install cron
RUN apt-get update && apt-get install -y cron && rm -rf /var/lib/apt/lists/*

# Ensure log file exists
RUN touch /var/log/cron.log

# Set the entrypoint to your startup script
ENTRYPOINT ["/app/startup.sh"]