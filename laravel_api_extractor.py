#!/usr/bin/env python3
"""
Laravel API Endpoint Extractor using Tree-Sitter
Extracts GET API endpoints with validation rules and response structures
"""

import json
import re
import os
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict, field

try:
    import tree_sitter_php as tsphp
    from tree_sitter import Language, Parser, Query, Node
except ImportError:
    print("ERROR: Required packages not installed.")
    print("Install with: pip install tree-sitter tree-sitter-php")
    exit(1)


@dataclass
class RouteParameter:
    """Route parameter definition"""
    name: str
    optional: bool = False
    type: Optional[str] = None


@dataclass
class ValidationRule:
    """Validation rule with details"""
    rule_name: str
    parameters: List[str] = field(default_factory=list)
    required: bool = False
    enum_values: List[str] = field(default_factory=list)
    min_value: Optional[int] = None
    max_value: Optional[int] = None
    default_value: Optional[Any] = None


@dataclass
class FieldValidation:
    """Field validation specification"""
    field_name: str
    field_type: str = "string"
    required: bool = False
    nullable: bool = False
    rules: List[ValidationRule] = field(default_factory=list)
    nested_fields: Dict[str, 'FieldValidation'] = field(default_factory=dict)
    is_array: bool = False
    enum_values: List[str] = field(default_factory=list)
    min_value: Optional[int] = None
    max_value: Optional[int] = None
    default_value: Optional[Any] = None


@dataclass
class ResponseField:
    """Response field structure"""
    field_name: str
    field_type: str = "mixed"
    nullable: bool = False
    nested_fields: Dict[str, 'ResponseField'] = field(default_factory=dict)
    is_array: bool = False
    description: Optional[str] = None


@dataclass
class APIEndpoint:
    """Complete API endpoint definition"""
    http_method: str
    route_path: str
    controller: Optional[str] = None
    controller_method: Optional[str] = None
    route_parameters: List[RouteParameter] = field(default_factory=list)
    request_validation: Dict[str, FieldValidation] = field(default_factory=dict)
    form_request_class: Optional[str] = None
    response_structure: Dict[str, ResponseField] = field(default_factory=dict)
    response_status: int = 200
    route_name: Optional[str] = None
    middleware: List[str] = field(default_factory=list)
    file_path: Optional[str] = None
    line_number: Optional[int] = None


class LaravelASTExtractor:
    """Extract API information from Laravel project using Tree-Sitter"""
    
    def __init__(self, project_path: str):
        """Initialize parser with Laravel project path"""
        self.project_path = Path(project_path)
        self.php_language = Language(tsphp.language_php())
        self.parser = Parser(self.php_language)
        
        # Storage for extracted data
        self.routes: List[APIEndpoint] = []
        self.form_requests: Dict[str, Dict[str, FieldValidation]] = {}
        self.controllers: Dict[str, Dict] = {}
        self.resources: Dict[str, Dict[str, ResponseField]] = {}
        
        # Route to FormRequest mapping
        self.route_form_request_map: Dict[Tuple[str, str], str] = {}
    
    def parse_file(self, file_path: Path) -> Tuple[Optional[Any], Optional[bytes]]:
        """Parse a PHP file and return tree and source"""
        try:
            with open(file_path, 'rb') as f:
                source_code = f.read()
            tree = self.parser.parse(source_code)
            return tree, source_code
        except Exception as e:
            print(f"Error parsing {file_path}: {e}")
            return None, None
    
    def get_node_text(self, node: Node, source: bytes) -> str:
        """Extract text for a node"""
        return source[node.start_byte:node.end_byte].decode('utf8', errors='ignore')
    
    def extract_all(self) -> List[APIEndpoint]:
        """Extract all API endpoint information from Laravel project"""
        print("Extracting Laravel API endpoints...")
        
        # Step 1: Extract FormRequest validation rules
        print("  - Parsing FormRequest classes...")
        self.extract_form_requests()
        
        # Step 2: Extract API Resources
        print("  - Parsing API Resources...")
        self.extract_resources()
        
        # Step 3: Extract Controllers
        print("  - Parsing Controllers...")
        self.extract_controllers()
        
        # Step 4: Extract Routes
        print("  - Parsing Routes...")
        self.extract_routes()
        
        # Step 5: Link everything together
        print("  - Linking routes with validation and responses...")
        self.link_route_data()
        
        # Filter only GET methods
        get_routes = [r for r in self.routes if r.http_method.upper() == 'GET']
        
        print(f"\nExtraction complete: {len(get_routes)} GET endpoints found")
        return get_routes
    
    def extract_form_requests(self):
        """Extract validation rules from FormRequest classes"""
        requests_path = self.project_path / 'app' / 'Http' / 'Requests'
        if not requests_path.exists():
            return
        
        for php_file in requests_path.rglob('*.php'):
            tree, source = self.parse_file(php_file)
            if not tree or not source:
                continue
            
            # Find FormRequest classes
            query = self.php_language.query("""
                (class_declaration
                    name: (name) @class_name
                    base_clause: (base_clause) @base
                    body: (declaration_list) @body) @class
            """)
            
            captures = query.captures(tree.root_node)
            
            for node, capture_name in captures:
                if capture_name == 'class':
                    class_name_node = node.child_by_field_name('name')
                    base_clause = node.child_by_field_name('base_clause')
                    
                    if base_clause:
                        base_text = self.get_node_text(base_clause, source)
                        if 'FormRequest' in base_text:
                            class_name = self.get_node_text(class_name_node, source)
                            rules = self.extract_rules_from_class(node, source)
                            if rules:
                                self.form_requests[class_name] = rules
    
    def extract_rules_from_class(self, class_node: Node, source: bytes) -> Dict[str, FieldValidation]:
        """Extract validation rules from FormRequest class"""
        body = class_node.child_by_field_name('body')
        if not body:
            return {}
        
        # Find rules() method
        for child in body.children:
            if child.type == 'method_declaration':
                method_name = child.child_by_field_name('name')
                if method_name and self.get_node_text(method_name, source) == 'rules':
                    return self.extract_validation_rules(child, source)
        
        return {}
    
    def extract_validation_rules(self, method_node: Node, source: bytes) -> Dict[str, FieldValidation]:
        """Extract validation rules from rules() method"""
        rules = {}
        body = method_node.child_by_field_name('body')
        if not body:
            return rules
        
        # Find return statement with array
        for child in body.children:
            if child.type == 'return_statement':
                # Find array creation expression
                for subchild in child.children:
                    if subchild.type == 'array_creation_expression':
                        rules = self.parse_validation_array(subchild, source)
                        break
                break
        
        return rules
    
    def parse_validation_array(self, array_node: Node, source: bytes) -> Dict[str, FieldValidation]:
        """Parse validation array and extract field rules"""
        rules = {}
        
        for child in array_node.children:
            if child.type == 'array_element_initializer':
                for element in child.children:
                    if element.type == 'array_element':
                        key_node = element.child_by_field_name('key')
                        value_node = element.child_by_field_name('value')
                        
                        if key_node and value_node:
                            field_name = self.get_node_text(key_node, source).strip('\'"')
                            validation = self.parse_validation_value(value_node, source)
                            
                            # Handle nested fields (e.g., 'user.email', 'items.*.name')
                            if '.' in field_name:
                                rules.update(self.handle_nested_validation(field_name, validation))
                            else:
                                rules[field_name] = validation
        
        return rules
    
    def parse_validation_value(self, value_node: Node, source: bytes) -> FieldValidation:
        """Parse validation rules from value node""" 
        validation = FieldValidation(field_name="")
        rule_strings = []
        
        if value_node.type in ['string', 'encapsed_string']:
            # Pipe-delimited string: 'required|string|max:255'
            text = self.get_node_text(value_node, source).strip('\'"')
            rule_strings = [r.strip() for r in text.split('|')]
        
        elif value_node.type == 'array_creation_expression':
            # Array of rules: ['required', 'string', 'max:255']
            for child in value_node.children:
                if child.type == 'array_element_initializer':
                    for element in child.children:
                        if element.type == 'array_element':
                            val = element.child_by_field_name('value')
                            if val and val.type in ['string', 'encapsed_string']:
                                rule_strings.append(
                                    self.get_node_text(val, source).strip('\'"')
                                )
        
        # Parse individual rules
        validation.rules = []
        for rule_str in rule_strings:
            parsed_rule = self.parse_single_rule(rule_str)
            validation.rules.append(parsed_rule)
            
            # Set field-level properties based on rules
            if parsed_rule.rule_name == 'required':
                validation.required = True
            elif parsed_rule.rule_name == 'nullable':
                validation.nullable = True
            elif parsed_rule.rule_name in ['string', 'integer', 'numeric', 'boolean', 'array', 'file', 'image']:
                validation.field_type = parsed_rule.rule_name
            elif parsed_rule.rule_name == 'min' and parsed_rule.parameters:
                try:
                    validation.min_value = int(parsed_rule.parameters[0])
                except ValueError:
                    pass
            elif parsed_rule.rule_name == 'max' and parsed_rule.parameters:
                try:
                    validation.max_value = int(parsed_rule.parameters[0])
                except ValueError:
                    pass
            elif parsed_rule.rule_name == 'in' and parsed_rule.parameters:
                validation.enum_values = parsed_rule.parameters
            elif parsed_rule.rule_name.startswith('enum'):
                validation.enum_values = parsed_rule.enum_values
        
        return validation
    
    def parse_single_rule(self, rule_str: str) -> ValidationRule:
        """Parse a single validation rule string"""
        rule = ValidationRule(rule_name=rule_str)
        
        # Handle rules with parameters: 'max:255', 'in:foo,bar,baz'
        if ':' in rule_str:
            parts = rule_str.split(':', 1)
            rule.rule_name = parts[0]
            rule.parameters = [p.strip() for p in parts[1].split(',')]
            
            # Special handling for enum values in 'in' rule
            if rule.rule_name == 'in':
                rule.enum_values = rule.parameters
        
        # Mark as required
        if rule.rule_name == 'required':
            rule.required = True
        
        return rule
    
    def handle_nested_validation(self, field_path: str, validation: FieldValidation) -> Dict[str, FieldValidation]:
        """Handle nested field validation like 'user.email' or 'items.*.name'""" 
        parts = field_path.split('.')
        result = {}
        
        # Check if it's an array wildcard pattern
        if '*' in parts:
            # Handle array patterns like 'items.*.name'
            base_field = parts[0]
            if base_field not in result:
                result[base_field] = FieldValidation(field_name=base_field, is_array=True)
            
            # Add nested validation for array items
            nested_path = '.'.join(parts[2:]) if len(parts) > 2 else parts[-1]
            validation.field_name = nested_path
            result[base_field].nested_fields[nested_path] = validation
        else:
            # Regular nested object like 'user.email'
            validation.field_name = field_path
            result[field_path] = validation
        
        return result
    
    def extract_resources(self):
        """Extract response structures from API Resource classes"""
        resources_path = self.project_path / 'app' / 'Http' / 'Resources'
        if not resources_path.exists():
            return
        
        for php_file in resources_path.rglob('*.php'):
            tree, source = self.parse_file(php_file)
            if not tree or not source:
                continue
            
            # Find Resource classes
            query = self.php_language.query("""
                (class_declaration
                    name: (name) @class_name
                    base_clause: (base_clause) @base
                    body: (declaration_list) @body) @class
            """)
            
            captures = query.captures(tree.root_node)
            
            for node, capture_name in captures:
                if capture_name == 'class':
                    base_clause = node.child_by_field_name('base_clause')
                    if base_clause:
                        base_text = self.get_node_text(base_clause, source)
                        if 'JsonResource' in base_text or 'Resource' in base_text:
                            class_name_node = node.child_by_field_name('name')
                            class_name = self.get_node_text(class_name_node, source)
                            response_fields = self.extract_resource_fields(node, source)
                            if response_fields:
                                self.resources[class_name] = response_fields
    
    def extract_resource_fields(self, class_node: Node, source: bytes) -> Dict[str, ResponseField]:
        """Extract response fields from toArray() method in Resource"""
        body = class_node.child_by_field_name('body')
        if not body:
            return {}
        
        # Find toArray() method
        for child in body.children:
            if child.type == 'method_declaration':
                method_name = child.child_by_field_name('name')
                if method_name and self.get_node_text(method_name, source) == 'toArray':
                    return self.extract_response_array(child, source)
        
        return {}
    
    def extract_response_array(self, method_node: Node, source: bytes) -> Dict[str, ResponseField]:
        """Extract response array structure from toArray() method"""
        fields = {}
        body = method_node.child_by_field_name('body')
        if not body:
            return fields
        
        # Find return statement
        for child in body.children:
            if child.type == 'return_statement':
                for subchild in child.children:
                    if subchild.type == 'array_creation_expression':
                        fields = self.parse_response_array(subchild, source)
                        break
                break
        
        return fields
    
    def parse_response_array(self, array_node: Node, source: bytes) -> Dict[str, ResponseField]:
        """Parse response array structure"""
        fields = {}
        
        for child in array_node.children:
            if child.type == 'array_element_initializer':
                for element in child.children:
                    if element.type == 'array_element':
                        key_node = element.child_by_field_name('key')
                        value_node = element.child_by_field_name('value')
                        
                        if key_node and value_node:
                            field_name = self.get_node_text(key_node, source).strip('\'"')
                            field = ResponseField(field_name=field_name)
                            
                            # Infer type from value
                            value_text = self.get_node_text(value_node, source)
                            field.field_type = self.infer_response_type(value_node, value_text, source)
                            
                            # Check if it's an array
                            if value_node.type == 'array_creation_expression':
                                field.is_array = True
                                field.nested_fields = self.parse_response_array(value_node, source)
                            
                            fields[field_name] = field
        
        return fields
    
    def infer_response_type(self, node: Node, text: str, source: bytes) -> str:
        """Infer response field type from node"""
        if node.type == 'integer':
            return 'integer'
        elif node.type == 'float':
            return 'float'
        elif node.type in ['string', 'encapsed_string']:
            return 'string'
        elif node.type == 'boolean':
            return 'boolean'
        elif node.type == 'array_creation_expression':
            return 'array'
        elif 'Resource::collection' in text:
            return 'collection'
        elif 'Resource' in text:
            return 'resource'
        else:
            return 'mixed'
    
    def extract_controllers(self):
        """Extract controller methods and type hints"""
        controllers_path = self.project_path / 'app' / 'Http' / 'Controllers'
        if not controllers_path.exists():
            return
        
        for php_file in controllers_path.rglob('*.php'):
            tree, source = self.parse_file(php_file)
            if not tree or not source:
                continue
            
            # Find Controller classes
            query = self.php_language.query("""
                (class_declaration
                    name: (name) @class_name
                    body: (declaration_list) @body) @class
            """)
            
            captures = query.captures(tree.root_node)
            
            for node, capture_name in captures:
                if capture_name == 'class':
                    class_name_node = node.child_by_field_name('name')
                    class_name = self.get_node_text(class_name_node, source)
                    methods = self.extract_controller_methods(node, source)
                    if methods:
                        self.controllers[class_name] = methods
    
    def extract_controller_methods(self, class_node: Node, source: bytes) -> Dict:
        """Extract methods from controller class""" 
        methods = {}
        body = class_node.child_by_field_name('body')
        if not body:
            return methods
        
        for child in body.children:
            if child.type == 'method_declaration':
                method_name_node = child.child_by_field_name('name')
                if method_name_node:
                    method_name = self.get_node_text(method_name_node, source)
                    method_info = {
                        'name': method_name,
                        'parameters': [],
                        'form_request': None,
                        'return_type': None
                    }
                    
                    # Extract parameters
                    params = child.child_by_field_name('parameters')
                    if params:
                        method_info['parameters'] = self.extract_method_parameters(params, source)
                        
                        # Check for FormRequest parameter
                        for param in method_info['parameters']:
                            if param.get('type', '').endswith('Request'):
                                method_info['form_request'] = param['type']
                    
                    # Extract return type if available
                    return_type = child.child_by_field_name('return_type')
                    if return_type:
                        method_info['return_type'] = self.get_node_text(return_type, source)
                    
                    methods[method_name] = method_info
        
        return methods
    
    def extract_method_parameters(self, params_node: Node, source: bytes) -> List[Dict]:
        """Extract method parameters with type hints"""
        parameters = []
        
        for child in params_node.children:
            if child.type == 'simple_parameter':
                param_info = {}
                
                # Extract type hint
                type_node = child.child_by_field_name('type')
                if type_node:
                    param_info['type'] = self.get_node_text(type_node, source)
                
                # Extract parameter name
                name_node = child.child_by_field_name('name')
                if name_node:
                    param_info['name'] = self.get_node_text(name_node, source)
                
                # Check for default value
                default_value = child.child_by_field_name('default_value')
                if default_value:
                    param_info['default'] = self.get_node_text(default_value, source)
                
                parameters.append(param_info)
        
        return parameters
    
    def extract_routes(self):
        """Extract routes from route files"""
        routes_path = self.project_path / 'routes'
        if not routes_path.exists():
            return
        
        # Check both web.php and api.php
        route_files = ['api.php', 'web.php']
        for route_file in route_files:
            file_path = routes_path / route_file
            if not file_path.exists():
                continue
            
            tree, source = self.parse_file(file_path)
            if not tree or not source:
                continue
            
            # Query for Route static calls
            query = self.php_language.query("""
                (expression_statement
                    (scoped_call_expression
                        scope: (name) @scope
                        name: (name) @method
                        arguments: (arguments) @args) @route_call)
            """)
            
            captures = query.captures(tree.root_node)
            route_nodes = []
            
            for node, capture_name in captures:
                if capture_name == 'scope':
                    scope_text = self.get_node_text(node, source)
                    if scope_text == 'Route':
                        parent = node.parent
                        if parent and parent.type == 'scoped_call_expression':
                            route_nodes.append(parent)
            
            # Parse each route
            for route_node in route_nodes:
                endpoint = self.parse_route_definition(route_node, source, str(file_path))
                if endpoint and endpoint.http_method.upper() == 'GET':
                    self.routes.append(endpoint)
    
    def parse_route_definition(self, route_node: Node, source: bytes, file_path: str) -> Optional[APIEndpoint]:
        """Parse a Route::method() call"""
        method_node = route_node.child_by_field_name('name')
        args_node = route_node.child_by_field_name('arguments')
        
        if not method_node or not args_node:
            return None
        
        http_method = self.get_node_text(method_node, source)
        
        # Only process standard HTTP methods
        if http_method not in ['get', 'post', 'put', 'patch', 'delete', 'options']:
            return None
        
        endpoint = APIEndpoint(
            http_method=http_method.upper(),
            route_path="",
            file_path=file_path,
            line_number=route_node.start_point[0] + 1
        )
        
        # Extract arguments
        args = []
        for child in args_node.children:
            if child.type not in ['(', ')', ',']:
                args.append(child)
        
        # First argument is the path
        if len(args) > 0:
            path_text = self.get_node_text(args[0], source)
            endpoint.route_path = path_text.strip('\'"')
            
            # Extract route parameters
            endpoint.route_parameters = self.extract_route_parameters(endpoint.route_path)
        
        # Second argument is the handler (controller or closure)
        if len(args) > 1:
            handler = args[1]
            if handler.type == 'array_creation_expression':
                # Controller array: [Controller::class, 'method']
                controller_info = self.parse_controller_handler(handler, source)
                if controller_info:
                    endpoint.controller = controller_info['controller']
                    endpoint.controller_method = controller_info['method']
        
        # Check for chained methods (->name(), ->middleware())
        self.extract_route_chains(route_node, endpoint, source)
        
        return endpoint
    
    def extract_route_parameters(self, route_path: str) -> List[RouteParameter]:
        """Extract parameters from route path like /users/{id}/posts/{post?}"""
        parameters = []
        
        # Match {param} and {param?}
        matches = re.finditer(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\??}', route_path)
        
        for match in matches:
            param_name = match.group(1)
            optional = match.group(0).endswith('?}')
            parameters.append(RouteParameter(name=param_name, optional=optional))
        
        return parameters
    
    def parse_controller_handler(self, handler_node: Node, source: bytes) -> Optional[Dict]:
        """Parse controller array handler [Controller::class, 'method']"""
        elements = []
        
        for child in handler_node.children:
            if child.type == 'array_element_initializer':
                for element in child.children:
                    if element.type == 'array_element':
                        value = element.child_by_field_name('value')
                        if value:
                            elements.append(value)
        
        if len(elements) < 2:
            return None
        
        controller = None
        method = None
        
        # First element: Controller::class
        if elements[0].type == 'scoped_property_access_expression':
            scope = elements[0].child_by_field_name('scope')
            if scope:
                controller = self.get_node_text(scope, source)
        
        # Second element: 'method'
        if elements[1].type in ['string', 'encapsed_string']:
            method = self.get_node_text(elements[1], source).strip('\'"')
        
        if controller and method:
            return {'controller': controller, 'method': method}
        
        return None
    
    def extract_route_chains(self, route_node: Node, endpoint: APIEndpoint, source: bytes):
        """Extract chained method calls like ->name() or ->middleware()"""
        parent = route_node.parent
        
        while parent and parent.type == 'member_call_expression':
            method_name_node = parent.child_by_field_name('name')
            if method_name_node:
                method_name = self.get_node_text(method_name_node, source)
                args_node = parent.child_by_field_name('arguments')
                
                if method_name == 'name' and args_node:
                    # Extract route name
                    for child in args_node.children:
                        if child.type in ['string', 'encapsed_string']:
                            endpoint.route_name = self.get_node_text(child, source).strip('\'"')
                            break
                
                elif method_name == 'middleware' and args_node:
                    # Extract middleware
                    for child in args_node.children:
                        if child.type in ['string', 'encapsed_string']:
                            middleware = self.get_node_text(child, source).strip('\'"')
                            endpoint.middleware.append(middleware)
            
            parent = parent.parent
    
    def link_route_data(self):
        """Link routes with FormRequests, controllers, and resources"""
        for endpoint in self.routes:
            if not endpoint.controller or not endpoint.controller_method:
                continue
            
            # Find controller info
            controller_info = self.controllers.get(endpoint.controller)
            if controller_info:
                method_info = controller_info.get(endpoint.controller_method)
                if method_info:
                    # Link FormRequest
                    form_request = method_info.get('form_request')
                    if form_request:
                        endpoint.form_request_class = form_request
                        
                        # Find validation rules for this FormRequest
                        for fr_name, fr_rules in self.form_requests.items():
                            if fr_name in form_request or form_request.endswith(fr_name):
                                endpoint.request_validation = fr_rules
                                break
                    
                    # Link Resource response
                    return_type = method_info.get('return_type', '')
                    for resource_name, resource_fields in self.resources.items():
                        if resource_name in return_type:
                            endpoint.response_structure = resource_fields
                            break
    
    def to_dict(self, endpoints: List[APIEndpoint]) -> List[Dict]:
        """Convert endpoints to dictionary format"""
        result = []
        
        for endpoint in endpoints:
            endpoint_dict = {
                'http_method': endpoint.http_method,
                'route_path': endpoint.route_path,
                'route_name': endpoint.route_name,
                'controller': endpoint.controller,
                'controller_method': endpoint.controller_method,
                'route_parameters': [
                    {'name': p.name, 'optional': p.optional, 'type': p.type}
                    for p in endpoint.route_parameters
                ],
                'request_validation': {},
                'response_structure': {},
                'middleware': endpoint.middleware,
                'file_path': endpoint.file_path,
                'line_number': endpoint.line_number
            }
            
            # Convert request validation
            for field_name, validation in endpoint.request_validation.items():
                endpoint_dict['request_validation'][field_name] = {
                    'field_type': validation.field_type,
                    'required': validation.required,
                    'nullable': validation.nullable,
                    'is_array': validation.is_array,
                    'enum_values': validation.enum_values,
                    'min_value': validation.min_value,
                    'max_value': validation.max_value,
                    'default_value': validation.default_value,
                    'rules': [
                        {
                            'rule': r.rule_name,
                            'parameters': r.parameters,
                            'enum_values': r.enum_values
                        }
                        for r in validation.rules
                    ],
                    'nested_fields': {
                        nf_name: {
                            'field_type': nf.field_type,
                            'required': nf.required,
                            'nullable': nf.nullable
                        }
                        for nf_name, nf in validation.nested_fields.items()
                    } if validation.nested_fields else {}
                }
            
            # Convert response structure
            for field_name, field in endpoint.response_structure.items():
                endpoint_dict['response_structure'][field_name] = {
                    'field_type': field.field_type,
                    'nullable': field.nullable,
                    'is_array': field.is_array,
                    'description': field.description,
                    'nested_fields': {
                        nf_name: {
                            'field_type': nf.field_type,
                            'nullable': nf.nullable,
                            'is_array': nf.is_array
                        }
                        for nf_name, nf in field.nested_fields.items()
                    } if field.nested_fields else {}
                }
            
            result.append(endpoint_dict)
        
        return result
    
    def generate_documentation(self, endpoints: List[APIEndpoint], output_format: str = 'json') -> str:
        """Generate API documentation in specified format"""
        if output_format == 'json':
            return json.dumps(self.to_dict(endpoints), indent=2)
        
        elif output_format == 'markdown':
            return self.generate_markdown_docs(endpoints)
        
        else:
            raise ValueError(f"Unsupported output format: {output_format}")
    
    def generate_markdown_docs(self, endpoints: List[APIEndpoint]) -> str:
        """Generate Markdown documentation"""
        lines = ["# API Documentation\n"]
        lines.append("## GET Endpoints\n")
        
        for endpoint in endpoints:
            # Endpoint header
            lines.append(f"### {endpoint.http_method} {endpoint.route_path}\n")
            
            if endpoint.route_name:
                lines.append(f"**Route Name:** `{endpoint.route_name}`\n")
            
            if endpoint.controller and endpoint.controller_method:
                lines.append(f"**Controller:** `{endpoint.controller}@{endpoint.controller_method}`\n")
            
            # Route parameters
            if endpoint.route_parameters:
                lines.append("\n**Route Parameters:**\n")
                for param in endpoint.route_parameters:
                    optional = " (optional)" if param.optional else " (required)"
                    lines.append(f"- `{param.name}`{optional}\n")
            
            # Request validation
            if endpoint.request_validation:
                lines.append("\n**Request Validation:**\n")
                lines.append("| Field | Type | Required | Rules |\n")
                lines.append("|-------|------|----------|-------|\n")
                
                for field_name, validation in endpoint.request_validation.items():
                    required = "Yes" if validation.required else "No"
                    rules = ", ".join([r.rule_name for r in validation.rules])
                    lines.append(f"| {field_name} | {validation.field_type} | {required} | {rules} |\n")
                    
                    # Show nested fields
                    if validation.nested_fields:
                        for nested_name, nested_field in validation.nested_fields.items():
                            nested_required = "Yes" if nested_field.required else "No"
                            nested_rules = ", ".join([r.rule_name for r in nested_field.rules])
                            lines.append(f"| ↳ {nested_name} | {nested_field.field_type} | {nested_required} | {nested_rules} |\n")
            
            # Response structure
            if endpoint.response_structure:
                lines.append("\n**Response Structure:**\n")
                lines.append("```json\n")
                lines.append(self.generate_sample_response(endpoint.response_structure))
                lines.append("```\n")
            
            lines.append("\n---\n\n")
        
        return "".join(lines)
    
    def generate_sample_response(self, response_structure: Dict[str, ResponseField], indent: int = 0) -> str:
        """Generate sample JSON response structure"""
        lines = []
        indent_str = "  " * indent
        
        lines.append(indent_str + "{\n")
        
        items = list(response_structure.items())
        for i, (field_name, field) in enumerate(items):
            comma = "," if i < len(items) - 1 else ""
            
            if field.is_array:
                lines.append(f'{indent_str}  "{field_name}": [\n')
                if field.nested_fields:
                    lines.append(self.generate_sample_response(field.nested_fields, indent + 2))
                else:
                    lines.append(f'{indent_str}    {self.get_sample_value(field.field_type)}\n')
                lines.append(f'{indent_str}  ]{comma}\n')
            elif field.nested_fields:
                lines.append(f'{indent_str}  "{field_name}": \n')
                lines.append(self.generate_sample_response(field.nested_fields, indent + 1))
                lines.append(f'{comma}\n')
            else:
                sample_value = self.get_sample_value(field.field_type)
                lines.append(f'{indent_str}  "{field_name}": {sample_value}{comma}\n')
        
        lines.append(indent_str + "}\n")
        
        return "".join(lines)
    
    def get_sample_value(self, field_type: str) -> str:
        """Get sample value for field type"""
        samples = {
            'string': '"string"',
            'integer': '0',
            'float': '0.0',
            'boolean': 'false',
            'array': '[]',
            'object': '{}',
            'mixed': 'null'
        }
        return samples.get(field_type, 'null')


def main():
    """Main execution function"""
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python laravel_api_extractor.py <laravel_project_path> [output_format]")
        print("  output_format: json (default) or markdown")
        sys.exit(1)
    
    project_path = sys.argv[1]
    output_format = sys.argv[2] if len(sys.argv) > 2 else 'json'
    
    if not os.path.exists(project_path):
        print(f"Error: Project path '{project_path}' does not exist")
        sys.exit(1)
    
    print(f"Analyzing Laravel project at: {project_path}")
    print("=" * 60)
    
    # Extract API endpoints
    extractor = LaravelASTExtractor(project_path)
    endpoints = extractor.extract_all()
    
    # Generate documentation
    documentation = extractor.generate_documentation(endpoints, output_format)
    
    # Output results
    output_file = f'api_documentation.{output_format}'
    with open(output_file, 'w') as f:
        f.write(documentation)
    
    print(f"\n✓ Documentation generated: {output_file}")
    print(f"✓ Total GET endpoints: {len(endpoints)}")


if __name__ == '__main__':
    main()


