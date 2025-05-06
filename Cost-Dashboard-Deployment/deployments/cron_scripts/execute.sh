#!/bin/bash
set -e

# Configuration
MAX_RETRIES=10
RETRY_DELAY=300  # 5 minutes in seconds
AWS_SCRIPT="/app/get_aws_costs_cron.py"
AZURE_SCRIPT="/app/get_azure_costs_cron.py"
OVERALL_STATUS=0

# Function to run a script with retries
function run_script_with_retry {
  local script="$1"
  local script_name=$(basename "$script")
  local status=0
  
  echo "Starting execution of $script_name at $(date)"
  
  local n=0
  until [ $n -ge $MAX_RETRIES ]; do
    echo "[$script_name] Attempt $((n+1)) of $MAX_RETRIES"
    
    # Run the script with error handling for all types of errors
    python "$script" || status=$?
    
    if [ $status -eq 0 ]; then
      echo "[$script_name] Successfully completed at $(date)"
      return 0
    fi
    
    n=$((n+1))
    if [ $n -lt $MAX_RETRIES ]; then
      echo "[$script_name] Attempt failed with status $status. Waiting $RETRY_DELAY seconds before retry..."
      sleep $RETRY_DELAY
    else
      echo "[$script_name] All $MAX_RETRIES attempts failed."
      return $status
    fi
  done
  
  return $status
}

# Main execution starts here
echo "Starting cost collection job at $(date)"

# Run AWS cost collection
run_script_with_retry "$AWS_SCRIPT"
AWS_STATUS=$?
if [ $AWS_STATUS -ne 0 ]; then
  echo "AWS cost collection failed after all retry attempts"
  OVERALL_STATUS=1
fi

# Run Azure cost collection (runs regardless of AWS script success/failure)
run_script_with_retry "$AZURE_SCRIPT"
AZURE_STATUS=$?
if [ $AZURE_STATUS -ne 0 ]; then
  echo "Azure cost collection failed after all retry attempts"
  OVERALL_STATUS=1
fi

# Report overall status
if [ $OVERALL_STATUS -eq 0 ]; then
  echo "All cost collection jobs completed successfully at $(date)"
  exit 0
else
  echo "One or more cost collection jobs failed at $(date)"
  exit 1
fi