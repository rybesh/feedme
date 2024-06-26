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

run: ../deals/searches.pickle searches.txt | $(PYTHON)
	cp $< searches.pickle
	time ./feedme.py searches.txt searches.pickle index.xml

secrets:
	cat .env | fly secrets import
	@echo
	fly secrets list

update_searches: ../deals/searches.pickle searches.txt
	cp $< searches.pickle
	rsync searches.txt $(APP).internal:searches.txt
	rsync searches.pickle $(APP).internal:searches.pickle

deploy: ../deals/searches.pickle searches.txt
	cp $< searches.pickle
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
