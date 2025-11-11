#!/usr/bin/env python3
"""
Linker для GET endpoints - витягує тільки response schemas
"""

import json
import sys


def get_text(node):
    """Витягти текст з ноди"""
    if 'text' in node:
        return node['text']
    if 'text_preview' in node:
        return node['text_preview']
    return ''


def find_routes(files):
    """Знайти всі GET routes"""
    routes = []

    for file_data in files:
        file_path = file_data.get('file', '')
        if 'routes' not in file_path.lower():
            continue

        # Рекурсивно шукаємо Route::get() в AST
        def search_routes(node):
            if node.get('type') == 'scoped_call_expression':
                children = node.get('children', [])

                scope = None
                method = None
                args = None

                for child in children:
                    if child.get('field') == 'scope':
                        scope = get_text(child)
                    elif child.get('field') == 'name':
                        method = get_text(child)
                    elif child.get('type') == 'arguments':
                        args = child

                if scope == 'Route' and method and method.upper() == 'GET':
                    # Парсити GET route
                    route_info = parse_get_route(args, node.get('start_line'), file_path)
                    if route_info:
                        routes.append(route_info)

            for child in node.get('children', []):
                search_routes(child)

        search_routes(file_data['ast'])

    return routes


def parse_get_route(args_node, line, file_path):
    """Парсити Route::get('/path', [Controller::class, 'method'])"""
    if not args_node:
        return None

    arguments = [c for c in args_node.get('children', []) if c.get('type') == 'argument']

    if len(arguments) < 2:
        return None

    # Path
    path = None
    for child in arguments[0].get('children', []):
        if child.get('type') == 'string':
            path = get_text(child).strip('"\'')
            break

    # Controller + action
    controller = None
    action = None

    for child in arguments[1].get('children', []):
        if child.get('type') == 'array_creation_expression':
            for elem in child.get('children', []):
                if elem.get('type') == 'array_element_initializer':
                    for e in elem.get('children', []):
                        if e.get('type') == 'class_constant_access_expression':
                            for cc in e.get('children', []):
                                if cc.get('is_named'):
                                    controller = get_text(cc)
                                    break
                        elif e.get('type') == 'string':
                            action = get_text(e).strip('"\'')

    if not path or not controller or not action:
        return None

    return {
        'path': path,
        'controller': controller,
        'action': action,
        'line': line,
        'file': file_path
    }


def build_class_index(files):
    """Індекс класів по FQN"""
    print("[*] Building class index...")
    index = {}

    for i, file_data in enumerate(files):
        if (i+1) % 500 == 0:
            print(f"    {i+1}/{len(files)}...")

        # Знайти namespace
        namespace = find_namespace(file_data['ast'])

        # Знайти класи
        classes = find_classes(file_data['ast'])

        for cls in classes:
            class_name = cls['name']
            fqn = f"{namespace}\\{class_name}" if namespace else class_name

            index[fqn] = {
                'node': cls['node'],
                'file': file_data['file']
            }

    return index


def find_namespace(ast_node):
    """Знайти namespace в AST"""
    def search(node):
        if node.get('type') == 'namespace_definition':
            for child in node.get('children', []):
                if child.get('field') == 'name':
                    return get_text(child)
        for child in node.get('children', []):
            result = search(child)
            if result:
                return result
        return None

    return search(ast_node) or ''


def find_classes(ast_node):
    """Знайти всі class_declaration в AST"""
    classes = []

    def search(node):
        if node.get('type') == 'class_declaration':
            # Знайти ім'я класу
            for child in node.get('children', []):
                if child.get('field') == 'name':
                    classes.append({
                        'name': get_text(child),
                        'node': node
                    })
                    break

        for child in node.get('children', []):
            search(child)

    search(ast_node)
    return classes


def find_method_in_class(class_node, method_name):
    """Знайти метод в класі"""
    def search(node):
        if node.get('type') == 'method_declaration':
            for child in node.get('children', []):
                if child.get('field') == 'name' and get_text(child) == method_name:
                    return node
        for child in node.get('children', []):
            result = search(child)
            if result:
                return result
        return None

    return search(class_node)


def extract_response_schema(method_node, class_index):
    """Витягти response schema з return statements"""
    # Знайти return statements
    returns = []

    def find_returns(node):
        if node.get('type') == 'return_statement':
            returns.append(node)
        for child in node.get('children', []):
            find_returns(child)

    find_returns(method_node)

    # Шукати Resource/Transformer в return
    for ret_node in returns:
        ret_text = get_text(ret_node)

        # Знайти Resource клас
        for fqn, class_info in class_index.items():
            class_name = fqn.split('\\')[-1]

            if ('Resource' in class_name or 'Transformer' in class_name) and class_name in ret_text:
                # Знайти toArray() або transform()
                to_array = find_method_in_class(class_info['node'], 'toArray')
                if not to_array:
                    to_array = find_method_in_class(class_info['node'], 'transform')

                if to_array:
                    schema = parse_to_array_method(to_array)
                    if schema and schema.get('properties'):
                        return schema

    return {}


def parse_to_array_method(method_node):
    """Парсити toArray() метод → OpenAPI schema"""
    # Знайти return array
    def find_return_array(node):
        if node.get('type') == 'return_statement':
            for child in node.get('children', []):
                if child.get('type') == 'array_creation_expression':
                    return child
                r = find_return_array(child)
                if r: return r
        for child in node.get('children', []):
            r = find_return_array(child)
            if r: return r
        return None

    array_node = find_return_array(method_node)
    if not array_node:
        return {}

    # Парсити array elements
    properties = {}
    required = []

    for child in array_node.get('children', []):
        if child.get('type') == 'array_element_initializer':
            text = get_text(child)

            if '=>' not in text:
                continue

            parts = text.split('=>', 1)
            field_name = parts[0].strip().strip('"\'')
            field_value = parts[1].strip()

            # Визначити тип по шаблонах
            field_schema = infer_type_from_value(field_name, field_value)

            properties[field_name] = field_schema

            # Required якщо не nullable
            if 'deleted_at' not in field_name and 'null' not in field_value.lower():
                required.append(field_name)

    if not properties:
        return {}

    schema = {
        'type': 'object',
        'properties': properties
    }

    if required:
        schema['required'] = required

    return schema


def infer_type_from_value(field_name, field_value):
    """Визначити тип поля по назві і значенню"""
    # ID, counters
    if any(x in field_name for x in ['_id', 'id', 'count', 'number', 'amount', 'quantity']):
        return {'type': 'integer'}

    # Booleans
    if any(x in field_name for x in ['is_', 'has_', 'can_', 'should_']):
        return {'type': 'boolean'}

    # Dates
    if any(x in field_name for x in ['_at', 'date', 'time']):
        return {'type': 'string', 'format': 'date-time'}

    # Arrays
    if '[' in field_value or 'array' in field_value.lower():
        return {'type': 'array', 'items': {'type': 'object'}}

    # Default
    return {'type': 'string'}


def link_get_routes(routes, class_index):
    """Лінкувати GET routes до response schemas"""
    print(f"[*] Linking {len(routes)} GET routes...")

    linked = []

    for i, route in enumerate(routes):
        if (i+1) % 50 == 0:
            print(f"    {i+1}/{len(routes)}...")

        controller = route['controller']
        action = route['action']

        # Знайти controller class
        controller_class = None
        for fqn, class_info in class_index.items():
            if controller in fqn:
                controller_class = class_info
                break

        if not controller_class:
            continue

        # Знайти метод
        method_node = find_method_in_class(controller_class['node'], action)
        if not method_node:
            continue

        # Витягти response
        response = extract_response_schema(method_node, class_index)

        linked.append({
            'path': route['path'],
            'controller': controller,
            'action': action,
            'response': response,
            'line': route['line']
        })

    return linked


def generate_openapi(routes):
    """Генерувати OpenAPI для GET endpoints"""
    paths = {}

    for route in routes:
        path = route['path']

        if path not in paths:
            paths[path] = {}

        response = route.get('response', {})

        paths[path]['get'] = {
            'summary': f"GET {path}",
            'description': f"{route['controller']}.{route['action']}()",
            'responses': {
                '200': {
                    'description': 'Success',
                    'content': {
                        'application/json': {
                            'schema': response if response else {'type': 'object'}
                        }
                    }
                }
            }
        }

    return {
        'openapi': '3.0.3',
        'info': {
            'title': 'Laravel API - GET Endpoints',
            'version': '1.0.0'
        },
        'paths': paths
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python linker_get.py <ast_full.json>")
        sys.exit(1)

    print("[*] Loading AST...")
    with open(sys.argv[1], 'r', encoding='utf-8') as f:
        data = json.load(f)

    files = data['files']
    print(f"[*] Loaded {len(files)} files")

    # Build class index
    class_index = build_class_index(files)
    print(f"[*] Indexed {len(class_index)} classes")

    # Find GET routes
    print("[*] Extracting GET routes...")
    routes = find_routes(files)
    print(f"[*] Found {len(routes)} GET routes")

    # Link to schemas
    linked = link_get_routes(routes, class_index)
    print(f"[*] Linked {len(linked)} routes")

    # Stats
    with_response = sum(1 for r in linked if r.get('response') and r['response'].get('properties'))
    print(f"\n[*] Response coverage: {with_response}/{len(linked)} ({with_response*100//len(linked) if linked else 0}%)")

    # Generate OpenAPI
    print("\n[*] Generating OpenAPI...")
    openapi = generate_openapi(linked)

    output = 'openapi_get.json'
    with open(output, 'w', encoding='utf-8') as f:
        json.dump(openapi, f, indent=2, ensure_ascii=False)

    print(f"\n[+] Done! Output: {output}")
    print(f"    Total GET endpoints: {len(linked)}")
    print(f"    With response schemas: {with_response}")


if __name__ == '__main__':
    main()
