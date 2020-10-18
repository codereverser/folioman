FROM python:3.8-slim

ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1
ENV DJANGO_ENV local
ENV DOCKER_CONTAINER 1

RUN apt update && apt install -y gcc git
RUN pip install -U pip setuptools wheel

COPY requirements.txt /api/requirements.txt
RUN pip install -r /api/requirements.txt

ADD https://github.com/ufoscout/docker-compose-wait/releases/download/2.7.3/wait /wait
RUN chmod +x /wait

WORKDIR /api/
EXPOSE 8000
