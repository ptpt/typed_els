#!/usr/bin/env python3

import collections
import itertools
import json
import logging
import re
import sys

import click

TypedProperty = collections.namedtuple(
    'TypedProperty',
    [
        'interface',
        'property',
        'type',
        'comment',
    ],
)
_GEO_POINT_TYPE = '{lat: number, lon: number}'
_GEO_SHAPE_TYPE = ' | '.join('''
{coordinates: number[], type: 'Point'}
{coordinates: number[][], type: 'MultiPoint'}
{coordinates: number[][], type: 'LineString'}
{coordinates: number[][][], type: 'MultiLineString'}
{coordinates: number[][], type: 'Polygon'}
{coordinates: number[][][][], type: 'MultiPolygon'}
{coordinates: number[][], type: 'Envelope'}
{coordinates: number[], radius: string | number, type: 'Circle'}
{geometries: any[], type: 'GeometryCollection'}
'''.strip().split('\n'))

# ELS to TS types
_TYPE_MAPPINGS = {
    'byte': 'number',
    'date': 'number',
    'float': 'number',
    'integer': 'number',
    'double': 'number',
    'long': 'number',
    'short': 'number',
    'half_float': 'number',
    'scaled_float': 'number',

    'object': '{[key: string]: any}',

    'binary': 'string',         # base64
    'ip': 'string',
    'keyword': 'string',
    'string': 'string',
    'text': 'string',

    'boolean': 'boolean',

    'geo_point': _GEO_POINT_TYPE,
    'geo_shape': _GEO_SHAPE_TYPE,

    # TODO
    # 'integer_range'
    # 'float_range'
    # 'long_range'
    # 'double_range'
    # 'date_range'
    # 'ip_range'
}

# https://github.com/Microsoft/TypeScript/issues/2536#issuecomment-87194347
_RESERVED_KEYWORDS = {
    # Reserved Words
    'break',
    'case',
    'catch',
    'class',
    'const',
    'continue',
    'debugger',
    'default',
    'delete',
    'do',
    'else',
    'enum',
    'export',
    'extends',
    'false',
    'finally',
    'for',
    'function',
    'if',
    'import',
    'in',
    'instanceof',
    'new',
    'null',
    'return',
    'super',
    'switch',
    'this',
    'throw',
    'true',
    'try',
    'typeof',
    'var',
    'void',
    'while',
    'with',

    # Strict Mode Reserved Words
    'as',
    'implements',
    'interface',
    'let',
    'package',
    'private',
    'protected',
    'public',
    'static',
    'yield',

    # Contextual Keywords
    'any',
    'boolean',
    'constructor',
    'declare',
    'from',
    'get',
    'module',
    'number',
    'of',
    'require',
    'set',
    'string',
    'symbol',
    'type',

    # Misc.
    # Interface name cannot be 'object'
    'object',
}

_INTERFACE_NAME = re.compile(r'(\w|\$)(\w|\d|\$)*', flags=re.ASCII)
_INTERFACE_NAME_1 = re.compile(r'\w|\$', flags=re.ASCII)
_INTERFACE_NAME_INVALID = re.compile(r'[^\w\$]', flags=re.ASCII)

def normalize_interface_name(name):
    if not name:
        return '_'

    if not re.match(_INTERFACE_NAME_1, name):
        name = name.replace(name[0], '_', 1)

    name = re.sub(
        _INTERFACE_NAME_INVALID,
        '_',
        name,
    )

    if name in _RESERVED_KEYWORDS:
        name = name + '_'

    return name

def test_normalize_interface_name():
    assert normalize_interface_name('') == '_'
    assert normalize_interface_name('hello') == 'hello'
    assert normalize_interface_name('_hello') == '_hello'
    assert normalize_interface_name('_hello$') == '_hello$'
    assert normalize_interface_name('$_hello$') == '$_hello$'
    assert normalize_interface_name('*_hello') == '__hello'
    assert normalize_interface_name(' hello') == '_hello'
    assert normalize_interface_name('*') == '_'
    assert normalize_interface_name('**') == '__'
    assert normalize_interface_name('-') == '_'
    assert normalize_interface_name('default') == 'default_'

def new_interface_name(interface, used_interface_names):
    idx = 1
    new_interface = interface
    while new_interface in used_interface_names:
        new_interface = '{interface}_{idx}'.format(
            interface=interface,
            idx=idx,
        )
        idx += 1
    used_interface_names.add(new_interface)
    return new_interface

def test_new_interface_name():
    assert new_interface_name('hello', set()) == 'hello'
    assert new_interface_name('hello', {'hello', 'hello_1', 'hello_2'}) == 'hello_3'

def generate_typed_properties(interface, properties, used_interface_names):
    assert re.fullmatch(_INTERFACE_NAME, interface)

    for property, definition in properties.items():
        if not property:
            continue

        if 'properties' in definition:
            inner_interface = '{interface}${property}'.format(
                interface=interface,
                property=property,
            );
            inner_interface = new_interface_name(
                normalize_interface_name(inner_interface),
                used_interface_names,
            )
            yield from generate_typed_properties(
                inner_interface,
                definition['properties'],
                used_interface_names,
            )
            # object datatype or nested datatype can be an array or a
            # single object
            yield TypedProperty(
                interface,
                property,
                '{interface} | {interface}[]'.format(interface=inner_interface),
                json.dumps(definition),
            )

        else:
            datatype = definition.get('type')
            type = _TYPE_MAPPINGS.get(datatype)
            if type:
                yield TypedProperty(
                    interface,
                    property,
                    type,
                    json.dumps(definition),
                )
            elif datatype:
                logging.warning('Unable to find the corresponding TS type for ELS datatype {0}'.format(datatype))
                yield TypedProperty(
                    interface,
                    property,
                    'any',
                    json.dumps(definition),
                )

def search_typed_properties(type, mapping, used_interface_names=None):
    if used_interface_names is None:
        used_interface_names = set()

    if isinstance(mapping, dict):
        if 'properties' in mapping:
            interface = new_interface_name(
                normalize_interface_name(type),
                used_interface_names,
            )
            yield from generate_typed_properties(interface, mapping['properties'], used_interface_names)
        else:
            for key, value in mapping.items():
                yield from search_typed_properties(key, value, used_interface_names)

def print_typed_properties(typed_properties, indent, optional):
    sorted_typed_properties = sorted(
        typed_properties,
        key=lambda tp: tp.interface)

    for interface, group in itertools.groupby(sorted_typed_properties, lambda tp: tp.interface):
        group = list(group)

        print('export interface {interface} {{'.format(interface=interface))
        for typed_property in group:
            if typed_property.comment:
                print('{indent}/**'.format(indent=indent))
                print('{indent} * {comment}'.format(indent=indent, comment=typed_property.comment))
                print('{indent} **/'.format(indent=indent))

            print('{indent}{property}{optional}: {type};'.format(
                indent=indent,
                property=json.dumps(typed_property.property),
                optional='?' if optional else '',
                type=typed_property.type,
            ))
            print()
        print('}')
        print()

def parse_intent(indent):
    char_count = int(indent[:-1])
    char = indent[-1]
    if char == 'w':
        char = ' '
    elif char == 't':
        char = '\t'
    else:
        raise ValueError
    return char_count, char

@click.command()

@click.option(
    '--indent',
    default='4w',
    help='''
Specify indentation style for properties in format of "[0-9]+[w|t]"
where the number tells to use how many whitespaces(w) or tabs(t)
'''.strip(),
    show_default=True,
)

@click.option(
    '--interface',
    default='Root',
    help='The root interface name',
    show_default=True,
)

@click.option(
    '--optional',
    default=False,
    is_flag=True,
    help='Whether to mark properties as optional',
    show_default=True,
)

def command(indent, interface, optional):
    try:
        char_count, char = parse_intent(indent)
    except (TypeError, IndexError, ValueError):
        raise click.BadParameter('Invalid indent. See --help')
    indent = char * char_count
    mapping = json.load(sys.stdin)
    typed_properties = search_typed_properties(interface, mapping)
    print_typed_properties(typed_properties, indent, optional)

if __name__ == '__main__':
    command(auto_envvar_prefix='TYPED_ELS')
