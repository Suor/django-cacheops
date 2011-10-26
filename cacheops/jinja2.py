# -*- coding: utf-8 -*-
from __future__ import absolute_import
import re

from django.utils.safestring import mark_safe
from jinja2 import nodes
from jinja2.ext import Extension

import cacheops


class CacheopsExtension(Extension):
    tags = ['cached_as', 'cached']

    def parse(self, parser):
        lineno = parser.stream.current.lineno
        tag_name = parser.stream.current.value
        tag_location = '%s:%s' % (parser.name, lineno)

        parser.stream.next()
        args, kwargs = self.parse_args(parser)
        args = [nodes.Const(tag_name), nodes.Const(tag_location)] + args

        block_call = self.call_method('handle_tag', args, kwargs)
        body = parser.parse_statements(['name:end%s' % tag_name], drop_needle=True)

        return nodes.CallBlock(block_call, [], [], body).set_lineno(lineno)


    def handle_tag(self, tag_name, tag_location, *args, **kwargs):
        caller = kwargs.pop('caller')

        cacheops_decorator = getattr(cacheops, tag_name)
        kwargs['extra'] = str(kwargs.get('extra', '')) + tag_location

        @cacheops_decorator(*args, **kwargs)
        def _handle_tag():
            content = caller()
            return _carefully_strip_whitespace(content) # TODO: make this cache preparation configurable

        return _handle_tag()


    def parse_args(self, parser):
        args = []
        kwargs = []
        require_comma = False

        while parser.stream.current.type != 'block_end':
            if require_comma:
                parser.stream.expect('comma')

            if parser.stream.current.type == 'name' and parser.stream.look().type == 'assign':
                key = parser.stream.current.value
                parser.stream.skip(2)
                value = parser.parse_expression()
                kwargs.append(nodes.Keyword(key, value, lineno=value.lineno))
            else:
                if kwargs:
                    parser.fail('Invalid argument syntax for CacheopsExtension tag', parser.stream.current.lineno)
                args.append(parser.parse_expression())

            require_comma = True

        return args, kwargs

cache = CacheopsExtension


NEWLINE_BETWEEN_TAGS = mark_safe('>\n<')
SPACE_BETWEEN_TAGS = mark_safe('> <')

def _carefully_strip_whitespace(text):
    text = re.sub(r'>\s*\n\s*<', NEWLINE_BETWEEN_TAGS, text)
    text = re.sub(r'>\s{2,}<', SPACE_BETWEEN_TAGS, text)
    return text
