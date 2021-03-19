FROM ubuntu:focal 

ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1
ENV DOCKER_CONTAINER 1

# Install docker-compose-wait
RUN apt update && apt -y install curl git gcc && \
    curl -sSf https://sh.rustup.rs | sh -s -- -y && \
    git clone https://github.com/ufoscout/docker-compose-wait.git && \
    cd docker-compose-wait && \
    RUSTFLAGS='-C link-arg=-s' /root/.cargo/bin/cargo build --release && \
    cp target/release/wait /wait && \
    cd .. && \
    apt -y purge curl git gcc && \
    apt -y autoremove && \
    /root/.cargo/bin/rustup self uninstall -y && \
    rm -fr /var/lib/apt/lists/* /var/cache/apt/archives/*.deb && \
    rm -fr /root/.cache /root/.cargo /docker-compose-wait

RUN chmod +x /wait

# Install mupdf==1.18.0
ARG MUPDF=1.18.0
RUN apt update && \
    DEBIAN_FRONTEND=noninteractive TZ=Asia/Kolkata apt -y install gcc g++ make \
         libfreetype-dev wget pkg-config && \
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
    DEBIAN_FRONTEND=noninteractive TZ=Asia/Kolkata apt -y install g++ gcc git make \
            libffi-dev libfreetype-dev libssl-dev pkg-config python3-lxml python3-numpy \
            python3-pandas python3-pip python3-psycopg2 python3-venv rustc wget && \
    python3 -m venv --system-site-packages /venv && \
    ln -s /usr/include/freetype2/ft2build.h /usr/include/ft2build.h && \
    ln -s /usr/include/freetype2/freetype/ /usr/include/freetype && \
    /venv/bin/pip install -U pip setuptools wheel && \
    /venv/bin/pip3 install -r /api/requirements.txt && \
    apt -y purge g++ gcc git make libffi-dev libfreetype-dev libssl-dev pkg-config rustc wget && \
    apt -y autoremove && \
    rm /usr/include/ft2build.h /usr/include/freetype && \
    rm -fr /var/lib/apt/lists/* /var/cache/apt/archives/*.deb /root/.cache /root/.cargo

WORKDIR /api/
EXPOSE 8000
