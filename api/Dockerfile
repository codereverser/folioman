FROM ubuntu:focal 

ENV TZ Asia/Kolkata
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1
ENV DJANGO_ENV local
ENV DOCKER_CONTAINER 1


# Install mupdf==1.18.0
ARG MUPDF=1.18.0
RUN apt update && \
    apt -y install gcc g++ make libfreetype-dev wget pkg-config && \
    wget -c -q https://www.mupdf.com/downloads/archive/mupdf-${MUPDF}-source.tar.gz && \
    tar xf mupdf-${MUPDF}-source.tar.gz && \
    cd mupdf-${MUPDF}-source && \
    make HAVE_X11=no HAVE_GLUT=no prefix=/usr/local -j4 install && \
    cd .. && \
    rm -rf *.tar.gz mupdf-${MUPDF}-source && \
    apt -y purge gcc g++ make libfreetype-dev wget pkg-config && \
    apt -y autoremove && \
    rm -fr /var/lib/apt/lists/* /var/cache/apt/archives/*.deb /root/.cache /root/.cargo

COPY requirements.txt /api/requirements.txt
RUN apt update && \
    apt -y install gcc g++ make python3-lxml python3-pandas python3-numpy \
	        libfreetype-dev libssl-dev python3-psycopg2 python3-pip \
		wget python3-venv git libffi-dev rustc pkg-config && \
    python3 -m venv --system-site-packages /venv && \
    ln -s /usr/include/freetype2/ft2build.h /usr/include/ft2build.h && \
    ln -s /usr/include/freetype2/freetype/ /usr/include/freetype && \
    /venv/bin/pip install -U pip setuptools wheel && \
    /venv/bin/pip3 install -r /api/requirements.txt && \
    apt -y purge gcc g++ make libfreetype-dev libssl-dev libpq-dev libffi-dev rustc git wget pkg-config && \
    apt -y autoremove && \
    rm /usr/include/ft2build.h /usr/include/freetype && \
    rm -fr /var/lib/apt/lists/* /var/cache/apt/archives/*.deb /root/.cache /root/.cargo

RUN apt update && apt -y install curl git gcc && \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y -t armv7-unknown-linux-musleabihf && \
    git clone https://github.com/ufoscout/docker-compose-wait.git && \
    cd docker-compose-wait && \
    RUSTFLAGS='-C link-arg=-s'  /root/.cargo/bin/cargo build --release --target=armv7-unknown-linux-musleabihf && \
    cp target/armv7-unknown-linux-musleabihf/release/wait /wait && \
    cd .. && \
    apt -y purge curl git gcc && \
    apt -y autoremove && \
    /root/.cargo/bin/rustup self uninstall -y && \
    rm -fr /var/lib/apt/lists/* /var/cache/apt/archives/*.deb /root/.cache /root/.cargo /docker-compose-wait

RUN chmod +x /wait

WORKDIR /api/
EXPOSE 8000
