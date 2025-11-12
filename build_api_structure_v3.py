#!/usr/bin/env python3
"""
API Structure Builder v3 - Modular version with bug fixes
Builds API structure with nested support using separate modules
"""

import json
from pathlib import Path

# Import modules
from utils.path_utils import PathUtils
from parsers.ast_reader import ASTReader
from parsers.rules_extractor import RulesExtractor
from parsers.nested_builder import NestedSchemaBuilder
from parsers.trait_rules import TraitRules


class APIStructureBuilderV3:
    """Build API structure from api_index.json with nested structures support"""

    def __init__(self, api_index_file):
        self.api_index_file = Path(api_index_file)
        self.ast_dir = self.api_index_file.parent / 'AST'
        self.api_index = None
        self.api_structure = {
            'endpoints': []
        }

    def load_api_index(self):
        """Load api_index.json"""
        print(f"Loading API index: {self.api_index_file}")
        with open(self.api_index_file, 'r', encoding='utf-8') as f:
            self.api_index = json.load(f)

        stats = self.api_index.get('statistics', {})
        print(f"  Controllers: {stats.get('controllers', 0)}")
        print(f"  Requests: {stats.get('requests', 0)}")
        print(f"  Models: {stats.get('models', 0)}")
        print()

    def parse_validation_rules_from_ast(self, request_class_name):
        """
        Parse validation rules from Request class AST

        Extracts:
        - Rules from rules() method (including array_merge support)
        - Rules from traits (e.g., HasPaginationAttributes)

        Args:
            request_class_name: Name of Request class

        Returns:
            dict: {field_name: validation_rule}
        """
        if not request_class_name:
            return {}

        requests = self.api_index['index'].get('requests', {})
        if request_class_name not in requests:
            return {}

        request_info = requests[request_class_name]

        # FIX: Normalize path (handles both AST/ and AST\\ prefixes)
        ast_file_path = PathUtils.normalize_ast_file_path(
            self.ast_dir,
            request_info.get('ast_file', '')
        )

        if not ast_file_path.exists():
            print(f"      [WARNING] AST file not found: {ast_file_path}")
            return {}

        print(f"      Reading AST: {ast_file_path.name}")

        # Load AST file
        ast_data = ASTReader.load_ast_file(ast_file_path)
        if not ast_data:
            return {}

        # Find class_declaration in AST
        class_node = ASTReader.find_node_by_type(ast_data['ast'], 'class_declaration')

        # Extract trait rules
        trait_rules = {}
        if class_node:
            trait_rules = TraitRules.extract_all_trait_rules(class_node, ASTReader)
            if trait_rules:
                print(f"      Found {len(trait_rules)} rules from traits")

        # Find rules() method in AST
        rules_method = ASTReader.find_method_by_name(ast_data['ast'], 'rules')

        if not rules_method:
            print(f"      [WARNING] rules() method not found in AST")
            # Return only trait rules if no rules() method
            return trait_rules

        # Extract rules from method (supports array_merge)
        rules_dict = RulesExtractor.extract_rules_from_method(rules_method, ASTReader)

        print(f"      Found {len(rules_dict)} validation rules from rules() method")

        # Merge trait rules with method rules (method rules override trait rules)
        combined_rules = {**trait_rules, **rules_dict}

        print(f"      Total rules: {len(combined_rules)}")

        return combined_rules

    def build_get_endpoints(self):
        """Build GET endpoints from controllers"""
        print("=" * 80)
        print("Building GET endpoints with nested structures")
        print("=" * 80)
        print()

        controllers = self.api_index['index'].get('controllers', {})

        for controller_name, controller_info in controllers.items():
            print(f"Processing: {controller_name}")

            # Generate path from controller name
            base_path = PathUtils.controller_to_path(controller_name)
            print(f"  Base path: /{base_path}")

            methods = controller_info.get('methods', {})

            # Process index method (GET /resource)
            if 'index' in methods:
                method_info = methods['index']
                endpoint = self.build_index_endpoint(
                    controller_name,
                    base_path,
                    method_info
                )
                self.api_structure['endpoints'].append(endpoint)
                print(f"    ✓ GET /{base_path}")

            # Process show method (GET /resource/{id})
            if 'show' in methods:
                method_info = methods['show']
                endpoint = self.build_show_endpoint(
                    controller_name,
                    base_path,
                    method_info
                )
                self.api_structure['endpoints'].append(endpoint)
                print(f"    ✓ GET /{base_path}/{{id}}")

            print()

    def build_index_endpoint(self, controller_name, base_path, method_info):
        """Build index endpoint with nested structure support"""
        # Find Request class from parameters
        request_class = None
        for param in method_info.get('parameters', []):
            param_type = param.get('type')
            if param_type and param_type != 'Request':
                request_class = param_type
                break

        # Parse validation rules from AST
        rules_dict = {}
        if request_class:
            print(f"      Request class: {request_class}")
            rules_dict = self.parse_validation_rules_from_ast(request_class)

        # Build nested schema from rules with enum resolution
        schema_builder = NestedSchemaBuilder(api_index=self.api_index)
        query_params_schema = schema_builder.build_schema(rules_dict)

        endpoint = {
            'path': f'/api/{base_path}',
            'method': 'GET',
            'controller': controller_name,
            'action': 'index',
            'description': f'List all {base_path}',
            'request': {
                'query_parameters': query_params_schema
            }
        }

        if request_class:
            endpoint['request']['request_class'] = request_class

        return endpoint

    def build_show_endpoint(self, controller_name, base_path, method_info):
        """Build show endpoint"""
        endpoint = {
            'path': f'/api/{base_path}/{{id}}',
            'method': 'GET',
            'controller': controller_name,
            'action': 'show',
            'description': f'Get single {base_path} by ID',
            'request': {
                'path_parameters': {
                    'id': {
                        'type': 'integer',
                        'required': True,
                        'in': 'path',
                        'description': f'{base_path} ID'
                    }
                }
            }
        }

        return endpoint

    def save_api_structure(self, output_file):
        """Save API structure to JSON"""
        print("=" * 80)
        print(f"Saving API structure: {output_file.name}")
        print(f"Total GET endpoints: {len(self.api_structure['endpoints'])}")
        print("=" * 80)

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(self.api_structure, f, indent=2, ensure_ascii=False)

        print(f"\n✓ Saved to: {output_file}")

    def run(self):
        """Main execution"""
        print("=" * 80)
        print("API Structure Builder v3 - Modular with Bug Fixes")
        print("=" * 80)
        print()

        self.load_api_index()
        self.build_get_endpoints()

        output_file = self.api_index_file.parent / 'api_structure.json'
        self.save_api_structure(output_file)

        print("\n✓ Done! Features:")
        print("  - Nested structures (objects, arrays, deep nesting)")
        print("  - Validation constraints (min, max, enum, gt, gte, in, etc.)")
        print("  - Enum resolution from api_index (Rule::enum support)")
        print("  - array_merge() support in rules() method")
        print("  - Trait rules support (HasPaginationAttributes, etc.)")
        print("  - Fixed Windows path bug (AST\\ handling)")
        print("  - Modular architecture (parsers + utils)")


def main():
    script_dir = Path(__file__).parent
    api_index_file = script_dir / 'api_index.json'

    if not api_index_file.exists():
        print(f"ERROR: API index not found: {api_index_file}")
        print("Run parse_routes_v2.py first to generate api_index.json")
        return

    builder = APIStructureBuilderV3(api_index_file)
    builder.run()


if __name__ == '__main__':
    main()
