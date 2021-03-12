# PP Django Cacheops

This is a fork of https://github.com/Suor/django-cacheops to add some functionality for redis to work with cluster mode

## Development guide
1. Clone this repo
```
git clone git@git.parcelperform.com:dev-team/pp_django_cacheops.git
```

2. Setup fork repo for futher update
```
git remote add fork https://github.com/Suor/django-cacheops.git
```

3. Code!

Note that the main part is in cacheops/cluster folder. Most of the cluster code will be in that folder.

## Usage guide
[Click here](README.rst)

## Change log
[Click here](PP_CHANGELOG.MD)

## Documentation
https://docs.google.com/document/d/1luNTSqhCmGiERJXFD0jJaKzlhIRAC74G6yy75pMWd4o/edit?usp=sharing

## New Feature from original cacheops
- Added timeout for every redis command
  - CACHEOPS_TIMEOUT_HANDLER = function to handle timeout error
  - CACHEOPS_REDIS_CONNECTION_TIMEOUT = number of seconds allowed for each command
- Support redis cluster
  - CACHEOPS_CLUSTER_ENABLED = boolean to enable mode
