#!/bin/sh

curl http://deals.internal:8043/searches.pickle -o /searches.pickle

/venv/bin/python \
    /feedme.py \
    /searches.txt \
    /searches.pickle \
    /srv/http/index.xml
