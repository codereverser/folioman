@echo off
REM Script to run Celery locally with minimal Docker usage on Windows

REM Make sure Redis is running
echo Checking if Redis is running...
docker-compose -f docker-compose.local.yml ps | findstr "cache" | findstr "Up" >nul
if errorlevel 1 (
    echo Redis is not running. Starting required Docker services...
    docker-compose -f docker-compose.local.yml up -d cache
    timeout /t 5
)

REM Activate virtual environment
call .venv\Scripts\activate.bat

cd api

REM Check if .env exists (should have been created by run-local.bat)
if not exist .env (
    echo Setting up local environment file...
    copy .env.local .env
)

REM Start Celery beat
echo Starting Celery beat...
start "Celery Beat" celery -A taskman beat -l INFO --pidfile=./beat.pid

REM Start Celery worker
echo Starting Celery worker...
start "Celery Worker" celery -A taskman worker -l INFO

echo ================================================
echo Celery services are now running
echo ================================================
echo Press Ctrl+C in each terminal window to stop the services

cmd /k