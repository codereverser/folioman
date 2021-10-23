"api/Dockerfile" 27L, 821C                                                                            21,47         All
FROM fedora:36

ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1
ENV DOCKER_CONTAINER 1

# Install docker-compose-wait
ADD https://github.com/ufoscout/docker-compose-wait/releases/download/2.9.0/wait /wait
RUN chmod +x /wait

COPY requirements.txt /api/requirements.txt

RUN dnf -y update && \
    dnf install -y gcc g++ git \
                   python3-cryptography python3-devel python3-lxml \
                   python3-numpy python3-pandas python3-psycopg2 \
                   python3-PyMuPDF python3-yaml && \
    python3 -m venv --system-site-packages /venv  && \
    /venv/bin/pip install -U pip setuptools wheel && \
    /venv/bin/pip3 install -r /api/requirements.txt && \
    dnf -y remove gcc g++ git python3-devel && \
    dnf -y autoremove && \
    dnf -y clean all

WORKDIR /api/
EXPOSE 8000