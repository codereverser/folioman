@echo off
REM Script to run FolioMan locally with minimal Docker usage on Windows

echo Starting required Docker services (timedb, cache, pgadmin)...
docker-compose -f docker-compose.local.yml up -d

echo Waiting for services to be ready...
timeout /t 5

REM Set up Python environment
if not exist .venv (
    echo Creating Python virtual environment...
    python -m venv .venv
)

REM Activate virtual environment
call .venv\Scripts\activate.bat

REM Install Python requirements
echo Installing Python requirements...
cd api
pip install -r requirements.txt
pip install casparser-isin

REM Use the local environment file
if not exist .env (
    echo Setting up local environment file...
    copy .env.local .env
)

REM Run Django migrations
echo Running database migrations...
python manage.py migrate

REM Collect static files
echo Collecting static files...
python manage.py collectstatic --noinput

REM Start Django server
echo Starting Django server...
start "Django Server" python manage.py runserver 8000

REM Setup UI
echo Setting up UI...
cd ..\ui

REM Install Node.js dependencies
echo Installing Node.js dependencies...
call yarn install

REM Build and start UI
echo Building and starting UI...
start "UI Server" yarn run dev --host 0.0.0.0

echo ================================================
echo FolioMan is now running:
echo API: http://localhost:8000/admin
echo UI: http://localhost:3000
echo PGAdmin: http://localhost:5050
echo ================================================
echo Press Ctrl+C in each terminal window to stop the servers
echo Run 'docker-compose -f docker-compose.local.yml down' to stop Docker services when done

cmd /k
