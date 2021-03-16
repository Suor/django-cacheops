#!/bin/bash

#rm -r pp_django_cacheops.egg-info
#rm dist/*
#python setup.py bdist_wheel

git clone git@git.parcelperform.com:dev-team/pp_django_cacheops.git /pp_django_cacheops_clone
echo "project cloned"
cd /pp_django_cacheops_clone
VERSION=`cat VERSION`
output=$(git tag -a $VERSION -m $VERSION 2>&1)
if [[ $output == *"fatal"* ]]; then
  echo $output
  exit 1
fi
git push origin --tags
