#nstarx.azurecr.io/cost-dashboard:v6

# Use your base image
FROM nstarx.azurecr.io/cost-dashboard-base:latest

WORKDIR /app

# Copy only application code
COPY . .

EXPOSE 8501

# Default command to run the application
CMD ["streamlit", "run", "/app/static_cloud_cost_report.py", "--server.port=8501", "--server.address=0.0.0.0"]