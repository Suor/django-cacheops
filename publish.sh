#!/usr/bin/bash

set -ex

NAME=django-cacheops
VERSION=`awk '/__version__ = /{gsub(/'\''/, "", $3); print $3}' cacheops/__init__.py`

python setup.py sdist bdist_wheel
twine check dist/$NAME-$VERSION*
twine upload --skip-existing -uSuor dist/$NAME-$VERSION*
