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

First you will need `redis <http://redis.io/>`_, you can search for ``redis-server`` 
or ``redis`` package in your system packet manager. Or you can 
`install it from source <http://redis.io/download>`_.

Then install python redis client, clone cacheops and symlink it to your python path::

    $ pip install redis
    $ git clone git://github.com/Suor/django-cacheops.git
    $ ln -s `pwd`/django-cacheops/cacheops/ /somewhere/on/python/import/path


Setup
-----

Add ``cacheops`` to your ``INSTALLED_APPS`` first::

    INSTALLED_APPS = (
        'cacheops',
        ...
    )

Setup redis connection and enable caching for desired models::

    CACHEOPS_REDIS = {
        'host': 'localhost', # redis-server is on same machine
        'port': 6379,        # default redis port
        #'db': 1,            # SELECT non-default redis database
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
    
Usage
-----

| **Automatic caching.** It's automatic you just need to `set it up <#setup>`_.

| **Manual caching.** You can force any queryset to use cache by caling it's ``.cache()`` method::

    Article.objects.filter(tag=2).cache()
    
You can specify which ops should be cached for queryset, for example, this code::

    qs = Article.objects.filter(tag=2).cache(ops=['count'])
    paginator = Paginator(objects, ipp)
    artciles = list(pager.page(page_num)) # hits database

will cache ``.count()`` call in Paginator but not later in articles fetch. There are three
possible actions - ``get``, ``fetch`` and ``count``. You can pass any subset of ops
to ``.cache()`` method even empty to turn off caching. There are, however, 
a shortcut for it::

    qs = Article.objects.filter(visible=True).nocache()
    qs1 = qs.filter(tag=2)       # hits database
    qs2 = qs.filter(category=3)  # hits it once more
    
It is usefull when you want to disable automatic caching on particular queryset.

| **Function caching.** You can cache and invalidate result of a function same way is queryset::

    from cacheops import cacheoped_as
    
    @cacheoped_as(Article.objects.all())
    def article_stats():
        return {
            tags: Article.objects.values('tag').annotate(count=Count('id'))
            categories: Article.objects.values('category').annotate(count=Count('id'))
        }
        

Invalidation
------------

Cacheops uses both time and event-driven invalidation and is fully automatic.
The event-driven one listens on model signals and
invalidates appropriate caches on Model.save() and .delete().

Usually you won't need to do anything with it.
