Cacheops
========

A slick app that supports automatic or manual queryset caching and automatic
granular event-driven invalidation.

It uses `redis <http://redis.io/>`_ as backend for ORM cache and redis or
filesystem for simple time-invalidated one.

And there is more to it:

- decorator to cache any user function
- extension for jinja2 to cache template fragments
- a couple of hacks to make django faster


Requirements
------------

Python 2.6, Django 1.2 and Redis 2.2.7.


Installation
------------

Using pip::

    $ pip install django-cacheops

Or you can get latest one from github::

    $ git clone git://github.com/Suor/django-cacheops.git
    $ ln -s `pwd`/django-cacheops/cacheops/ /somewhere/on/python/path/


Setup
-----

Add ``cacheops`` to your ``INSTALLED_APPS`` before any apps that use it.

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

Usage
-----

| **Automatic caching.**

It's automatic you just need to set it up.

| **Manual caching.**

You can force any queryset to use cache by calling it's ``.cache()`` method::

    Article.objects.filter(tag=2).cache()

Here you can specify which ops should be cached for queryset, for example, this code::

    qs = Article.objects.filter(tag=2).cache(ops=['count'])
    paginator = Paginator(objects, ipp)
    articles = list(pager.page(page_num)) # hits database

will cache ``.count()`` call in Paginator but not later in articles fetch.
There are three possible actions - ``get``, ``fetch`` and ``count``. You can
pass any subset of this ops to ``.cache()`` method even empty to turn off caching.
There are, however, a shortcut for it::

    qs = Article.objects.filter(visible=True).nocache()
    qs1 = qs.filter(tag=2)       # hits database
    qs2 = qs.filter(category=3)  # hits it once more

It is usefull when you want to disable automatic caching on particular queryset.

| **Function caching.**

You can cache and invalidate result of a function the same way as a queryset.
Cache of next function will be invalidated on any Article change, addition
or deletetion::

    from cacheops import cacheoped_as

    @cacheoped_as(Article.objects.all())
    def article_stats():
        return {
            'tags': list( Article.objects.values('tag').annotate(count=Count('id')) )
            'categories': list( Article.objects.values('category').annotate(count=Count('id')) )
        }

Note that we are using list on both querysets here, it's because we don't want
to cache queryset objects but their result.

Also note that cache key does not depend on arguments of a function, so it's result
should not, either. This is done to enable caching of view functions. Instead
you should use a local function::

    def articles_block(category, count=5):

        @cacheoped_as(Article.objects.filter(category=category), extra=count)
        def _articles_block():
            qs = Article.objects.filter(category=category)
            articles = list(qs.filter(photo=True)[:count])

            if len(articles) < count:
                articles += list(qs[:count-len(articles)])

            return articles

        return _articles_block()

Using local function gives additional advantage: we can filter queryset used
in ``@cacheoped_as()`` to make invalidation more granular. We also add an
``extra`` to make diffrent keys for calls with same ``category`` but diffrent
``count``.


Invalidation
------------

Cacheops uses both time and event-driven invalidation. The event-driven one
listens on model signals and invalidates appropriate caches on Model.save()
and .delete().

Invalidation tries to be granular which means it won't invalidate a queryset
that cannot be influenced by added/updated/deleted object judjing by query
conditions. Most time this will do what you want, if it's not you can use one
of the following::

    from cacheops import invalidate_obj, invalidate_model

    invalidate_obj(some_article)  # invalidates queries affected by some_article
    invalidate_model(Article)     # invalidates all queries for model

And last there is ``invalidate`` command::

    ./manage.py invalidate articles.Artcile.34  # same as invalidate_obj
    ./manage.py invalidate articles.Article     # same as invalidate_model
    ./manage.py invalidate articles   # invalidate all models in articles

And the one that FLUSHES cacheops redis database::

    ./manage.py invalidate all

Don't use that if you share redis database for both cache and something else.


Jinja2 extension
----------------

Add ``cacheops.jinja2.cache`` to your extensions and use::

    {% cacheoped_as queryset [, timeout=<timeout>] [, extra=<key addition>] %}
        ... some template code ...
    {% endcacheoped_as %}

or

::

    {% cached queryset [, timeout=<timeout>] [, cache_key=<key addition>] %}
        ...
    {% cached %}


CAVEATS
-------

1. Conditions other than __exact or __in don't provide more granularity for
   invalidation.
2. Conditions on related models don't provide it either.
3. Update of "selected_related" object does not invalidate cache for queryset.
4. Mass updates don't trigger invalidation.
5. ORDER BY and LIMIT/OFFSET don't affect invalidation.

7. Conditions on subqueries don't affect invalidation.

9. Aggregates is not implemented yet.
10. Timeout in queryset and cacheoped_as cannot ne larger that default.

Here 1, 3, 5, 10 are part of design compromise, trying to solve them will make
things complicated and slow. 2 and 7 can be implemented if needed, but it's
probably counter-productive since one can just break queries into simple ones,
which cache better. 4 is a deliberate choice, making it "right" will flush
cache too much when update conditions are orthogonal to most queries conditions.


TODO
----

- docs about simple cache
- docs about file cache
- add .delete(cache_key) method to simple and file cache
- .invalidate() method on simple cached funcs
- queryset brothers
- jinja2 tag for "get random of some list" block with lazy rendering
