# folioman

Portfolio manager and analysis tools for investments in Indian mutual funds, stocks and other digital assets.

<img src="https://github.com/codereverser/folioman/raw/main/screenshots/mutualfunds/01.dashboard.png" alt="Demo Dashboard" width="720"/>

### Pre-requisites

folioman requires docker and docker-compose installed in your system
- [Install docker](https://docs.docker.com/get-docker/)
- [Install docker-compose](https://docs.docker.com/compose/install/)

### Setup
To run the project, create a file called `.env` in the `api` folder based on the 
template `api/env.template` and run

```bash
docker-compose up --build
```

Once the process is complete, visit the following urls to get started.

- http://localhost:8000 - frontend
- http://localhost:8000/admin/ - backend
- http://localhost:5050 - pgadmin

The default username and password is given below; it can be changed from the backend dashboard. 
```
username: admin
password: foliom4n
```

This will build the container images for backend, frontend and all dependent services
and may take quite a while to finish.

---

### Running Locally (Minimal Docker Setup)

If you want to run the application locally with minimal Docker usage (only loading timedb and cache services in Docker), follow these steps:

#### Step 1: Start Local Environment

**On Windows:**
```bash
run-local.bat
```

**On Linux/Mac:**
```bash
./run-local.sh
```

This will:
1. Start only the required Docker containers (timedb, cache, and pgadmin)
2. Set up a Python virtual environment
3. Install all Python dependencies
4. Configure the Django environment to connect to your local Docker containers
5. Run database migrations
6. Start the Django API server
7. Install Node.js dependencies for the UI
8. Start the UI development server

#### Step 2: Start Celery for Background Tasks (Optional)

**On Windows:**
```bash
run-celery-local.bat
```

This will start Celery worker and beat scheduler processes for background tasks.

#### Step 3: Access Your Application

Once everything is running, you can access:
- API: http://localhost:8000
- UI: http://localhost:3000
- PGAdmin: http://localhost:5050 (login: pgadmin4@pgadmin.org / admin)

#### Step 4: Shutting Down

Stop the Django and UI processes, then stop the Docker containers:
```bash
docker-compose -f docker-compose.local.yml down
```

## Features

### Asset classes
- Indian Mutual funds (_Work_In_Progress_)
- Indian Stocks (_TODO_)
- CryptoCurrencies (_TODO_)

### Screenshots
Some screenshots are available in the [screenshots](screenshots) folder


# Warning
The code is a work in progress and is in pre-alpha / proof-of-concept stage. 
It may have many critical bugs and it may also go through major 
backward-incompatible changes.
