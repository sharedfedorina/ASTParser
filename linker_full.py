#!/usr/bin/env python3
"""
Linker для повного AST - витягує parameters, types, required, response
"""

import json
import sys


def load_ast_streaming(json_file):
    """Завантажити величезний JSON по частинах"""
    print(f"[*] Loading {json_file}...")
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data


def get_text(node):
    """Витягти текст з ноди"""
    return node.get('text', node.get('text_preview', ''))


def build_class_index(files):
    """Індекс всіх класів: FQN → class node"""
    index = {}

    print("[*] Building class index...")
    for i, file_data in enumerate(files):
        if (i+1) % 500 == 0:
            print(f"    Indexed {i+1}/{len(files)} files...")

        # Витягти classes з nodes
        for class_node in file_data.get('nodes', {}).get('classes', []):
            # Знайти namespace
            namespace = ''
            for ns_node in file_data.get('nodes', {}).get('namespaces', []):
                # Витягти namespace name
                for child in ns_node.get('children', []):
                    if child.get('field') == 'name':
                        namespace = get_text(child)
                        break
                if namespace:
                    break

            # Знайти class name
            class_name = None
            for child in class_node.get('children', []):
                if child.get('field') == 'name':
                    class_name = get_text(child)
                    break

            if class_name:
                fqn = f"{namespace}\\{class_name}" if namespace else class_name
                index[fqn] = {
                    'node': class_node,
                    'file': file_data.get('file')
                }

    return index


def find_validation_rules(class_node):
    """Знайти validation rules в FormRequest класі"""
    # Шукаємо метод rules()
    def find_method(node, name):
        if node.get('type') == 'method_declaration':
            for child in node.get('children', []):
                if child.get('field') == 'name' and get_text(child) == name:
                    return node
        for child in node.get('children', []):
            result = find_method(child, name)
            if result:
                return result
        return None

    rules_method = find_method(class_node, 'rules')
    if not rules_method:
        return {}

    # Знайти return array
    def find_return_array(node):
        if node.get('type') == 'return_statement':
            # Знайти array_creation_expression
            for child in node.get('children', []):
                if child.get('type') == 'array_creation_expression':
                    return child
                result = find_return_array(child)
                if result:
                    return result
        for child in node.get('children', []):
            result = find_return_array(child)
            if result:
                return result
        return None

    array_node = find_return_array(rules_method)
    if not array_node:
        return {}

    # Парсити array elements
    rules = {}
    for child in array_node.get('children', []):
        if child.get('type') == 'array_element_initializer':
            # Витягти key => value
            text = get_text(child)
            if '=>' in text:
                parts = text.split('=>', 1)
                field_name = parts[0].strip().strip("'\"")
                rules_str = parts[1].strip().strip("'\"")

                rules[field_name] = parse_validation_rule(rules_str)

    return rules


def parse_validation_rule(rule_str):
    """Парсити Laravel validation rule в OpenAPI schema - ПОВНИЙ парсинг"""
    rules_list = [r.strip() for r in rule_str.split('|')]

    schema = {'type': 'string'}  # default
    required = False
    nullable = False

    for rule in rules_list:
        # Required/Optional
        if rule == 'required':
            required = True
        elif rule in ['nullable', 'sometimes']:
            nullable = True

        # Types
        elif rule == 'integer':
            schema['type'] = 'integer'
        elif rule == 'numeric':
            schema['type'] = 'number'
        elif rule == 'boolean':
            schema['type'] = 'boolean'
        elif rule == 'array':
            schema['type'] = 'array'
            schema['items'] = {'type': 'string'}
        elif rule == 'string':
            schema['type'] = 'string'

        # String constraints
        elif rule.startswith('min:'):
            min_val = rule.split(':')[1]
            if schema.get('type') == 'string':
                schema['minLength'] = int(min_val)
            elif schema.get('type') in ['integer', 'number']:
                schema['minimum'] = int(min_val)

        elif rule.startswith('max:'):
            max_val = rule.split(':')[1]
            if schema.get('type') == 'string':
                schema['maxLength'] = int(max_val)
            elif schema.get('type') in ['integer', 'number']:
                schema['maximum'] = int(max_val)

        # Enums
        elif rule.startswith('in:'):
            enum_str = rule[3:]
            schema['enum'] = [v.strip() for v in enum_str.split(',')]

        # Format
        elif rule.startswith('email'):
            schema['format'] = 'email'
        elif rule == 'url':
            schema['format'] = 'uri'
        elif rule == 'date':
            schema['type'] = 'string'
            schema['format'] = 'date'
        elif rule == 'datetime':
            schema['type'] = 'string'
            schema['format'] = 'date-time'

        # Array size
        elif rule.startswith('size:'):
            size = rule.split(':')[1]
            if schema.get('type') == 'array':
                schema['minItems'] = int(size)
                schema['maxItems'] = int(size)

    if nullable:
        schema['nullable'] = True

    return {'schema': schema, 'required': required, 'nullable': nullable}


def extract_routes(files):
    """Витягти всі routes з files"""
    print("[*] Extracting routes...")
    routes = []

    for file_data in files:
        file_path = file_data.get('file', '')
        if 'routes' not in file_path.lower():
            continue

        for route_call in file_data.get('nodes', {}).get('route_calls', []):
            # Парсити Route::get('/path', [Controller::class, 'method'])
            route_info = parse_route_call_node(route_call)
            if route_info:
                route_info['source_file'] = file_path
                routes.append(route_info)

    return routes


def parse_route_call_node(node):
    """Парсити Route::method(...) ноду"""
    children = node.get('children', [])

    # Знайти scope (Route), name (get/post), arguments
    scope_text = None
    method_text = None
    args_node = None

    for child in children:
        if child.get('field') == 'scope':
            scope_text = get_text(child)
        elif child.get('field') == 'name':
            method_text = get_text(child)
        elif child.get('type') == 'arguments':
            args_node = child

    if scope_text != 'Route' or not method_text:
        return None

    route = {
        'method': method_text.upper(),
        'line': node.get('start_line')
    }

    # Парсити arguments
    if args_node:
        arguments = [c for c in args_node.get('children', []) if c.get('type') == 'argument']

        # Path (1st arg)
        if len(arguments) >= 1:
            path = extract_string_from_arg(arguments[0])
            if path:
                route['path'] = path

        # Controller (2nd arg)
        if len(arguments) >= 2:
            controller_info = extract_controller_from_arg(arguments[1])
            if controller_info:
                route.update(controller_info)

    return route


def extract_string_from_arg(arg_node):
    """Витягти string з argument"""
    for child in arg_node.get('children', []):
        if child.get('type') == 'string':
            return get_text(child).strip('"\'')
    return None


def extract_controller_from_arg(arg_node):
    """Витягти controller з [Controller::class, 'method']"""
    for child in arg_node.get('children', []):
        if child.get('type') == 'array_creation_expression':
            controller = None
            action = None

            for elem in child.get('children', []):
                if elem.get('type') == 'array_element_initializer':
                    for e_child in elem.get('children', []):
                        if e_child.get('type') == 'class_constant_access_expression':
                            # Controller::class
                            for cc in e_child.get('children', []):
                                if cc.get('is_named'):
                                    controller = get_text(cc)
                                    break
                        elif e_child.get('type') == 'string':
                            action = get_text(e_child).strip('"\'')

            if controller or action:
                return {'controller': controller, 'action': action}

    return None


def link_routes_to_schemas(routes, class_index):
    """Лінкувати routes до FormRequest і JsonResource"""
    print(f"[*] Linking {len(routes)} routes...")

    linked = []
    for i, route in enumerate(routes):
        if (i+1) % 100 == 0:
            print(f"    Linked {i+1}/{len(routes)}...")

        # Знайти controller клас
        controller = route.get('controller')
        action = route.get('action')

        if not controller or not action:
            continue

        # Шукати controller в index (може бути з namespace)
        controller_class = None
        for fqn, class_info in class_index.items():
            if controller in fqn:
                controller_class = class_info
                break

        if not controller_class:
            continue

        # Знайти метод action в controller
        method_node = find_method_in_class(controller_class['node'], action)
        if not method_node:
            continue

        # Витягти FormRequest з параметрів методу
        request_params = extract_request_from_method(method_node, class_index)

        # Витягти Response з return statements
        response_schema = extract_response_from_method(method_node, class_index)

        linked.append({
            'method': route.get('method'),
            'path': route.get('path'),
            'controller': controller,
            'action': action,
            'parameters': request_params,
            'response': response_schema,
            'line': route.get('line'),
            'file': route.get('source_file')
        })

    return linked


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


def extract_request_from_method(method_node, class_index):
    """Витягти FormRequest параметри з методу"""
    # Знайти parameters
    params_node = None
    for child in method_node.get('children', []):
        if child.get('type') == 'formal_parameters':
            params_node = child
            break

    if not params_node:
        return {}

    # Шукати параметр типу *Request
    for child in params_node.get('children', []):
        if child.get('type') == 'simple_parameter':
            # Знайти type
            param_type = None
            for p_child in child.get('children', []):
                if p_child.get('field') == 'type':
                    param_type = get_text(p_child)
                    break

            if param_type and 'Request' in param_type:
                # Знайти FormRequest клас
                for fqn, class_info in class_index.items():
                    if param_type in fqn:
                        # Витягти validation rules
                        rules = find_validation_rules(class_info['node'])
                        return rules

    return {}


def extract_response_from_method(method_node, class_index):
    """Витягти response schema з return statements"""
    # Знайти всі return statements
    def find_returns(node):
        returns = []
        if node.get('type') == 'return_statement':
            returns.append(node)
        for child in node.get('children', []):
            returns.extend(find_returns(child))
        return returns

    returns = find_returns(method_node)

    for ret_node in returns:
        # Шукати new JsonResource(...) або return UserResource::collection(...)
        text = get_text(ret_node)

        # Знайти class names в return
        for fqn, class_info in class_index.items():
            class_name = fqn.split('\\')[-1]
            if ('Resource' in class_name or 'Transformer' in class_name) and class_name in text:
                # Знайти toArray() або transform() метод
                to_array = find_method_in_class(class_info['node'], 'toArray')
                if not to_array:
                    to_array = find_method_in_class(class_info['node'], 'transform')

                if to_array:
                    # Парсити return array
                    schema = extract_array_schema_from_method(to_array)
                    return schema

    return {}


def extract_array_schema_from_method(method_node):
    """Витягти schema з toArray() методу"""
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
    schema = {'type': 'object', 'properties': {}, 'required': []}

    for child in array_node.get('children', []):
        if child.get('type') == 'array_element_initializer':
            text = get_text(child)
            if '=>' in text:
                parts = text.split('=>', 1)
                field_name = parts[0].strip().strip('"\'')
                field_value = parts[1].strip()

                # Визначити тип
                field_schema = {'type': 'string'}  # default

                if '$this->' in field_value:
                    # Поле з моделі
                    if any(x in field_name for x in ['id', 'count', 'number']):
                        field_schema['type'] = 'integer'
                    elif any(x in field_name for x in ['created_at', 'updated_at', 'date']):
                        field_schema = {'type': 'string', 'format': 'date-time'}
                    elif any(x in field_name for x in ['is_', 'has_']):
                        field_schema['type'] = 'boolean'

                schema['properties'][field_name] = field_schema

                # Required якщо не nullable
                if 'deleted_at' not in field_name and 'null' not in field_value.lower():
                    schema['required'].append(field_name)

    return schema


def main():
    if len(sys.argv) < 2:
        print("Usage: python linker_full.py <ast_full.json>")
        sys.exit(1)

    data = load_ast_streaming(sys.argv[1])

    files = data.get('files', [])
    print(f"[*] Total files: {len(files)}")

    # Build index
    class_index = build_class_index(files)
    print(f"[*] Indexed {len(class_index)} classes")

    # Extract routes
    routes = extract_routes(files)
    print(f"[*] Found {len(routes)} routes")

    # Link routes to schemas
    linked = link_routes_to_schemas(routes, class_index)
    print(f"[*] Linked {len(linked)} routes with full schemas")

    # Generate OpenAPI
    print(f"\n[*] Generating OpenAPI...")
    openapi = generate_openapi_full(linked)

    output_file = 'openapi_full.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(openapi, f, indent=2, ensure_ascii=False)

    print(f"\n[+] Done! Output: {output_file}")
    print(f"    Paths: {len(openapi['paths'])}")
    print(f"    Total endpoints: {len(linked)}")

    # Show stats
    with_params = sum(1 for r in linked if r.get('parameters'))
    with_response = sum(1 for r in linked if r.get('response'))
    print(f"\n[*] Coverage:")
    print(f"    With request params: {with_params}/{len(linked)} ({with_params*100//len(linked) if linked else 0}%)")
    print(f"    With response schema: {with_response}/{len(linked)} ({with_response*100//len(linked) if linked else 0}%)")


def generate_openapi_full(routes):
    """Генерувати повний OpenAPI з parameters і response"""
    paths = {}

    for route in routes:
        path = route.get('path')
        method = route.get('method', 'GET').lower()

        if not path or method not in ['get', 'post', 'put', 'patch', 'delete']:
            continue

        if path not in paths:
            paths[path] = {}

        # Build operation
        operation = {
            'summary': f"{method.upper()} {path}",
            'description': f"{route.get('controller', 'Unknown')}.{route.get('action', 'unknown')}()",
            'responses': {}
        }

        # Add request parameters
        params = route.get('parameters', {})
        if params and method in ['post', 'put', 'patch']:
            properties = {}
            required = []

            for field, rule_info in params.items():
                properties[field] = rule_info['schema']
                if rule_info.get('required'):
                    required.append(field)

            operation['requestBody'] = {
                'required': bool(required),
                'content': {
                    'application/json': {
                        'schema': {
                            'type': 'object',
                            'properties': properties
                        }
                    }
                }
            }

            if required:
                operation['requestBody']['content']['application/json']['schema']['required'] = required

        # Add response schema
        response = route.get('response', {})
        if response:
            operation['responses']['200'] = {
                'description': 'Success',
                'content': {
                    'application/json': {
                        'schema': response
                    }
                }
            }
        else:
            operation['responses']['200'] = {
                'description': 'Success',
                'content': {
                    'application/json': {
                        'schema': {'type': 'object'}
                    }
                }
            }

        paths[path][method] = operation

    return {
        'openapi': '3.0.3',
        'info': {
            'title': 'Laravel API - Full AST Analysis',
            'version': '1.0.0'
        },
        'paths': paths
    }


if __name__ == '__main__':
    main()
