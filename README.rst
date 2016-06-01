Cacheops |Build Status| |Gitter|
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

| Python 2.7 or 3.3+, Django 1.7+ and Redis 2.6+.
| **Note:** use cacheops 2.4.3 for older Djangos and Python.


Installation
------------

Using pip::

    $ pip install django-cacheops

Or you can get latest one from github::

    $ git clone git://github.com/Suor/django-cacheops.git
    $ ln -s `pwd`/django-cacheops/cacheops/ /somewhere/on/python/path/


Setup
-----

Add ``cacheops`` to your ``INSTALLED_APPS``.

Setup redis connection and enable caching for desired models:

.. code:: python

    CACHEOPS_REDIS = {
        'host': 'localhost', # redis-server is on same machine
        'port': 6379,        # default redis port
        'db': 1,             # SELECT non-default redis database
                             # using separate redis db or redis instance
                             # is highly recommended

        'socket_timeout': 3,   # connection timeout in seconds, optional
        'password': '...',     # optional
        'unix_socket_path': '' # replaces host and port
    }

    # Alternatively the redis connection can be defined using a URL:
    CACHEOPS_REDIS = "redis://localhost:6379/1"
    # or
    CACHEOPS_REDIS = "unix://path/to/socket?db=1"
    # or with password (note a colon)
    CACHEOPS_REDIS = "redis://:password@localhost:6379/1"

    CACHEOPS = {
        # Automatically cache any User.objects.get() calls for 15 minutes
        # This includes request.user or post.author access,
        # where Post.author is a foreign key to auth.User
        'auth.user': {'ops': 'get', 'timeout': 60*15},

        # Automatically cache all gets and queryset fetches
        # to other django.contrib.auth models for an hour
        'auth.*': {'ops': ('fetch', 'get'), 'timeout': 60*60},

        # Cache gets, fetches, counts and exists to Permission
        # 'all' is just an alias for ('get', 'fetch', 'count', 'exists')
        'auth.permission': {'ops': 'all', 'timeout': 60*60},

        # Enable manual caching on all other models with default timeout of an hour
        # Use Post.objects.cache().get(...)
        #  or Tags.objects.filter(...).order_by(...).cache()
        # to cache particular ORM request.
        # Invalidation is still automatic
        '*.*': {'ops': (), 'timeout': 60*60},

        # And since ops is empty by default you can rewrite last line as:
        '*.*': {'timeout': 60*60},
    }

You can configure default profile setting with ``CACHEOPS_DEFAULTS``. This way you can rewrite the config above:

.. code:: python

    CACHEOPS_DEFAULTS = {
        'timeout': 60*60
    }
    CACHEOPS = {
        'auth.user': {'ops': 'get', 'timeout': 60*15},
        'auth.*': {'ops': ('fetch', 'get')},
        'auth.permission': {'ops': 'all'},
        '*.*': {},
    }

Besides ``ops`` and ``timeout`` options you can also use:

``local_get: True``
    To cache simple gets for this model in process local memory.
    This is very fast, but is not invalidated in any way until process is restarted.
    Still could be useful for extremely rarely changed things.

``cache_on_save=True | 'field_name'``
    To write an instance to cache upon save.
    Cached instance will be retrieved on ``.get(field_name=...)`` request.
    Setting to ``True`` causes caching by primary key.

Additionally, you can tell cacheops to degrade gracefully on redis fail with:

.. code:: python

    CACHEOPS_DEGRADE_ON_FAILURE = True

There is also a possibility to make all cacheops methods and decorators no-op, e.g. for testing:

.. code:: python

    from django.test import override_settings

    @override_settings(CACHEOPS_ENABLED=False)
    def test_something():
        # ...
        assert cond


Usage
-----

| **Automatic caching**

It's automatic you just need to set it up.


| **Manual caching**

You can force any queryset to use cache by calling it's ``.cache()`` method:

.. code:: python

    Article.objects.filter(tag=2).cache()


Here you can specify which ops should be cached for queryset, for example, this code:

.. code:: python

    qs = Article.objects.filter(tag=2).cache(ops=['count'])
    paginator = Paginator(objects, ipp)
    articles = list(pager.page(page_num)) # hits database


will cache count call in ``Paginator`` but not later articles fetch.
There are four possible actions - ``get``, ``fetch``, ``count`` and ``exists``. You can
pass any subset of this ops to ``.cache()`` method even empty - to turn off caching.
There is, however, a shortcut for the latter:

.. code:: python

    qs = Article.objects.filter(visible=True).nocache()
    qs1 = qs.filter(tag=2)       # hits database
    qs2 = qs.filter(category=3)  # hits it once more


It is useful when you want to disable automatic caching on particular queryset.

You can also override default timeout for particular queryset with ``.cache(timeout=...)``
or make queryset only write cache, but don't try to fetch it with ``.cache(write_only=True)``.


| **Function caching**

You can cache and invalidate result of a function the same way as a queryset.
Cached results of the next function will be invalidated on any ``Article`` change,
addition or deletion:

.. code:: python

    from cacheops import cached_as

    @cached_as(Article, timeout=120)
    def article_stats():
        return {
            'tags': list(Article.objects.values('tag').annotate(Count('id')))
            'categories': list(Article.objects.values('category').annotate(Count('id')))
        }


Note that we are using list on both querysets here, it's because we don't want
to cache queryset objects but their results.

Also note that if you want to filter queryset based on arguments,
e.g. to make invalidation more granular, you can use a local function:

.. code:: python

    def articles_block(category, count=5):
        qs = Article.objects.filter(category=category)

        @cached_as(qs, extra=count)
        def _articles_block():
            articles = list(qs.filter(photo=True)[:count])
            if len(articles) < count:
                articles += list(qs.filter(photo=False)[:count-len(articles)])
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


| **View caching**

You can also cache and invalidate a view as a queryset. This works mostly the same way as function
caching, but only path of the request parameter is used to construct cache key:

.. code:: python

    from cacheops import cached_view_as

    @cached_view_as(News)
    def news_index(request):
        # ...
        return HttpResponse(...)

You can pass ``timeout``, ``extra`` and several samples the same way as to ``@cached_as()``.

Class based views can also be cached:

.. code:: python

    class NewsIndex(ListView):
        model = News

    news_index = cached_view_as(News)(NewsIndex.as_view())


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


| **Turning off and postponing invalidation**

There is also a way to turn off invalidation for a while:

.. code:: python

    from cacheops import no_invalidation

    with no_invalidation:
        # ... do some changes
        obj.save()

Also works as decorator:

.. code:: python

    @no_invalidation
    def some_work(...):
        # ... do some changes
        obj.save()

Combined with ``try ... finally`` it could be used to postpone invalidation:

.. code:: python

    try:
        with no_invalidation:
            # ...
    finally:
        invalidate_obj(...)
        # ... or
        invalidate_model(...)

Postponing invalidation can speed up batch jobs.


| **Mass updates**

Normally `qs.update(...)` doesn't emit any events and thus doesn't trigger invalidation.
And there is no transparent and efficient way to do that: trying to act on conditions will
invalidate too much if update conditions are orthogonal to many queries conditions,
and to act on specific objects we will need to fetch all of them,
which `QuerySet.update()` users generally try to avoid.

In the case you actually want to perform the latter cacheops provides a shortcut:

.. code:: python

    qs.invalidated_update(...)

Note that all the updated objects are fetched twice, prior and post the update.


Using memory limit
------------------

If your cache never grows too large you may not bother. But if you do you have some options.
Cacheops stores cached data along with invalidation data,
so you can't just set ``maxmemory`` and let redis evict at its will.
For now cacheops offers 2 imperfect strategies, which are considered **experimental**.
So be careful and consider `leaving feedback <https://github.com/Suor/django-cacheops/issues/143>`_.

First strategy is configuring ``maxmemory-policy volatile-ttl``. Invalidation data is guaranteed to have higher TTL than referenced keys.
Redis however doesn't guarantee perfect TTL eviction order, it selects several keys and removes
one with the least TTL, thus invalidator could be evicted before cache key it refers leaving it orphan and causing it survive next invalidation.
You can reduce this chance by increasing ``maxmemory-samples`` redis config option and by reducing cache timeout.

Second strategy, probably more efficient one is adding ``CACHEOPS_LRU = True`` to your settings and then using ``maxmemory-policy volatile-lru``.
However, this makes invalidation structures persistent, they are still removed on associated events, but in absence of them can clutter redis database.


Multiple database support
-------------------------

By default cacheops considers query result is same for same query, not depending
on database queried. That could be changed with ``db_agnostic`` cache profile option:

.. code:: python

    CACHEOPS = {
        'some.model': {'ops': 'get', 'db_agnostic': False, 'timeout': ...}
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
        @cached(timeout=10*60, extra=self.category_id)
        def _articles_json():
            ...
            return json.dumps(...)

        return _articles_json()


You can manually invalidate or update a result of a cached function:

.. code:: python

    top_articles.invalidate(some_category)
    top_articles.key(some_category).set(new_value)


To invalidate cached view you can pass absolute uri instead of request:

.. code:: python

    top_articles.invalidate('http://example.com/page', some_category)


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
    # or
    top_articles.key(some_category).set(some_value)

    # primitives
    file_cache.set(cache_key, data, timeout=None)
    file_cache.get(cache_key)
    file_cache.delete(cache_key)


It has several improvements upon django built-in file cache, both about high load.
First, it's safe against concurrent writes. Second, it's invalidation is done as separate task,
you'll need to call this from crontab for that to work::

    /path/manage.py cleanfilecache


Django templates integration
----------------------------

Cacheops provides tags to cache template fragments. They mimic ``@cached_as``
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

To invalidate cached fragment use:

.. code:: python

    from cacheops import invalidate_fragment

    invalidate_fragment(fragment_name, extra1, ...)

If you have more complex fragment caching needs, cacheops provides a helper to
make your own template tags which decorate a template fragment in a way
analogous to decorating a function with ``@cached`` or ``@cached_as``.
This is **experimental** feature for now.

To use it create ``myapp/templatetags/mycachetags.py`` and add something like this there:

.. code:: python

    from cacheops import cached_as, CacheopsLibrary

    register = CacheopsLibrary()

    @register.decorator_tag(takes_context=True)
    def cache_menu(context, menu_name):
        from django.utils import translation
        from myapp.models import Flag, MenuItem

        request = context.get('request')
        if request and request.user.is_staff():
            # Use noop decorator to bypass caching for staff
            return lambda func: func

        return cached_as(
            # Invalidate cache if any menu item or a flag for menu changes
            MenuItem,
            Flag.objects.filter(name='menu'),
            # Vary for menu name and language, also stamp it as "menu" to be safe
            extra=("menu", menu_name, translation.get_language()),
            timeout=24 * 60 * 60
        )

``@decorator_tag`` here creates a template tag behaving the same as returned decorator
upon wrapped template fragment. Resulting template tag could be used as follows:

.. code:: django

    {% load mycachetags %}

    {% cache_menu "top" %}
        ... the top menu template code ...
    {% endcache_menu %}

    ... some template code ..

    {% cache_menu "bottom" %}
        ... the bottom menu template code ...
    {% endcache_menu %}


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


Keeping stats
-------------

Cacheops provides ``cache_read`` signal for you to keep stats. Signal is emitted immediately after each cache lookup. Passed arguments are: ``sender`` - model class if queryset cache is fetched,
``func`` - decorated function and ``hit`` - fetch success as boolean value.

Here is simple stats implementation:

.. code:: python

    from cacheops.signals import cache_read
    from statsd.defaults.django import statsd

    def stats_collector(sender, func, hit, **kwargs):
        event = 'hit' if hit else 'miss'
        statsd.incr('cacheops.%s' % event)

    cache_read.connect(stats_collector)


CAVEATS
-------

1. Conditions other than ``__exact``, ``__in`` and ``__isnull=True`` don't make invalidation
   more granular.
2. Conditions on TextFields, FileFields and BinaryFields don't make it either.
   One should not test on their equality anyway.
3. Update of "selected_related" object does not invalidate cache for queryset.
4. Mass updates don't trigger invalidation by default.
5. Sliced queries are invalidated as non-sliced ones.
6. Doesn't work with ``.raw()`` and other sql queries.
7. Conditions on subqueries don't affect invalidation.
8. Doesn't work right with multi-table inheritance.
9. Aggregates are not implemented yet.

Here 1, 2, 3, 5 are part of the design compromise, trying to solve them will make
things complicated and slow. 7 can be implemented if needed, but it's
probably counter-productive since one can just break queries into simpler ones,
which cache better. 4 is a deliberate choice, making it "right" will flush
cache too much when update conditions are orthogonal to most queries conditions,
see, however, `.invalidated_update()`. 8 and 9 are postponed until they will gain
more interest or a champion willing to implement any one of them emerge.

All unsupported things could still be used easyly enough with the help of `@cached_as()`.


Performance tips
----------------

Here come some performance tips to make cacheops and Django ORM faster.

1. When you use cache you pickle and unpickle lots of django model instances, which could be slow. You can optimize django models serialization with `django-pickling <http://github.com/Suor/django-pickling>`_.

2. Constructing querysets is rather slow in django, mainly because most of ``QuerySet`` methods clone self, then change it and return the clone. Original queryset is usually thrown away. Cacheops adds ``.inplace()`` method, which makes queryset mutating, preventing useless cloning::

    items = Item.objects.inplace().filter(category=12).order_by('-date')[:20]

   You can revert queryset to cloning state using ``.cloning()`` call.

   Note that this is a micro-optimization technique. Using it is only desirable in the hottest places, not everywhere.

3. Use template fragment caching when possible, it's way more fast because you don't need to generate anything. Also pickling/unpickling a string is much faster than a list of model instances.

4. Run separate redis instance for cache with disabled `persistence <http://redis.io/topics/persistence>`_. You can manually call `SAVE <http://redis.io/commands/save>`_ or `BGSAVE <http://redis.io/commands/bgsave>`_ to stay hot upon server restart.

5. If you filter queryset on many different or complex conditions cache could degrade performance (comparing to uncached db calls) in consequence of frequent cache misses. Disable cache in such cases entirely or on some heuristics which detect if this request would be probably hit. E.g. enable cache if only some primary fields are used in filter.

   Caching querysets with large amount of filters also slows down all subsequent invalidation on that model. You can disable caching if more than some amount of fields is used in filter simultaneously.


Writing a test
--------------

Writing a test for an issue you are experiencing can speed up its resolution a lot.
Here is how you do that. I suppose you have some application code causing it.

1. Make a fork.
2. Install all from ``test_requirements.txt``.
3. Ensure you can run tests with ``./run_tests.py``.
4. Copy relevant models code to ``tests/models.py``.
5. Go to ``tests/tests.py`` and paste code causing exception to ``IssueTests.test_{issue_number}``.
6. Execute ``./run_tests.py IssueTests.test_{issue_number}`` and see it failing.
7. Cut down model and test code until error disappears and make a step back.
8. Commit changes and make a pull request.


TODO
----

- faster .get() handling for simple cases such as get by pk/id, with simple key calculation
- integrate previous one with prefetch_related()
- shard cache between multiple redises
- respect subqueries?
- respect headers in @cached_view*?
- group invalidate_obj() calls?
- a postpone invalidation context manager/decorator?
- fast mode: store cache in local memory, but check in with redis if it's valid
- an interface for complex fields to extract exact on parts or transforms: ArrayField.len => field__len=?, ArrayField[0] => field__0=?, JSONField['some_key'] => field__some_key=?
- custom cache eviction strategy in lua
- cache a string directly (no pickle) for direct serving (custom key function?)


.. |Build Status| image:: https://travis-ci.org/Suor/django-cacheops.svg?branch=master
   :target: https://travis-ci.org/Suor/django-cacheops


.. |Gitter| image:: https://badges.gitter.im/JoinChat.svg
   :alt: Join the chat at https://gitter.im/Suor/django-cacheops
   :target: https://gitter.im/Suor/django-cacheops?utm_source=badge&utm_medium=badge&utm_campaign=pr-badge&utm_content=badge
