#!/usr/bin/make -f

VERSION = $$(cd .. && python3 setup.py -V)

SED_rm_python_usage_msg = -e '/end_of_description_header/,/positional arguments/{d}'
SED_fix_rst2man_quirks = -e 's,^ \\- ,,' -e 's,^\[,\\[char91],g'

all: reprotest.1

reprotest.1: reprotest.h2m ../reprotest/__init__.py
	help2man --version-string=$(VERSION) ./reprotest -N --include="$<" | \
	  sed $(SED_rm_python_usage_msg) > "$@"

reprotest.h2m: reprotest.h2m.0 ../README.rst Makefile
	{ cat reprotest.h2m.0; cat ../README.rst | \
	  rst2man --no-doc-title | \
	  sed $(SED_fix_rst2man_quirks) \
	      -e 's,\.SH \(.*\),[\1],g'; } > "$@"

.PHONY: clean
clean:
	$(RM) reprotest.1 reprotest.h2m
