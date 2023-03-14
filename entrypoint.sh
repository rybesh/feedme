#!/bin/sh
mkdir -p /srv/http
echo "Generating feed..."
python3 /feedme.py /searches.txt /srv/http/index.xml
echo "Starting cron..."
supercronic ./crontab &
echo "Starting web server..."
goStatic -fallback /index.xml
