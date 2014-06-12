Cacheops |Build Status|
========

A slick app that supports automatic or manual queryset caching and automatic
granular event-driven invalidation.

It uses `redis <http://redis.io/>`_ as backend for ORM cache and redis or
filesystem for simple time-invalidated one.

And there is more to it:

- decorators to cache any user function or view as a queryset or by time
- extensions for django and jinja2 templates to cache template fragments as querysets or by time
- concurrent file cache with a decorator
- a couple of hacks to make django faster


Requirements
------------

Python 2.6+ or 3.3+, Django 1.3+ and Redis 2.6+.


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

Setup redis connection and enable caching for desired models:

.. code:: python

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


Additionally, you can tell cacheops to degrade gracefully on redis fail with:

.. code:: python

    CACHEOPS_DEGRADE_ON_FAILURE = True

There is also a possibility to make all cacheops methods and decorators no-op, e.g. for testing:

.. code:: python

    CACHEOPS_FAKE = True


Usage
-----

| **Automatic caching.**

It's automatic you just need to set it up.

| **Manual caching.**

You can force any queryset to use cache by calling it's ``.cache()`` method:

.. code:: python

    Article.objects.filter(tag=2).cache()


Here you can specify which ops should be cached for queryset, for example, this code:

.. code:: python

    qs = Article.objects.filter(tag=2).cache(ops=['count'])
    paginator = Paginator(objects, ipp)
    articles = list(pager.page(page_num)) # hits database


will cache count call in ``Paginator`` but not later articles fetch.
There are three possible actions - ``get``, ``fetch`` and ``count``. You can
pass any subset of this ops to ``.cache()`` method even empty - to turn off caching.
There is, however, a shortcut for it:

.. code:: python

    qs = Article.objects.filter(visible=True).nocache()
    qs1 = qs.filter(tag=2)       # hits database
    qs2 = qs.filter(category=3)  # hits it once more


It is useful when you want to disable automatic caching on particular queryset.

You can also override default timeout for particular queryset with ``.cache(timeout=...)``
or make queryset only write cache, but don't try to fetch it with ``.cache(write_only=True)``.


| **Function caching.**

You can cache and invalidate result of a function the same way as a queryset.
Cache of the next function will be invalidated on any ``Article`` change, addition
or deletion:

.. code:: python

    from cacheops import cached_as

    @cached_as(Article, timeout=120)
    def article_stats():
        return {
            'tags': list( Article.objects.values('tag').annotate(count=Count('id')) )
            'categories': list( Article.objects.values('category').annotate(count=Count('id')) )
        }


Note that we are using list on both querysets here, it's because we don't want
to cache queryset objects but their results.

Also note that if you want to filter queryset based on arguments,
e.g. to make invalidation more granular, you can use a local function:

.. code:: python

    def articles_block(category, count=5):

        @cached_as(Article.objects.filter(category=category), extra=count)
        def _articles_block():
            qs = Article.objects.filter(category=category)
            articles = list(qs.filter(photo=True)[:count])

            if len(articles) < count:
                articles += list(qs[:count-len(articles)])

            return articles

        return _articles_block()


We added ``extra`` here to make different keys for calls with same ``category`` but different
``count``. Cache key will also depend on function arguments, so we could just pass ``count`` as
an argument to inner function. We also omitted ``timeout`` here, so a default for the model
will be used.

Another possibility is to make function cache invalidate on changes to any one of several models:

.. code:: python

    @cached_as(Article.objects.filter(public=True), Tag)
    def article_stats():
        return {...}

As you can see, we can mix querysets and models here.

| **View caching.**

You can also cache and invalidate a view as a queryset. This works mostly the same way as function
caching, but only path of the request parameter is used to construct cache key:

.. code:: python

    from cacheops import cached_view_as

    @cached_view_as(News)
    def news_index(request):
        # ...
        return HttpResponse(...)

You can pass ``timeout``, ``extra`` and several samples the same way as to ``@cached_as()``.


Invalidation
------------

Cacheops uses both time and event-driven invalidation. The event-driven one
listens on model signals and invalidates appropriate caches on ``Model.save()``, ``.delete()``
and m2m changes.

Invalidation tries to be granular which means it won't invalidate a queryset
that cannot be influenced by added/updated/deleted object judging by query
conditions. Most of the time this will do what you want, if it won't you can use
one of the following:

.. code:: python

    from cacheops import invalidate_obj, invalidate_model, invalidate_all

    invalidate_obj(some_article)  # invalidates queries affected by some_article
    invalidate_model(Article)     # invalidates all queries for model
    invalidate_all()              # flush redis cache database

And last there is ``invalidate`` command::

    ./manage.py invalidate articles.Article.34  # same as invalidate_obj
    ./manage.py invalidate articles.Article     # same as invalidate_model
    ./manage.py invalidate articles   # invalidate all models in articles

And the one that FLUSHES cacheops redis database::

    ./manage.py invalidate all

Don't use that if you share redis database for both cache and something else.


Multiple database support
-------------------------

By default cacheops considers query result is same for same query, not depending
on database queried. That could be changed with ``db_agnostic`` cache profile option:

.. code:: python

    CACHEOPS = {
        'some.model': ('get', TIMEOUT, {'db_agnostic': False}),
        # ...
    }


Simple time-invalidated cache
-----------------------------

To cache result of a function call or a view for some time use:

.. code:: python

    from cacheops import cached, cached_view

    @cached(timeout=number_of_seconds)
    def top_articles(category):
        return ... # Some costly queries

    @cached_view(timeout=number_of_seconds)
    def top_articles(request, category=None):
        # Some costly queries
        return HttpResponse(...)


``@cached()`` will generate separate entry for each combination of decorated function and its
arguments. Also you can use ``extra`` same way as in ``@cached_as()``, most useful for nested
functions:

.. code:: python

    @property
    def articles_json(self):
        @cached(timeout=10*60, extra=self.category)
        def _articles_json():
            ...
            return json.dumps(...)

        return _articles_json()


You can manually invalidate cached function result this way:

.. code:: python

    top_articles.invalidate(some_category)


Cacheops also provides get/set primitives for simple cache:

.. code:: python

    from cacheops import cache

    cache.set(cache_key, data, timeout=None)
    cache.get(cache_key)
    cache.delete(cache_key)


``cache.get`` will raise ``CacheMiss`` if nothing is stored for given key:

.. code:: python

    from cacheops import cache, CacheMiss

    try:
        result = cache.get(key)
    except CacheMiss:
        ... # deal with it


File Cache
----------

File based cache can be used the same way as simple time-invalidated one:

.. code:: python

    from cacheops import file_cache

    @file_cache.cached(timeout=number_of_seconds)
    def top_articles(category):
        return ... # Some costly queries

    @file_cache.cached_view(timeout=number_of_seconds)
    def top_articles(request, category):
        # Some costly queries
        return HttpResponse(...)

    # later, on appropriate event
    top_articles.invalidate(some_category)

    # primitives
    file_cache.set(cache_key, data, timeout=None)
    file_cache.get(cache_key)
    file_cache.delete(cache_key)


It have several improvements upon django built-in file cache, both about high load.
First, it is safe against concurrent writes. Second, it's invalidation is done as separate task,
you'll need to call this from crontab for that to work::

    /path/manage.py cleanfilecache


Django templates integration
----------------------------

Cacheops provides tags to cache template fragments for Django 1.4+. They mimic ``@cached_as``
and ``@cached`` decorators, however, they require explicit naming of each fragment:

.. code:: django

    {% load cacheops %}

    {% cached_as <queryset> <timeout> <fragment_name> [<extra1> <extra2> ...] %}
        ... some template code ...
    {% endcached_as %}

    {% cached <timeout> <fragment_name> [<extra1> <extra2> ...] %}
        ... some template code ...
    {% endcached %}

You can use ``0`` for timeout in ``@cached_as`` to use it's default value for model.


Jinja2 extension
----------------

Add ``cacheops.jinja2.cache`` to your extensions and use:

.. code:: jinja

    {% cached_as <queryset> [, timeout=<timeout>] [, extra=<key addition>] %}
        ... some template code ...
    {% endcached_as %}

or

.. code:: jinja

    {% cached [timeout=<timeout>] [, extra=<key addition>] %}
        ...
    {% endcached %}

Tags work the same way as corresponding decorators.


CAVEATS
-------

1. Conditions other than ``__exact``, ``__in`` and ``__isnull=True`` don't make invalidation
   more granular.
2. Conditions on TextFields, FileFields and BinaryFields don't make it either.
   One should not test on their equality anyway.
3. Update of "selected_related" object does not invalidate cache for queryset.
4. Mass updates don't trigger invalidation.
5. ORDER BY and LIMIT/OFFSET don't affect invalidation.
6. Doesn't work with RawQuerySet.
7. Conditions on subqueries don't affect invalidation.
8. Doesn't work right with multi-table inheritance.
9. Aggregates are not implemented yet.

Here 1, 2, 3, 5 are part of design compromise, trying to solve them will make
things complicated and slow. 7 can be implemented if needed, but it's
probably counter-productive since one can just break queries into simpler ones,
which cache better. 4 is a deliberate choice, making it "right" will flush
cache too much when update conditions are orthogonal to most queries conditions.
6 can be cached as ``SomeModel.objects.all()`` but ``@cached_as()`` someway covers that
and is more flexible. 8 is postponed until it will gain more interest or a champion willing to
implement it emerge.


Performance tips
----------------

Here come some performance tips to make cacheops and Django ORM faster.

1. When you use cache you pickle and unpickle lots of django model instances, which could be slow. You can optimize django models serialization with `django-pickling <http://github.com/Suor/django-pickling>`_.

2. Constructing querysets is rather slow in django, mainly because most of ``QuerySet`` methods clone self, then change it and return a clone. Original queryset is usually thrown away. Cacheops adds ``.inplace()`` method, which makes queryset mutating, preventing useless cloning::

    items = Item.objects.inplace().filter(category=12).order_by('-date')[:20]

   You can revert queryset to cloning state using ``.cloning()`` call.

   Note that this is a micro-optimization technique. Using it is desirable in most hot places, but not everywhere.

3. More to 2, there is a `bug in django 1.4- <https://code.djangoproject.com/ticket/16759>`_,
   which sometimes makes queryset cloning very slow. You can use any patch from this ticket to fix it.

4. Use template fragment caching when possible, it's way more fast because you don't need to generate anything. Also pickling/unpickling a string is much faster than list of model instances.

5. Run separate redis instance for cache with disabled `persistence <http://redis.io/topics/persistence>`_. You can manually call `SAVE <http://redis.io/topics/persistence>`_ or `BGSAVE <http://redis.io/commands/bgsave>`_ to stay hot upon server restart.

6. If you filter queryset on many different or complex conditions cache could degrade performance (comparing to uncached db calls) in consequence of frequent cache misses. Disable cache in such cases entirely or on some heuristics which detect if this request would be probably hit. E.g. enable cache if only some primary fields are used in filter.

   Caching querysets with large amount of filters also slows down all subsequent invalidation on that model. You can disable caching if more than some amount of fields is used in filter simultaneously.


Writing a test
--------------

Writing a test for an issue you are having can speed up its resolution a lot. Here is how you do that. I am supposing you have some application code causing it.

1. Make a fork.
2. Install all from `test_requirements.txt`.
3. Ensure you can run tests with `./run_tests.py`.
4. Copy relevant models code to https://github.com/Suor/django-cacheops/blob/master/tests/models.py
5. Go to https://github.com/Suor/django-cacheops/blob/master/tests/tests.py and paste code causing exception to `IssueTests.test_{issue_number}`.
6. Execute `./run_tests.py IssueTests.test_{issue_number}` and see it failing.
7. Cut down model and test code until error disappears and make a step back.
8. Commit changes and make a pull request.


TODO
----

- disable cache if select_for_update() called (or if _for_write set?)
- add local cache (cleared at the and of request?)
- better support transactions
- a way to turn off or postpone invalidation
- faster .get() handling for simple cases such as get by pk/id, with simple key calculation
- integrate with prefetch_related()
- fast mode: store cache in local memory, but check in with redis if it's valid
- shard cache between multiple redises
- lazy methods on querysets (calculate cache key from methods called)


.. |Build Status| image:: https://travis-ci.org/Suor/django-cacheops.svg?branch=master
   :target: https://travis-ci.org/Suor/django-cacheops
