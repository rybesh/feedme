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

searches.txt: searches-manual.txt searches-automatic.txt
	cat $^ > $@

run: searches.txt | $(PYTHON)
	time ./feedme.py $< atom.xml

launch:
	fly launch \
	--auto-confirm \
	--copy-config \
	--ignorefile .dockerignore \
	--dockerfile Dockerfile \
	--region $(REGION) \
	--name $(APP)
	@echo "Next: make secrets"

secrets:
	cat .env | fly secrets import
	@echo
	fly secrets list
	@echo "Next: make deploy"

deploy:
	source .env && \
	fly deploy \
	--build-secret APP_ID="$$APP_ID" \
	--build-secret FEED_URL="$$FEED_URL" \
	--build-secret FEED_AUTHOR_NAME="$$FEED_AUTHOR_NAME" \
	--build-secret FEED_AUTHOR_EMAIL="$$FEED_AUTHOR_EMAIL"

clean:
	rm -rf venv atom.xml searches.txt

.PHONY: run launch secrets deploy clean
