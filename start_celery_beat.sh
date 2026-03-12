#!/bin/bash

# Navigate to backend directory
cd "$(dirname "$0")"

# Activate virtual environment
source venv/bin/activate

# Start Celery Beat in the background, logging to debug.log
echo "Starting Celery Beat..."
nohup celery -A core beat -l info >> debug.log 2>&1 &

echo "Celery Beat started in the background!"
echo "Check debug.log for output."
