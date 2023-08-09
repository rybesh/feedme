FROM golang:latest as builder

WORKDIR /

# install python libs and scripts and generate initial feed

RUN apt-get update && apt-get install -y \
    python3-full \
    python3-setuptools \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv /venv
RUN set -ex && \
    /venv/bin/python -m pip install -r /tmp/requirements.txt && \
    rm -rf /root/.cache/
COPY feedme.py /
COPY config.py /
COPY searches.txt /
RUN mkdir -p /srv/http
RUN --mount=type=secret,id=APP_ID \
    --mount=type=secret,id=FEED_URL \
    --mount=type=secret,id=FEED_AUTHOR_NAME \
    --mount=type=secret,id=FEED_AUTHOR_EMAIL \
    APP_ID="$(cat /run/secrets/APP_ID)" \
    FEED_URL="$(cat /run/secrets/FEED_URL)" \
    FEED_AUTHOR_NAME="$(cat /run/secrets/FEED_AUTHOR_NAME)" \
    FEED_AUTHOR_EMAIL="$(cat /run/secrets/FEED_AUTHOR_EMAIL)" \
    /venv/bin/python /feedme.py /searches.txt /srv/http/index.xml

# install supercronic and crontab

RUN go install github.com/aptible/supercronic@latest
COPY crontab /
RUN supercronic -test ./crontab

# install goStatic

RUN go install github.com/PierreZ/goStatic@latest

# start supercronic and goStatic

COPY ./entrypoint.sh /
ENTRYPOINT ["./entrypoint.sh"]
