#!/bin/bash
# Script to run FolioMan locally with minimal Docker usage

# Start Docker services (timedb, cache, pgadmin)
echo "Starting required Docker services (timedb, cache, pgadmin)..."
docker-compose -f docker-compose.local.yml up -d

echo "Waiting for services to be ready..."
sleep 5

# Set up Python environment
if [ ! -d ".venv" ]; then
    echo "Creating Python virtual environment..."
    python -m venv .venv
fi

# Activate virtual environment
source .venv/bin/activate || source .venv/Scripts/activate

# Install Python requirements
echo "Installing Python requirements..."
cd api || { echo "Error: api directory not found"; exit 1; }
pip install -r requirements.txt
pip install casparser-isin

# Use the local environment file
if [ ! -f ".env" ] || [ ! -L ".env" ]; then
    echo "Setting up local environment file..."
    cp .env.local .env
fi

# Run Django migrations
echo "Running database migrations..."
python manage.py migrate

# Collect static files
echo "Collecting static files..."
python manage.py collectstatic --noinput

# Start Django server in background
echo "Starting Django server..."
python manage.py runserver 8000 &
API_PID=$!

# Setup UI
echo "Setting up UI..."
cd ../ui || { echo "Error: ui directory not found"; exit 1; }

# Install Node.js dependencies
echo "Installing Node.js dependencies..."
yarn install

# Build and start UI
echo "Building and starting UI..."
# Modified to explicitly set the host to 0.0.0.0 to make it accessible from outside
yarn run dev --host 0.0.0.0 &
UI_PID=$!

echo "================================================"
echo "FolioMan is now running:"
echo "API: http://localhost:8000/admin"
echo "UI: http://localhost:3000"
echo "PGAdmin: http://localhost:5050"
echo "================================================"
echo "Press Ctrl+C to stop all services"

# Handle graceful shutdown
function cleanup {
    echo "Shutting down services..."
    kill $API_PID
    kill $UI_PID
    cd $(dirname "$0") && docker-compose -f docker-compose.local.yml down
    exit 0
}

trap cleanup INT TERM
wait