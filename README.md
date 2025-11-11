# Laravel API Extractor via AST

Автоматичне витягування OpenAPI документації з Laravel проектів через AST аналіз (tree-sitter-php).

## Особливості

- **Повний AST аналіз** - використовує tree-sitter-php для парсингу PHP коду
- **БЕЗ regex** - чистий AST traversal
- **Витягує з коду:**
  - Routes (GET, POST, PUT, PATCH, DELETE)
  - Request parameters з FormRequest validation rules (типи, required, min/max, enum)
  - Response schemas з JsonResource/Transformer
  - Error responses (abort, throw)

## Файли

- `dump_ast_v2.py` - Головний скрипт для створення повного AST дампу проекту
- `linker_get.py` - Linker для GET endpoints з response schemas
- `linker_full.py` - Повний linker для всіх endpoints
- `parse_routes_from_ast.py` - Простий парсер routes з AST

## Використання

### 1. Створити AST dump Laravel проекту

```bash
python dump_ast_v2.py /path/to/laravel/project
```

Створює `ast_full.json` з повним AST всіх PHP файлів проекту.

### 2. Згенерувати OpenAPI для GET endpoints

```bash
python linker_get.py ast_full.json
```

Створює `openapi_get.json` з OpenAPI специфікацією.

### 3. Згенерувати повний OpenAPI

```bash
python linker_full.py ast_full.json
```

Створює `openapi_full.json` з request/response schemas.

## Вимоги

```bash
pip install tree-sitter tree-sitter-php
```

## Статус

**В розробці**

- ✅ Повний AST dump (named + unnamed nodes)
- ✅ Routes extraction
- ✅ FormRequest parameters (типи, validation, enum)
- ⚠️ Response schemas (частково працює, потребує доробки)

## TODO

- Покращити пошук response schemas (методи типу `listResponse()`)
- Парсити вкладені об'єкти/масиви
- PHPDoc аналіз для типів
- Error responses з exception handlers

## Ліцензія

MIT
