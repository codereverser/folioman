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
