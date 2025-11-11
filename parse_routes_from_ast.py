#!/usr/bin/env python3
"""
Парсер routes з повного AST (ast_generic.json)
Працює ТІЛЬКИ з dict traversal - БЕЗ вигадок
"""

import json
import sys


def get_text_from_node(node):
    """Отримати текст з ноди"""
    if 'text' in node:
        return node['text']
    if 'text_preview' in node:
        return node['text_preview']
    return ''


def find_all_routes(root_node):
    """Знайти всі Route:: виклики рекурсивно"""
    routes = []

    def traverse(node):
        # Якщо це scoped_call_expression - перевірити чи Route::
        if node.get('type') == 'scoped_call_expression':
            children = node.get('children', [])

            # Структура: scope (Route) :: name (get/post/etc) arguments
            scope_node = None
            name_node = None
            args_node = None

            for child in children:
                if child.get('field') == 'scope':
                    scope_node = child
                elif child.get('field') == 'name':
                    name_node = child
                elif child.get('type') == 'arguments':
                    args_node = child

            # Якщо це Route::method()
            if scope_node and name_node:
                scope_text = get_text_from_node(scope_node)
                method_text = get_text_from_node(name_node)

                if scope_text == 'Route':
                    route_info = parse_route_call(method_text, args_node, node.get('start_line'))
                    if route_info:
                        routes.append(route_info)

        # Рекурсивно обійти дітей
        for child in node.get('children', []):
            traverse(child)

    traverse(root_node)
    return routes


def parse_route_call(method, args_node, line):
    """Парсити Route::method(...) виклик"""
    if not args_node:
        return None

    route = {
        'method': method.upper(),
        'line': line
    }

    # Витягти аргументи
    arguments = [c for c in args_node.get('children', []) if c.get('type') == 'argument']

    # Перший аргумент - path (string)
    if len(arguments) >= 1:
        path = extract_string_from_argument(arguments[0])
        if path:
            route['path'] = path

    # Другий аргумент - controller (array або closure)
    if len(arguments) >= 2:
        controller_info = extract_controller_from_argument(arguments[1])
        if controller_info:
            route.update(controller_info)

    return route


def extract_string_from_argument(arg_node):
    """Витягти string з argument ноди"""
    for child in arg_node.get('children', []):
        if child.get('type') == 'string':
            text = get_text_from_node(child)
            # Очистити лапки
            return text.strip('"\'')
    return None


def extract_controller_from_argument(arg_node):
    """Витягти controller з argument ноди (array [Controller::class, 'method'])"""
    for child in arg_node.get('children', []):
        if child.get('type') == 'array_creation_expression':
            return parse_controller_array(child)
    return None


def parse_controller_array(array_node):
    """Парсити [Controller::class, 'method']"""
    controller = None
    action = None

    # Знайти array_element_initializer
    elements = [c for c in array_node.get('children', []) if c.get('type') == 'array_element_initializer']

    for elem in elements:
        # Кожен елемент може бути Controller::class або 'method'
        for child in elem.get('children', []):
            if child.get('type') == 'class_constant_access_expression':
                # Controller::class
                controller = extract_class_name(child)
            elif child.get('type') == 'string':
                # 'method'
                action = get_text_from_node(child).strip('"\'')

    result = {}
    if controller:
        result['controller'] = controller
    if action:
        result['action'] = action

    return result if result else None


def extract_class_name(class_const_node):
    """Витягти ім'я класу з Controller::class"""
    # Перший child - ім'я класу
    children = [c for c in class_const_node.get('children', []) if c.get('is_named')]
    if children:
        return get_text_from_node(children[0])
    return None


def generate_openapi(routes):
    """Згенерувати OpenAPI з routes"""
    paths = {}

    for route in routes:
        path = route.get('path')
        method = route.get('method', 'GET').lower()

        if not path:
            continue

        if path not in paths:
            paths[path] = {}

        controller = route.get('controller', 'Unknown')
        action = route.get('action', 'unknown')

        paths[path][method] = {
            'summary': f"{method.upper()} {path}",
            'description': f"{controller}.{action}() - line {route.get('line')}",
            'responses': {
                '200': {
                    'description': 'Success',
                    'content': {
                        'application/json': {
                            'schema': {'type': 'object'}
                        }
                    }
                }
            }
        }

    return {
        'openapi': '3.0.3',
        'info': {
            'title': 'Laravel API from Full AST',
            'version': '1.0.0'
        },
        'paths': paths
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python parse_routes_from_ast.py <ast_generic.json>")
        sys.exit(1)

    json_file = sys.argv[1]

    print(f"[*] Loading AST from {json_file}...")
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    print(f"[*] Parsing routes...")
    routes = find_all_routes(data['ast'])

    print(f"[*] Found {len(routes)} routes")

    # Статистика
    methods_count = {}
    for route in routes:
        method = route.get('method', 'UNKNOWN')
        methods_count[method] = methods_count.get(method, 0) + 1

    print(f"\n[*] By method:")
    for method, count in sorted(methods_count.items()):
        print(f"    {method}: {count}")

    # Показати перші 5
    print(f"\n[*] First 5 routes:")
    for route in routes[:5]:
        print(f"    {route['method']} {route.get('path', '???')} -> {route.get('controller', '???')}.{route.get('action', '???')}()")

    # Генерувати OpenAPI
    print(f"\n[*] Generating OpenAPI...")
    openapi = generate_openapi(routes)

    output_file = 'openapi_from_ast.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(openapi, f, indent=2, ensure_ascii=False)

    print(f"\n[+] Done!")
    print(f"    Output: {output_file}")
    print(f"    Paths: {len(openapi['paths'])}")
    print(f"    Endpoints: {len(routes)}")


if __name__ == '__main__':
    main()
