#!/bin/bash
# Script to run Celery locally with minimal Docker usage on Linux/macOS

# Make sure Redis is running
echo "Checking if Redis is running..."
if ! docker-compose -f docker-compose.local.yml ps | grep -q "cache.*Up"; then
    echo "Redis is not running. Starting required Docker services..."
    docker-compose -f docker-compose.local.yml up -d cache
    sleep 5
fi

# Activate virtual environment from .venv folder instead of venv
source .venv/bin/activate

cd api || { echo "Error: Cannot change to api directory"; exit 1; }

# Check if .env exists (should have been created by run-local.sh)
if [ ! -f .env ]; then
    echo "Setting up local environment file..."
    cp .env.local .env
fi

# Start Celery beat in background
echo "Starting Celery beat..."
celery -A taskman beat -l INFO --pidfile=./beat.pid --detach

# Start Celery worker in background
echo "Starting Celery worker..."
celery -A taskman worker -l INFO --detach

echo "================================================"
echo "Celery services are now running"
echo "================================================"
echo "Run 'pkill -f celery' to stop all Celery processes"

# Keep terminal open
exec $SHELL
