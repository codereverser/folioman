FROM codereverser/docker-compose-wait:latest

ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1
ENV DOCKER_CONTAINER 1


RUN dnf install -y gcc g++ git \
                   python3-cryptography python3-devel python3-lxml \
                   python3-numpy python3-pandas python3-psycopg2 \
                   python3-PyMuPDF python3-yaml && \
    python3 -m venv --system-site-packages /venv  && \
    /venv/bin/pip install -U pip setuptools wheel && \
    /venv/bin/pip3 install casparser-isin && \
    dnf -y remove gcc g++ git && \
    dnf -y autoremove && \
    dnf -y clean all

COPY requirements.txt /api/requirements.txt
RUN /venv/bin/pip3 install -r /api/requirements.txt

WORKDIR /api/
EXPOSE 8000