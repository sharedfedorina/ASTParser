#!/usr/bin/env python3
"""
PHP Routes AST Parser v2 - with detailed API index
Parses routes file and all dependencies, creates detailed index for API documentation
"""

import os
import json
import re
import gc
from pathlib import Path
from tree_sitter import Language, Parser
import tree_sitter_php as tsphp

class DetailedASTParser:
    def __init__(self, project_root, routes_file):
        self.project_root = Path(project_root)
        self.routes_file = Path(routes_file)
        self.ast_dir = Path(__file__).parent / 'AST'

        self.ast_dir.mkdir(exist_ok=True)

        self.parser = Parser()
        PHP_LANGUAGE = Language(tsphp.language_php())
        self.parser.language = PHP_LANGUAGE

        self.processed_files = set()
        self.files_to_process = []

        self.dependency_tree = {}

        # Detailed API index
        self.api_index = {
            'controllers': {},
            'requests': {},
            'resources': {},
            'models': {},
            'enums': {},
            'traits': {},
            'interfaces': {}
        }

    def get_ast_file_path(self, php_file_path):
        """Get AST JSON file path for PHP file"""
        relative = php_file_path.relative_to(self.project_root)
        safe_name = str(relative).replace('/', '_').replace('\\', '_')
        return self.ast_dir / f"{safe_name}.json"

    def ast_exists(self, php_file_path):
        """Check if AST file already exists"""
        return self.get_ast_file_path(php_file_path).exists()

    def node_to_dict(self, node):
        """Convert tree-sitter node to dictionary"""
        result = {
            'type': node.type,
            'start_point': {'row': node.start_point[0], 'column': node.start_point[1]},
            'end_point': {'row': node.end_point[0], 'column': node.end_point[1]},
            'start_byte': node.start_byte,
            'end_byte': node.end_byte,
            'text': node.text.decode('utf-8') if node.text else None,
        }

        if node.child_count > 0:
            result['children'] = [self.node_to_dict(child) for child in node.children]

        return result

    def get_node_text(self, node):
        """Get text from node"""
        return node.text.decode('utf-8') if node.text else ''

    def find_child_by_type(self, node, node_type):
        """Find first child with specific type"""
        for child in node.children:
            if child.type == node_type:
                return child
        return None

    def find_children_by_type(self, node, node_type):
        """Find all children with specific type"""
        return [child for child in node.children if child.type == node_type]

    def extract_class_name(self, class_node):
        """Extract class name from class_declaration node"""
        name_node = self.find_child_by_type(class_node, 'name')
        return self.get_node_text(name_node) if name_node else None

    def extract_parent_class(self, class_node):
        """Extract parent class from extends clause"""
        base_clause = self.find_child_by_type(class_node, 'base_clause')
        if base_clause:
            name_node = self.find_child_by_type(base_clause, 'name')
            if name_node:
                return self.get_node_text(name_node)
        return None

    def extract_class_methods(self, class_node):
        """Extract all methods from class"""
        methods = {}

        declaration_list = self.find_child_by_type(class_node, 'declaration_list')
        if not declaration_list:
            return methods

        for child in declaration_list.children:
            if child.type == 'method_declaration':
                method_info = self.extract_method_details(child)
                if method_info:
                    methods[method_info['name']] = method_info

        return methods

    def extract_method_details(self, method_node):
        """Extract method name, parameters, return type"""
        name_node = self.find_child_by_type(method_node, 'name')
        if not name_node:
            return None

        method_name = self.get_node_text(name_node)

        # Extract parameters
        parameters = []
        formal_params = self.find_child_by_type(method_node, 'formal_parameters')
        if formal_params:
            for param in formal_params.children:
                if param.type == 'simple_parameter':
                    param_info = self.extract_parameter_info(param)
                    if param_info:
                        parameters.append(param_info)

        # Extract visibility
        visibility = 'public'
        for child in method_node.children:
            if child.type in ['public', 'protected', 'private']:
                visibility = child.type
                break

        return {
            'name': method_name,
            'line': method_node.start_point[0] + 1,
            'parameters': parameters,
            'visibility': visibility,
            'text': self.get_node_text(method_node)
        }

    def extract_parameter_info(self, param_node):
        """Extract parameter name and type"""
        param_name = None
        param_type = None

        # Find variable_name
        for child in param_node.children:
            if child.type == 'variable_name':
                param_name = self.get_node_text(child)
            elif child.type in ['named_type', 'primitive_type', 'union_type']:
                param_type = self.get_node_text(child)

        if param_name:
            return {
                'name': param_name,
                'type': param_type
            }
        return None

    def extract_class_properties(self, class_node):
        """Extract class properties (fillable, casts, etc)"""
        properties = {}

        declaration_list = self.find_child_by_type(class_node, 'declaration_list')
        if not declaration_list:
            return properties

        for child in declaration_list.children:
            if child.type == 'property_declaration':
                prop_info = self.extract_property_info(child)
                if prop_info:
                    properties[prop_info['name']] = prop_info

        return properties

    def extract_property_info(self, prop_node):
        """Extract property name and value"""
        # Find property name
        prop_name = None
        prop_value = None

        for child in prop_node.children:
            if child.type == 'property_element':
                var_name = self.find_child_by_type(child, 'variable_name')
                if var_name:
                    prop_name = self.get_node_text(var_name).lstrip('$')

                # Try to get property value (for arrays like fillable, casts)
                prop_initializer = self.find_child_by_type(child, 'property_initializer')
                if prop_initializer:
                    array_node = self.find_child_by_type(prop_initializer, 'array_creation_expression')
                    if array_node:
                        prop_value = self.extract_array_values(array_node)

        if prop_name:
            return {
                'name': prop_name,
                'value': prop_value,
                'line': prop_node.start_point[0] + 1
            }
        return None

    def extract_array_values(self, array_node):
        """Extract values from array expression"""
        values = []

        for child in array_node.children:
            if child.type == 'array_element_initializer':
                # Simple value
                string_node = self.find_child_by_type(child, 'string')
                if string_node:
                    value = self.get_node_text(string_node).strip('"\'')
                    values.append(value)

        return values

    def extract_enum_cases(self, enum_node):
        """Extract enum cases"""
        cases = []

        declaration_list = self.find_child_by_type(enum_node, 'declaration_list')
        if not declaration_list:
            return cases

        for child in declaration_list.children:
            if child.type == 'enum_case':
                name_node = self.find_child_by_type(child, 'name')
                if name_node:
                    case_name = self.get_node_text(name_node)

                    # Try to get value
                    value = None
                    for c in child.children:
                        if c.type in ['string', 'integer']:
                            value = self.get_node_text(c).strip('"\'')

                    cases.append({
                        'name': case_name,
                        'value': value,
                        'line': child.start_point[0] + 1
                    })

        return cases

    def analyze_class_structure(self, class_node, file_path, class_name):
        """Analyze class and add to appropriate index"""
        relative_path = str(file_path.relative_to(self.project_root))
        ast_file = str(self.get_ast_file_path(file_path).relative_to(Path(__file__).parent))

        parent_class = self.extract_parent_class(class_node)
        methods = self.extract_class_methods(class_node)
        properties = self.extract_class_properties(class_node)

        base_info = {
            'file': relative_path,
            'ast_file': ast_file,
            'line': class_node.start_point[0] + 1,
            'parent': parent_class
        }

        # Classify by file path or parent class
        if 'Controller' in class_name or '/Controllers/' in relative_path:
            self.api_index['controllers'][class_name] = {
                **base_info,
                'methods': methods
            }
            print(f"      [CONTROLLER] {class_name} with {len(methods)} methods")

        elif 'Request' in class_name or '/Requests/' in relative_path:
            # Extract rules from rules() method
            rules = []
            if 'rules' in methods:
                rules_method_text = methods['rules'].get('text', '')
                # Simple extraction of array keys (can be improved)
                rules = re.findall(r"'([^']+)'\s*=>", rules_method_text)

            self.api_index['requests'][class_name] = {
                **base_info,
                'methods': methods,
                'rules': rules
            }
            print(f"      [REQUEST] {class_name} with {len(rules)} rules")

        elif 'Resource' in class_name or '/Resources/' in relative_path:
            self.api_index['resources'][class_name] = {
                **base_info,
                'methods': methods
            }
            print(f"      [RESOURCE] {class_name}")

        elif parent_class == 'Model' or '/Models/' in relative_path:
            # Extract fillable, casts
            fillable = properties.get('fillable', {}).get('value', [])
            casts = properties.get('casts', {}).get('value', [])

            self.api_index['models'][class_name] = {
                **base_info,
                'properties': properties,
                'fillable': fillable,
                'casts': casts,
                'methods': methods
            }
            print(f"      [MODEL] {class_name} with {len(fillable)} fillable fields")

        else:
            # Generic class - skip or add to general index
            pass

    def analyze_structures(self, root_node, file_path):
        """Analyze all structures in file and add to index"""
        relative_path = str(file_path.relative_to(self.project_root))

        def traverse(node):
            # Class declaration
            if node.type == 'class_declaration':
                class_name = self.extract_class_name(node)
                if class_name:
                    self.analyze_class_structure(node, file_path, class_name)

            # Enum declaration
            elif node.type == 'enum_declaration':
                enum_name = self.extract_class_name(node)
                if enum_name:
                    cases = self.extract_enum_cases(node)
                    ast_file = str(self.get_ast_file_path(file_path).relative_to(Path(__file__).parent))

                    self.api_index['enums'][enum_name] = {
                        'file': relative_path,
                        'ast_file': ast_file,
                        'line': node.start_point[0] + 1,
                        'cases': cases
                    }
                    print(f"      [ENUM] {enum_name} with {len(cases)} cases")

            # Trait declaration
            elif node.type == 'trait_declaration':
                trait_name = self.extract_class_name(node)
                if trait_name:
                    methods = self.extract_class_methods(node)
                    ast_file = str(self.get_ast_file_path(file_path).relative_to(Path(__file__).parent))

                    self.api_index['traits'][trait_name] = {
                        'file': relative_path,
                        'ast_file': ast_file,
                        'line': node.start_point[0] + 1,
                        'methods': methods
                    }
                    print(f"      [TRAIT] {trait_name}")

            # Recursively traverse children
            for child in node.children:
                traverse(child)

        traverse(root_node)

    def extract_use_statements(self, node, use_statements=None):
        """Extract all use statements"""
        if use_statements is None:
            use_statements = []

        if node.type == 'namespace_use_declaration':
            text = self.get_node_text(node)
            use_statements.append(text)

        for child in node.children:
            self.extract_use_statements(child, use_statements)

        return use_statements

    def parse_use_statement(self, use_text):
        """Parse use statement to extract namespaces"""
        use_text = use_text.strip()
        use_text = re.sub(r'^use\s+', '', use_text)
        use_text = re.sub(r';$', '', use_text)

        namespaces = []

        if '{' in use_text and '}' in use_text:
            match = re.match(r'(.+?)\{(.+?)\}', use_text)
            if match:
                base = match.group(1).strip().rstrip('\\')
                items = match.group(2).split(',')
                for item in items:
                    item = item.strip()
                    item = re.sub(r'\s+as\s+.*$', '', item)
                    namespaces.append(f"{base}\\{item}")
        else:
            use_text = re.sub(r'\s+as\s+.*$', '', use_text)
            namespaces.append(use_text.strip())

        return namespaces

    def namespace_to_file(self, namespace):
        """Convert namespace to file path"""
        namespace = namespace.lstrip('\\')
        path = namespace.replace('\\', '/')

        parts = path.split('/')
        if parts[0] == 'App':
            parts[0] = 'app'
        elif parts[0] == 'Database':
            parts[0] = 'database'
        elif parts[0] == 'Tests':
            parts[0] = 'tests'

        path = '/'.join(parts)
        if not path.endswith('.php'):
            path += '.php'

        full_path = self.project_root / path
        return full_path if full_path.exists() else None

    def parse_and_save_file(self, file_path):
        """Parse PHP file and save AST"""
        file_path = Path(file_path)
        relative_path = str(file_path.relative_to(self.project_root))

        if str(file_path) in self.processed_files:
            return

        if self.ast_exists(file_path):
            print(f"[SKIP] AST exists: {relative_path}")
            self.processed_files.add(str(file_path))

            # Load and process dependencies
            ast_file = self.get_ast_file_path(file_path)
            with open(ast_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                dependencies = data.get('dependencies', [])

                self.dependency_tree[relative_path] = {
                    'dependencies': dependencies,
                    'ast_file': str(ast_file.relative_to(Path(__file__).parent))
                }

                for namespace in dependencies:
                    dep_file = self.namespace_to_file(namespace)
                    if dep_file and dep_file.exists():
                        if str(dep_file) not in self.processed_files:
                            self.files_to_process.append(dep_file)
            return

        print(f"[PARSE] {relative_path}")

        try:
            with open(file_path, 'rb') as f:
                source_code = f.read()

            tree = self.parser.parse(source_code)
            root_node = tree.root_node

            # Extract use statements
            use_statements = self.extract_use_statements(root_node)
            dependencies = []
            for use_text in use_statements:
                namespaces = self.parse_use_statement(use_text)
                dependencies.extend(namespaces)

            # Analyze structures for API index
            print(f"    Analyzing structures...")
            self.analyze_structures(root_node, file_path)

            # Build and save AST
            ast_data = {
                'file_path': str(file_path),
                'relative_path': relative_path,
                'file_size': len(source_code),
                'ast': self.node_to_dict(root_node),
                'source_code': source_code.decode('utf-8', errors='replace'),
                'dependencies': dependencies
            }

            ast_file = self.get_ast_file_path(file_path)
            with open(ast_file, 'w', encoding='utf-8') as f:
                json.dump(ast_data, f, indent=2, ensure_ascii=False)

            print(f"    -> Saved: {ast_file.name} ({len(source_code)} bytes)")
            print(f"    -> Dependencies: {len(dependencies)}")

            # Update dependency tree
            self.dependency_tree[relative_path] = {
                'dependencies': dependencies,
                'ast_file': str(ast_file.relative_to(Path(__file__).parent))
            }

            self.processed_files.add(str(file_path))

            # Add dependencies to queue
            for namespace in dependencies:
                dep_file = self.namespace_to_file(namespace)
                if dep_file and dep_file.exists():
                    if str(dep_file) not in self.processed_files:
                        self.files_to_process.append(dep_file)

            # Free memory
            del ast_data
            del tree
            del root_node
            del source_code
            gc.collect()

        except Exception as e:
            print(f"    [ERROR] {e}")

    def process_routes(self):
        """Process routes file and all dependencies"""
        print("=" * 80)
        print("PHP Routes AST Parser v2 - Detailed API Index")
        print("=" * 80)
        print(f"Project root: {self.project_root}")
        print(f"Routes file: {self.routes_file}")
        print(f"AST directory: {self.ast_dir}")
        print("=" * 80)
        print()

        if not self.routes_file.exists():
            print(f"ERROR: Routes file not found: {self.routes_file}")
            return

        self.files_to_process.append(self.routes_file)

        while self.files_to_process:
            file_path = self.files_to_process.pop(0)
            self.parse_and_save_file(file_path)

        print()
        print("=" * 80)
        print(f"Processing completed!")
        print(f"Total files processed: {len(self.processed_files)}")
        print("=" * 80)

    def save_dependency_tree(self):
        """Save dependency tree"""
        tree_file = Path(__file__).parent / 'dependency_tree.json'
        print(f"\nSaving dependency tree: {tree_file.name}")

        with open(tree_file, 'w', encoding='utf-8') as f:
            json.dump(self.dependency_tree, f, indent=2, ensure_ascii=False)

        print(f"  -> {len(self.dependency_tree)} files in tree")

    def save_api_index(self):
        """Save detailed API index"""
        index_file = Path(__file__).parent / 'api_index.json'
        print(f"Saving API index: {index_file.name}")

        stats = {
            'controllers': len(self.api_index['controllers']),
            'requests': len(self.api_index['requests']),
            'resources': len(self.api_index['resources']),
            'models': len(self.api_index['models']),
            'enums': len(self.api_index['enums']),
            'traits': len(self.api_index['traits']),
            'interfaces': len(self.api_index['interfaces'])
        }

        index_data = {
            'statistics': stats,
            'index': self.api_index
        }

        with open(index_file, 'w', encoding='utf-8') as f:
            json.dump(index_data, f, indent=2, ensure_ascii=False)

        print(f"  -> Controllers: {stats['controllers']}")
        print(f"  -> Requests: {stats['requests']}")
        print(f"  -> Resources: {stats['resources']}")
        print(f"  -> Models: {stats['models']}")
        print(f"  -> Enums: {stats['enums']}")
        print(f"  -> Traits: {stats['traits']}")


def main():
    import sys

    script_dir = Path(__file__).parent
    project_root = script_dir.parent

    if len(sys.argv) < 2:
        print("Usage: python parse_routes_v2.py <routes_file>")
        print("Example: python parse_routes_v2.py routes/api.php")
        sys.exit(1)

    routes_file = Path(sys.argv[1])
    if not routes_file.is_absolute():
        routes_file = project_root / routes_file

    parser = DetailedASTParser(project_root, routes_file)
    parser.process_routes()
    parser.save_dependency_tree()
    parser.save_api_index()

    print("\n✓ Done! Next step: use api_index.json to build API documentation")


if __name__ == '__main__':
    main()
