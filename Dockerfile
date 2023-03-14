FROM golang:latest as builder

WORKDIR /

# install supercronic and crontab

RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*
ENV SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/v0.2.2/supercronic-linux-amd64 \
    SUPERCRONIC=supercronic-linux-amd64 \
    SUPERCRONIC_SHA1SUM=2319da694833c7a147976b8e5f337cd83397d6be
RUN curl -fsSLO "$SUPERCRONIC_URL" \
 && echo "${SUPERCRONIC_SHA1SUM}  ${SUPERCRONIC}" | sha1sum -c - \
 && chmod +x "$SUPERCRONIC" \
 && mv "$SUPERCRONIC" "/usr/local/bin/${SUPERCRONIC}" \
 && ln -s "/usr/local/bin/${SUPERCRONIC}" /usr/local/bin/supercronic
COPY crontab /
RUN supercronic -test ./crontab

# install python libs and scripts and generate initial feed

RUN apt-get update && apt-get install -y \
    python3 \
    python3-setuptools \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
COPY requirements.txt /tmp/requirements.txt
RUN set -ex && \
    pip install --upgrade pip && \
    pip install -r /tmp/requirements.txt && \
    rm -rf /root/.cache/
COPY feedme.py /
COPY config.py /
COPY searches.txt /

# install goStatic

RUN go install github.com/PierreZ/goStatic@latest

# start supercronic and goStatic

COPY ./entrypoint.sh /
ENTRYPOINT ["./entrypoint.sh"]
