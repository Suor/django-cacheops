Cacheops
========

A slick app that supports automatic or manual queryset caching and automatic
granular event-driven invalidation. It can also cache results of user functions
and invalidate them by time or the same way as querysets.


Requirements
------------
| Python 2.6, Django 1.2 and Redis 2.2.7.
| Later djangos not tested but will probably do as well.
  I'll appreciate any feedback positive or negative.


Installation
------------

First you will need `redis <http://redis.io/>`_, you can search for `redis` or
`redis-server` package in your system packet manager. Or you can
`install it from source <http://redis.io/download>`_.

Then install python redis client, clone cacheops and symlink it to your python path::

    $ pip install redis
    $ git clone git://github.com/Suor/django-cacheops.git
    $ ln -s `pwd`/django-cacheops/cacheops/ /somewhere/on/python/import/path


Setup
-----

Add ``cacheops`` to your ``INSTALLED_APPS`` before any apps that use it::

    INSTALLED_APPS = (
        'cacheops',
        ...
    )

Setup redis connection and enable caching for desired models::

    CACHEOPS_REDIS = {
        'host': 'localhost', # redis-server is on same machine
        'port': 6379,        # default redis port
        'db': 1,             # SELECT non-default redis database
                             # using separate redis db or redis instance
                             # is highly recommended
        'socket_timeout': 3,
    }

    CACHEOPS = {
        # Automatically cache any User.objects.get() calls for 15 minutes
        # This includes request.user or post.author access,
        # where Post.author is a foreign key to auth.User
        'auth.user': ('get', 60*15),

        # Automatically cache all gets, queryset fetches and counts
        # to other django.contrib.auth models for an hour
        'auth.*': ('all', 60*60),

        # Enable manual caching on all news models with default timeout of an hour
        # Use News.objects.cache().get(...)
        #  or Tags.objects.filter(...).order_by(...).cache()
        # to cache particular ORM request.
        # Invalidation is still automatic
        'news.*': ('just_enable', 60*60),

        # Automatically cache count requests for all other models for 15 min
        '*.*': ('count', 60*15),
    }


Invalidation
------------

Cacheops uses both time and event-driven invalidation and is fully automatic.
The event-driven one listens on model signals and
invalidates appropriate caches on Model.save() and .delete().

Usually you won't need to do anything with it.
