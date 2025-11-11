#!/usr/bin/env python3
"""
AST Dump v2 - Generic tree-sitter to JSON converter
Конвертує будь-який tree-sitter AST в JSON автоматично
"""

from pathlib import Path
from tree_sitter import Language, Parser, Node
import tree_sitter_php
import sys
import json


# Ініціалізація парсера
PHP_LANGUAGE = Language(tree_sitter_php.language_php())
parser = Parser(PHP_LANGUAGE)


def node_to_dict_with_cursor(cursor, code: bytes) -> dict:
    """
    ПОВНА конвертація через TreeCursor - отримує ВСІ ноди (named + unnamed)
    """
    node = cursor.node

    result = {
        'type': node.type,
        'start_line': node.start_point[0] + 1,
        'end_line': node.end_point[0] + 1,
        'start_byte': node.start_byte,
        'end_byte': node.end_byte,
        'is_named': node.is_named,
    }

    # Додати текст
    text = code[node.start_byte:node.end_byte].decode('utf-8', errors='ignore')
    if len(text) <= 500:
        result['text'] = text
    else:
        result['text_preview'] = text[:200] + '...'
        result['text_length'] = len(text)

    # Використовувати cursor для отримання ВСІХ дітей
    if cursor.goto_first_child():
        children = []

        while True:
            child_dict = node_to_dict_with_cursor(cursor, code)

            # Додати field name через node
            field_name = cursor.field_name
            if field_name:
                child_dict['field'] = field_name

            children.append(child_dict)

            if not cursor.goto_next_sibling():
                break

        cursor.goto_parent()
        result['children'] = children

    return result


def node_to_dict(node: Node, code: bytes) -> dict:
    """Wrapper для node_to_dict_with_cursor"""
    cursor = node.walk()
    return node_to_dict_with_cursor(cursor, code)


def extract_specific_nodes(root_dict: dict, node_types: list) -> list:
    """
    Знайти всі ноди певного типу в дереві
    Рекурсивний пошук по dict структурі
    """
    results = []

    if isinstance(root_dict, dict):
        if root_dict.get('type') in node_types:
            results.append(root_dict)

        # Рекурсивно по дітях
        for child in root_dict.get('children', []):
            results.extend(extract_specific_nodes(child, node_types))

        # Рекурсивно по полях
        for field_value in root_dict.get('fields', {}).values():
            results.extend(extract_specific_nodes(field_value, node_types))

    elif isinstance(root_dict, list):
        for item in root_dict:
            results.extend(extract_specific_nodes(item, node_types))

    return results


def process_file(file_path: Path, full_ast: bool = False) -> dict:
    """Обробити один PHP файл - конвертувати AST в JSON"""
    with open(file_path, 'rb') as f:
        code = f.read()

    tree = parser.parse(code)

    # Generic конвертація AST → dict - БЕЗ ЖОДНИХ ФІЛЬТРІВ
    ast_dict = node_to_dict(tree.root_node, code)

    # Просто зберегти AST і все
    return {
        'file': str(file_path),
        'ast': ast_dict
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python dump_ast_v2.py <path_to_laravel_project>")
        sys.exit(1)

    project_path = Path(sys.argv[1])

    if not project_path.exists():
        print(f"Error: {project_path} not found")
        sys.exit(1)

    # Якщо це файл - обробити один файл
    if project_path.is_file():
        print(f"[*] Parsing single file: {project_path}...")
        file_data = process_file(project_path)

        output_file = 'ast_generic.json'
        print(f"[*] Saving FULL AST (pure tree-sitter output)...")
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(file_data, f, indent=2, ensure_ascii=False)

        print(f"\n[+] Done! Output: {output_file}")
        print(f"    File size: {Path(output_file).stat().st_size / 1024:.1f} KB")
        return

    # Інакше - обробити весь Laravel проект
    app_dir = project_path / 'app'
    if not app_dir.exists():
        print(f"Error: app/ directory not found in {project_path}")
        sys.exit(1)

    # Знайти PHP файли
    print(f"[*] Scanning PHP files in {app_dir}...")
    php_files = list(app_dir.rglob('*.php'))

    # Додати routes файли
    routes_dir = project_path / 'routes'
    if routes_dir.exists():
        routes_files = list(routes_dir.glob('*.php'))
        php_files.extend(routes_files)
        print(f"[*] Found {len(routes_files)} routes files")

    print(f"[*] Total PHP files: {len(php_files)}")

    print("[*] Processing files...")
    all_data = []

    for i, php_file in enumerate(php_files, 1):
        if i % 100 == 0:
            print(f"    Processed {i}/{len(php_files)}...")
        try:
            # ПОВНИЙ AST для ВСІХ файлів - чистий tree-sitter вивід
            file_data = process_file(php_file)
            all_data.append(file_data)
        except Exception as e:
            print(f"    Error processing {php_file}: {e}")

    print(f"\n[*] Processed {len(all_data)} files")

    # Зберегти в JSON
    output_file = 'ast_full.json'
    print(f"\n[*] Saving to {output_file}...")
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({'files': all_data}, f, indent=2, ensure_ascii=False)

    print(f"\n[+] Done! Output: {output_file}")


if __name__ == '__main__':
    main()
