.PHONY: help install test compile check

help:
	@printf '%s\n' \
	  'make install  - editable install with MCP dependencies' \
	  'make test     - run unit tests' \
	  'make compile  - byte-compile host and scripts' \
	  'make check    - tests + compile + whitespace check'

install:
	python -m pip install -e '.[mcp,share]'

test:
	python -m unittest discover -s tests -v

compile:
	python -m compileall -q host scripts

check: test compile
	git diff --check
