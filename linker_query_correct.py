#!/usr/bin/env python3
"""
GET endpoints linker - ВИКЛЮЧНО Query API як в PDF
"""

import json
import sys
from pathlib import Path
from tree_sitter import Language, Parser, Query, QueryCursor
import tree_sitter_php

PHP_LANGUAGE = Language(tree_sitter_php.language_php())
parser = Parser(PHP_LANGUAGE)


def parse_php(file_path):
    with open(file_path, 'rb') as f:
        code = f.read()
    return parser.parse(code), code


def find_routes(laravel_path):
    """Знайти GET routes через Query API"""
    routes = []
    routes_path = Path(laravel_path) / 'routes'

    if not routes_path.exists():
        return routes

    # Query для Route::get()
    route_query = Query(PHP_LANGUAGE, """
        (scoped_call_expression
          scope: (name) @scope
          name: (name) @method
          arguments: (arguments) @args
        )
    """)

    for route_file in routes_path.glob('*.php'):
        tree, code = parse_php(route_file)
        cursor = QueryCursor(route_query)

        for pattern_index, captures in cursor.matches(tree.root_node):
            if 'scope' in captures and 'method' in captures and 'args' in captures:
                scope_node = captures['scope'][0]
                method_node = captures['method'][0]
                args_node = captures['args'][0]

                scope = scope_node.text.decode('utf-8')
                method = method_node.text.decode('utf-8')

                if scope == 'Route' and method.upper() == 'GET':
                    route_info = parse_route_args(args_node, code, route_file)
                    if route_info:
                        routes.append(route_info)

    return routes


def parse_route_args(args_node, code, file_path):
    """Парсити аргументи через Query API"""
    # Query для string аргументів
    string_query = Query(PHP_LANGUAGE, "(string) @str")
    cursor = QueryCursor(string_query)

    strings = []
    for _, captures in cursor.matches(args_node):
        if 'str' in captures:
            for node in captures['str']:
                strings.append(node.text.decode('utf-8').strip('"\''))

    if len(strings) < 1:
        return None

    path = strings[0]

    # Query для class_constant_access
    class_query = Query(PHP_LANGUAGE, "(class_constant_access_expression) @class_access")
    cursor2 = QueryCursor(class_query)

    controller = None
    for _, captures in cursor2.matches(args_node):
        if 'class_access' in captures:
            for node in captures['class_access']:
                text = node.text.decode('utf-8')
                if '::class' in text:
                    controller = text.replace('::class', '')
                    break

    action = strings[1] if len(strings) > 1 else None

    if path and controller and action:
        return {'path': path, 'controller': controller, 'action': action}
    return None


def find_controller(controller_name, laravel_path):
    """Знайти контролер через Query API"""
    app_path = Path(laravel_path) / 'app' / 'Http' / 'Controllers'

    class_query = Query(PHP_LANGUAGE, """
        (class_declaration
          name: (name) @class_name
        ) @class
    """)

    for php_file in app_path.rglob('*.php'):
        tree, code = parse_php(php_file)
        cursor = QueryCursor(class_query)

        for _, captures in cursor.matches(tree.root_node):
            if 'class_name' in captures and 'class' in captures:
                class_name_node = captures['class_name'][0]
                class_node = captures['class'][0]

                class_name = class_name_node.text.decode('utf-8')
                # Точна відповідність
                if class_name == controller_name:
                    return php_file, class_node, code

    return None, None, None


def find_method(class_node, method_name, code):
    """Знайти метод через Query API"""
    method_query = Query(PHP_LANGUAGE, """
        (method_declaration
          name: (name) @method_name
        ) @method
    """)

    cursor = QueryCursor(method_query)

    for _, captures in cursor.matches(class_node):
        if 'method_name' in captures and 'method' in captures:
            name_node = captures['method_name'][0]
            method_node = captures['method'][0]

            name = name_node.text.decode('utf-8')
            if name == method_name:
                return method_node

    return None


def find_response_calls(method_node, code):
    """Знайти $this->...Response() через Query API"""
    call_query = Query(PHP_LANGUAGE, """
        (member_call_expression
          object: (variable_name) @object
          name: (name) @method_name
        )
    """)

    cursor = QueryCursor(call_query)
    calls = []

    for _, captures in cursor.matches(method_node):
        if 'object' in captures and 'method_name' in captures:
            obj_node = captures['object'][0]
            method_name_node = captures['method_name'][0]

            obj = obj_node.text.decode('utf-8')
            method = method_name_node.text.decode('utf-8')

            if obj == '$this' and 'response' in method.lower():
                calls.append(method)

    return calls


def extract_response(method_node, class_node, code):
    """Витягти response schema"""
    response_calls = find_response_calls(method_node, code)

    if response_calls:
        for response_method_name in response_calls:
            response_method = find_method(class_node, response_method_name, code)
            if response_method:
                schema = parse_return_array(response_method, code)
                if schema:
                    return schema

    return parse_return_array(method_node, code)


def parse_return_array(method_node, code):
    """Парсити return array через Query API"""
    # Спочатку знайти return statements
    return_query = Query(PHP_LANGUAGE, "(return_statement) @return")
    cursor = QueryCursor(return_query)

    for _, captures in cursor.matches(method_node):
        if 'return' in captures:
            return_node = captures['return'][0]

            # Шукати array всередині цього return
            array_query = Query(PHP_LANGUAGE, "(array_creation_expression) @array")
            cursor2 = QueryCursor(array_query)

            for _, array_captures in cursor2.matches(return_node):
                if 'array' in array_captures:
                    array_node = array_captures['array'][0]
                    schema = parse_array(array_node, code)
                    if schema:
                        return schema

    return {}


def parse_array(array_node, code):
    """Парсити масив через Query API"""
    # Query для array elements
    elem_query = Query(PHP_LANGUAGE, """
        (array_element_initializer) @elem
    """)

    cursor = QueryCursor(elem_query)
    properties = {}
    required = []

    for _, captures in cursor.matches(array_node):
        if 'elem' in captures:
            for elem_node in captures['elem']:
                # Шукаємо string ключ
                string_query = Query(PHP_LANGUAGE, "(string) @str")
                cursor2 = QueryCursor(string_query)

                strings = []
                for _, str_captures in cursor2.matches(elem_node):
                    if 'str' in str_captures:
                        for s in str_captures['str']:
                            strings.append(s.text.decode('utf-8').strip('"\''))

                if strings:
                    key = strings[0]
                    value_text = elem_node.text.decode('utf-8')

                    properties[key] = infer_type(key, value_text)

                    if 'null' not in value_text.lower() and 'deleted_at' not in key:
                        required.append(key)

    if not properties:
        return {}

    schema = {'type': 'object', 'properties': properties}
    if required:
        schema['required'] = required
    return schema


def infer_type(field_name, value_text):
    if any(x in field_name for x in ['_id', 'id', 'count', 'number', 'amount', 'quantity']):
        return {'type': 'integer'}
    if any(x in field_name for x in ['is_', 'has_', 'can_', 'should_']):
        return {'type': 'boolean'}
    if any(x in field_name for x in ['_at', 'date', 'time']):
        return {'type': 'string', 'format': 'date-time'}
    if '[' in value_text or 'array' in value_text.lower():
        return {'type': 'array', 'items': {'type': 'object'}}
    return {'type': 'string'}


def link_routes(routes, laravel_path):
    print(f"[*] Linking {len(routes)} routes...")
    linked = []

    for i, route in enumerate(routes):
        if (i+1) % 20 == 0:
            print(f"    {i+1}/{len(routes)}...")

        controller_file, class_node, code = find_controller(route['controller'], laravel_path)

        if not controller_file:
            linked.append({**route, 'response': {}})
            continue

        method_node = find_method(class_node, route['action'], code)

        if not method_node:
            linked.append({**route, 'response': {}})
            continue

        response = extract_response(method_node, class_node, code)
        linked.append({**route, 'response': response})

    return linked


def generate_openapi(routes):
    paths = {}
    for route in routes:
        path = route['path']
        if path not in paths:
            paths[path] = {}
        paths[path]['get'] = {
            'summary': f"GET {path}",
            'description': f"{route['controller']}.{route['action']}()",
            'responses': {
                '200': {
                    'description': 'Success',
                    'content': {
                        'application/json': {
                            'schema': route.get('response', {}) or {'type': 'object'}
                        }
                    }
                }
            }
        }
    return {
        'openapi': '3.0.3',
        'info': {'title': 'Laravel API - GET Endpoints (Query API)', 'version': '1.0.0'},
        'paths': paths
    }


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python linker_query_correct.py <laravel_path>")
        sys.exit(1)

    laravel_path = sys.argv[1]

    print("[*] Finding GET routes...")
    routes = find_routes(laravel_path)
    print(f"[*] Found {len(routes)} routes")

    linked = link_routes(routes, laravel_path)
    print(f"[*] Linked {len(linked)} routes")

    with_response = sum(1 for r in linked if r.get('response') and r['response'].get('properties'))
    print(f"\n[*] Response coverage: {with_response}/{len(linked)} ({with_response*100//len(linked) if linked else 0}%)")

    openapi = generate_openapi(linked)

    output = 'openapi_query.json'
    with open(output, 'w', encoding='utf-8') as f:
        json.dump(openapi, f, indent=2, ensure_ascii=False)

    print(f"\n[+] Done! {output}")
    print(f"    GET endpoints: {len(linked)}")
    print(f"    With schemas: {with_response}")
