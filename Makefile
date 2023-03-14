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

run: $(PYTHON)
	set -o allexport
	source .env
	set +o allexport
	time ./feedme.py searches.txt atom.xml

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
	fly deploy

clean:
	rm -rf venv atom.xml

.PHONY: run launch secrets deploy clean
