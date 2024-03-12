Cacheops |Build Status|
========

A slick app that supports automatic or manual queryset caching and `automatic
granular event-driven invalidation <http://suor.github.io/blog/2014/03/09/on-orm-cache-invalidation/>`_.

It uses `redis <http://redis.io/>`_ as backend for ORM cache and redis or
filesystem for simple time-invalidated one.

And there is more to it:

- decorators to cache any user function or view as a queryset or by time
- extensions for django and jinja2 templates
- transparent transaction support
- dog-pile prevention mechanism
- a couple of hacks to make django faster

.. contents:: Contents
    :local:
    :backlinks: top

Requirements
++++++++++++

Python 3.7+, Django 3.2+ and Redis 4.0+.


Installation
++++++++++++

Using pip:

.. code:: bash

    $ pip install django-cacheops

    # Or from github directly
    $ pip install git+https://github.com/Suor/django-cacheops.git@master


Setup
+++++

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

    # If you want to use sentinel, specify this variable
    CACHEOPS_SENTINEL = {
        'locations': [('localhost', 26379)], # sentinel locations, required
        'service_name': 'mymaster',          # sentinel service name, required
        'socket_timeout': 0.1,               # connection timeout in seconds, optional
        'db': 0                              # redis database, default: 0
        ...                                  # everything else is passed to Sentinel()
    }

    # Use your own redis client class, should be compatible or subclass redis.Redis
    CACHEOPS_CLIENT_CLASS = 'your.redis.ClientClass'

    CACHEOPS = {
        # Automatically cache any User.objects.get() calls for 15 minutes
        # This also includes .first() and .last() calls,
        # as well as request.user or post.author access,
        # where Post.author is a foreign key to auth.User
        'auth.user': {'ops': 'get', 'timeout': 60*15},

        # Automatically cache all gets and queryset fetches
        # to other django.contrib.auth models for an hour
        'auth.*': {'ops': {'fetch', 'get'}, 'timeout': 60*60},

        # Cache all queries to Permission
        # 'all' is an alias for {'get', 'fetch', 'count', 'aggregate', 'exists'}
        'auth.permission': {'ops': 'all', 'timeout': 60*60},

        # Enable manual caching on all other models with default timeout of an hour
        # Use Post.objects.cache().get(...)
        #  or Tags.objects.filter(...).order_by(...).cache()
        # to cache particular ORM request.
        # Invalidation is still automatic
        '*.*': {'ops': (), 'timeout': 60*60},

        # And since ops is empty by default you can rewrite last line as:
        '*.*': {'timeout': 60*60},

        # NOTE: binding signals has its overhead, like preventing fast mass deletes,
        #       you might want to only register whatever you cache and dependencies.

        # Finally you can explicitely forbid even manual caching with:
        'some_app.*': None,
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

Using ``'*.*'`` with non-empty ``ops`` is **not recommended**
since it will easily cache something you don't intent to or even know about like migrations tables.
The better approach will be restricting by app with ``'app_name.*'``.

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
+++++

| **Automatic caching**

It's automatic you just need to set it up.


| **Manual caching**

You can force any queryset to use cache by calling its ``.cache()`` method:

.. code:: python

    Article.objects.filter(tag=2).cache()


Here you can specify which ops should be cached for the queryset, for example, this code:

.. code:: python

    qs = Article.objects.filter(tag=2).cache(ops=['count'])
    paginator = Paginator(objects, ipp)
    articles = list(pager.page(page_num)) # hits database


will cache count call in ``Paginator`` but not later articles fetch.
There are five possible actions - ``get``, ``fetch``, ``count``, ``aggregate`` and ``exists``.
You can pass any subset of this ops to ``.cache()`` method even empty - to turn off caching.
There is, however, a shortcut for the latter:

.. code:: python

    qs = Article.objects.filter(visible=True).nocache()
    qs1 = qs.filter(tag=2)       # hits database
    qs2 = qs.filter(category=3)  # hits it once more


It is useful when you want to disable automatic caching on particular queryset.

You can also override default timeout for particular queryset with ``.cache(timeout=...)``.


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
        return render(...)

You can pass ``timeout``, ``extra`` and several samples the same way as to ``@cached_as()``. Note that you can pass a function as ``extra``:

.. code:: python

    @cached_view_as(News, extra=lambda req: req.user.is_staff)
    def news_index(request):
        # ... add extra things for staff
        return render(...)

A function passed as ``extra`` receives the same arguments as the cached function.

Class based views can also be cached:

.. code:: python

    class NewsIndex(ListView):
        model = News

    news_index = cached_view_as(News, ...)(NewsIndex.as_view())


Invalidation
++++++++++++

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

Components
++++++++++


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
    /path/manage.py cleanfilecache /path/to/non-default/cache/dir


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

You can use ``None`` for timeout in ``@cached_as`` to use it's default value for model.

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


Special topics
++++++++++++++

Transactions
------------

Cacheops transparently supports transactions. This is implemented by following simple rules:

1. Once transaction is dirty (has changes) caching turns off. The reason is that the state of database at this point is only visible to current transaction and should not affect other users and vice versa.

2. Any invalidating calls are scheduled to run on the outer commit of transaction.

3. Savepoints and rollbacks are also handled appropriately.

Mind that simple and file cache don't turn itself off in transactions but work as usual.


Dog-pile effect prevention
--------------------------

There is optional locking mechanism to prevent several threads or processes simultaneously performing same heavy task. It works with ``@cached_as()`` and querysets:

.. code:: python

    @cached_as(qs, lock=True)
    def heavy_func(...):
        # ...

    for item in qs.cache(lock=True):
        # ...

It is also possible to specify ``lock: True`` in ``CACHEOPS`` setting but that would probably be a waste. Locking has no overhead on cache hit though.


Multiple database support
-------------------------

By default cacheops considers query result is same for same query, not depending
on database queried. That could be changed with ``db_agnostic`` cache profile option:

.. code:: python

    CACHEOPS = {
        'some.model': {'ops': 'get', 'db_agnostic': False, 'timeout': ...}
    }


Sharing redis instance
----------------------

Cacheops provides a way to share a redis instance by adding prefix to cache keys:

.. code:: python

    CACHEOPS_PREFIX = lambda query: ...
    # or
    CACHEOPS_PREFIX = 'some.module.cacheops_prefix'

A most common usage would probably be a prefix by host name:

.. code:: python

    # get_request() returns current request saved to threadlocal by some middleware
    cacheops_prefix = lambda _: get_request().get_host()

A ``query`` object passed to callback also enables reflection on used databases and tables:

.. code:: python

    def cacheops_prefix(query):
        query.dbs    # A list of databases queried
        query.tables # A list of tables query is invalidated on

        if set(query.tables) <= HELPER_TABLES:
            return 'helper:'
        if query.tables == ['blog_post']:
            return 'blog:'


Custom serialization
--------------------

Cacheops uses ``pickle`` by default, employing it's default protocol. But you can specify your own
it might be any module or a class having ``.dumps()`` and ``.loads()`` functions. For example you can use ``dill`` instead, which can serialize more things like anonymous functions:

.. code:: python

    CACHEOPS_SERIALIZER = 'dill'

One less obvious use is to fix pickle protocol, to use cacheops cache across python versions:

.. code:: python

    import pickle

    class CACHEOPS_SERIALIZER:
        dumps = lambda data: pickle.dumps(data, 3)
        loads = pickle.loads


Using memory limit
------------------

Cacheops offers an "insideout" mode, which idea is instead of conj sets contatining cache keys, cache values contain a checksum of random stamps stored in conj keys, which are checked on each read to stay the same. To use that add to settings:

.. code:: python

    CACHEOPS_INSIDEOUT = True  # Might become default in future

And set up ``maxmemory`` and ``maxmemory-policy`` in redis config::

    maxmemory 4gb
    maxmemory-policy volatile-lru  # or other volatile-*

Note that using any of ``allkeys-*`` policies might drop important invalidation structures of cacheops and lead to stale cache.


Memory usage cleanup
--------------------

**This does not apply to "insideout" mode. This issue doesn't happen there.**

In some cases, cacheops may leave some conjunction keys of expired cache keys in redis without being able to invalidate them. Those will still expire with age, but in the meantime may cause issues like slow invalidation (even "BUSY Redis ...") and extra memory usage. To prevent that it is advised to not cache complex queries, see `Perfomance tips <#performance-tips>`_, 5.

Cacheops ships with a ``cacheops.reap_conjs`` function that can clean up these keys,
ignoring conjunction sets with some reasonable size. It can be called using the ``reapconjs`` management command::

    ./manage.py reapconjs --chunk-size=100 --min-conj-set-size=10000  # with custom values
    ./manage.py reapconjs                                             # with default values (chunks=1000, min size=1000)

The command is a small wrapper that calls a function with the main logic. You can also call it from your code, for example from a Celery task:

.. code:: python

    from cacheops import reap_conjs

    @app.task
    def reap_conjs_task():
        reap_conjs(
            chunk_size=2000,
            min_conj_set_size=100,
        )


Keeping stats
-------------

Cacheops provides ``cache_read`` and ``cache_invalidated`` signals for you to keep track.

Cache read signal is emitted immediately after each cache lookup. Passed arguments are: ``sender`` - model class if queryset cache is fetched,
``func`` - decorated function and ``hit`` - fetch success as boolean value.

Here is a simple stats implementation:

.. code:: python

    from cacheops.signals import cache_read
    from statsd.defaults.django import statsd

    def stats_collector(sender, func, hit, **kwargs):
        event = 'hit' if hit else 'miss'
        statsd.incr('cacheops.%s' % event)

    cache_read.connect(stats_collector)

Cache invalidation signal is emitted after object, model or global invalidation passing ``sender`` and ``obj_dict`` args. Note that during normal operation cacheops only uses object invalidation, calling it once for each model create/delete and twice for update: passing old and new object dictionary.


Troubleshooting
+++++++++++++++

CAVEATS
-------

1. Conditions other than ``__exact``, ``__in`` and ``__isnull=True`` don't make invalidation
   more granular.
2. Conditions on TextFields, FileFields and BinaryFields don't make it either.
   One should not test on their equality anyway. See `CACHEOPS_SKIP_FIELDS` though.
3. Update of "select_related" object does not invalidate cache for queryset.
   Use ``.prefetch_related()`` instead.
4. Mass updates don't trigger invalidation by default. But see ``.invalidated_update()``.
5. Sliced queries are invalidated as non-sliced ones.
6. Doesn't work with ``.raw()`` and other sql queries.
7. Conditions on subqueries don't affect invalidation.
8. Doesn't work right with multi-table inheritance.

Here 1, 2, 3, 5 are part of the design compromise, trying to solve them will make
things complicated and slow. 7 can be implemented if needed, but it's
probably counter-productive since one can just break queries into simpler ones,
which cache better. 4 is a deliberate choice, making it "right" will flush
cache too much when update conditions are orthogonal to most queries conditions,
see, however, `.invalidated_update()`. 8 is postponed until it will gain
more interest or a champion willing to implement it emerges.

All unsupported things could still be used easily enough with the help of ``@cached_as()``.


Performance tips
----------------

Here come some performance tips to make cacheops and Django ORM faster.

1. When you use cache you pickle and unpickle lots of django model instances, which could be slow. You can optimize django models serialization with `django-pickling <http://github.com/Suor/django-pickling>`_.

2. Constructing querysets is rather slow in django, mainly because most of ``QuerySet`` methods clone self, then change it and return the clone. Original queryset is usually thrown away. Cacheops adds ``.inplace()`` method, which makes queryset mutating, preventing useless cloning:

   .. code:: python

    items = Item.objects.inplace().filter(category=12).order_by('-date')[:20]

   You can revert queryset to cloning state using ``.cloning()`` call. Note that this is a micro-optimization technique. Using it is only desirable in the hottest places, not everywhere.

3. Use template fragment caching when possible, it's way more fast because you don't need to generate anything. Also pickling/unpickling a string is much faster than a list of model instances.

4. Run separate redis instance for cache with disabled `persistence <http://redis.io/topics/persistence>`_. You can manually call `SAVE <http://redis.io/commands/save>`_ or `BGSAVE <http://redis.io/commands/bgsave>`_ to stay hot upon server restart.

5. If you filter queryset on many different or complex conditions cache could degrade performance (comparing to uncached db calls) in consequence of frequent cache misses. Disable cache in such cases entirely or on some heuristics which detect if this request would be probably hit. E.g. enable cache if only some primary fields are used in filter.

   Caching querysets with large amount of filters also slows down all subsequent invalidation on that model (negligable for "insideout" mode). You can disable caching if more than some amount of fields is used in filter simultaneously.

6. Split database queries into smaller ones when you cache them. This goes against usual approach, but this allows invalidation to be more granular: smaller parts will be invalidated independently and each part will invalidate more precisely.

   .. code:: python

    Post.objects.filter(category__slug="foo")
    # A single database query, but will be invalidated not only on
    # any Category with .slug == "foo" change, but also for any Post change

    Post.objects.filter(category=Category.objects.get(slug="foo"))
    # Two queries, each invalidates only on a granular event:
    # either category.slug == "foo" or Post with .category_id == <whatever is there>


Writing a test
--------------

Writing a test for an issue you are experiencing can speed up its resolution a lot.
Here is how you do that. I suppose you have some application code causing it.

1. Make a fork.
2. Install all from ``requirements-test.txt``.
3. Ensure you can run tests with ``pytest``.
4. Copy relevant models code to ``tests/models.py``.
5. Go to ``tests/tests.py`` and paste code causing exception to ``IssueTests.test_{issue_number}``.
6. Execute ``pytest -k {issue_number}`` and see it failing.
7. Cut down model and test code until error disappears and make a step back.
8. Commit changes and make a pull request.


TODO
++++

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


.. |Build Status| image:: https://github.com/Suor/django-cacheops/actions/workflows/ci.yml/badge.svg
   :target: https://github.com/Suor/django-cacheops/actions/workflows/ci.yml?query=branch%3Amaster
