
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