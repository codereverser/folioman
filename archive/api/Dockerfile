FROM codereverser/docker-compose-wait:latest

ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1
ENV DOCKER_CONTAINER 1


RUN apt -y update && \
    apt install -y gcc g++ git libpython3-dev python3-psycopg2 python3-venv && \
    python3 -m venv --system-site-packages /venv  && \
    /venv/bin/pip install -U pip setuptools wheel && \
    /venv/bin/pip3 install casparser-isin && \
    apt -y purge gcc g++ git && \
    apt -y autoremove && \
    rm -rf /var/lib/apt/lists /var/cache/apt/archives

COPY requirements.txt /api/requirements.txt
RUN /venv/bin/pip3 install -r /api/requirements.txt && rm -fr /root/.cache/pip

WORKDIR /api/
EXPOSE 8000
