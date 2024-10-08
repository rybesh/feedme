SHELL = /bin/bash
PYTHON := ./venv/bin/python
PIP := ./venv/bin/python -m pip
APP := feedme
REGION := iad
.DEFAULT_GOAL := run

$(PYTHON):
	python3 -m venv venv
	$(PIP) install --upgrade pip
	$(PIP) install wheel
	$(PIP) install -r requirements.txt

searches.pickle:
	curl http://deals.internal:8043/$@ -o $@

run: searches.pickle searches.txt | $(PYTHON)
	time ./feedme.py searches.txt searches.pickle index.xml

secrets:
	cat .env | fly secrets import
	@echo
	fly secrets list

deploy: searches.pickle searches.txt
	source .env && \
	caffeinate -s \
	fly deploy \
	--build-secret APP_ID="$$APP_ID" \
	--build-secret CERT_ID="$$CERT_ID" \
	--build-secret FEED_URL="$$FEED_URL" \
	--build-secret FEED_AUTHOR_NAME="$$FEED_AUTHOR_NAME" \
	--build-secret FEED_AUTHOR_EMAIL="$$FEED_AUTHOR_EMAIL"

clean:
	rm -rf venv searches.pickle

.PHONY: run launch secrets deploy clean
