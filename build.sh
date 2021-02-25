#!/bin/bash
docker stop pp_django_cacheops
docker rm pp_django_cacheops
docker build -t parcelperform/pp_django_cacheops:master .
docker run -it --name pp_django_cacheops --network parcel_perform --entrypoint /bin/bash --env-file=./env_files/local_pp.env -v $PWD:/srv/pp_django_cacheops -v $PWD/../logs:/srv/logs parcelperform/pp_django_cacheops:master
