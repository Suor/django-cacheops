from inspect import getfullargspec, unwrap
from functools import partial

from django.template import Library
from django.template.library import TagHelperNode, parse_bits

import cacheops
from cacheops.utils import carefully_strip_whitespace


__all__ = ['CacheopsLibrary', 'invalidate_fragment']


class CacheopsLibrary(Library):
    def decorator_tag(self, func=None, takes_context=False):
        if func is None:
            return partial(self.decorator_tag, takes_context=takes_context)

        name = func.__name__
        params, varargs, varkw, defaults, kwonly, kwonly_defaults, _ = getfullargspec(unwrap(func))

        def _compile(parser, token):
            # content
            nodelist = parser.parse(('end' + name,))
            parser.delete_first_token()

            # args
            bits = token.split_contents()[1:]
            args, kwargs = parse_bits(
                parser, bits, params, varargs, varkw, defaults,
                kwonly, kwonly_defaults, takes_context, name,
            )
            return CachedNode(func, takes_context, args, kwargs, nodelist)

        self.tag(name=name, compile_function=_compile)
        return func

register = CacheopsLibrary()


class CachedNode(TagHelperNode):
    def __init__(self, func, takes_context, args, kwargs, nodelist):
        super(CachedNode, self).__init__(func, takes_context, args, kwargs)
        self.nodelist = nodelist

    def render(self, context):
        args, kwargs = self.get_resolved_arguments(context)
        decorator = self.func(*args, **kwargs)
        render = _make_render(context, self.nodelist)
        return decorator(render)()


def _make_render(context, nodelist):
    def render():
        # TODO: make this cache preparation configurable
        return carefully_strip_whitespace(nodelist.render(context))
    return render


@register.decorator_tag
def cached(timeout, fragment_name, *extra):
    return cacheops.cached(timeout=timeout, extra=(fragment_name,) + extra)


def invalidate_fragment(fragment_name, *extra):
    render = _make_render(None, None)
    cached(None, fragment_name, *extra)(render).invalidate()


@register.decorator_tag
def cached_as(queryset, timeout, fragment_name, *extra):
    return cacheops.cached_as(queryset, timeout=timeout, extra=(fragment_name,) + extra)
