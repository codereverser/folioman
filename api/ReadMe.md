
## Start in local 

assuming python is installed, open code in Visual Studio Code, right-click and select 'Run Python'.

This will prompt you to create a virtual environment, select `.venv`, and install all required software.

then open the terminal and issue the command below to set up lookup data

```shell 
python manage.py migrate
```

Issue below command to start local server

```shell
python manage.py runserver
```

## How to update the dependencies in virtual environment

```shell
pip install -r requirements.txt
```

## Running the Celery worker
In the new terminal tab, run the following command:

```shell
celery -A app.celery worker --loglevel=info
```

where celery is the version of Celery, with the -A option to specify the celery instance to use (in our case, it's celery in the app.py file, so it's app.celery), and worker is the subcommand to run the worker, and --loglevel=info to set the verbosity log level to INFO.
