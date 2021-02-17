FROM python:3.6.12-slim-buster

# Prevent stale pyc file issue
ENV PYTHONDONTWRITEBYTECODE=true

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    libffi-dev gettext postgresql-client gcc libpq-dev libxml2-dev zlib1g-dev libxslt-dev ca-certificates python3-dev git openssh-client g++ locales-all procps libmagic-dev && apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* /var/cache/* \
    && pip --no-cache-dir install --upgrade pip

RUN mkdir -p /srv/logs
WORKDIR /srv/pp_django_cacheops

ADD requirements.txt ./
RUN pip install -r requirements.txt --no-cache-dir

ADD . ./
ENTRYPOINT ["python", "./run_tests_cluster.py"]
CMD []
