PYTHON = ./venv/bin/python
PIP = ./venv/bin/python -m pip

$(PYTHON):
	python3 -m venv venv
	$(PIP) install --upgrade pip
	$(PIP) install wheel
	$(PIP) install -r requirements.txt

run: $(PYTHON)
	time ./feedme.py searches.txt atom.xml

clean:
	rm -rf venv atom.xml

default: run

.PHONY: run clean
