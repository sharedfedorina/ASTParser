# Laravel API Extraction Results

## Query API Implementation - linker_query_correct.py

**Using EXCLUSIVELY tree-sitter Query API** as documented in Tree-sitter-Python.pdf and research.md

### Extraction Results (invoiceninja)

- **Total GET routes found:** 132
- **Response coverage:** 39/132 (29%)
- **Request parameters coverage:** 43/132 (32%)
  - **Path parameters:** 51
  - **Query parameters:** 16 (from FormRequest validation rules)

### Sample Extracted Data

#### Path Parameters
```
gocardless/oauth/connect/{token}
  - token (path): string, required=True

invoices/{invoice}/delivery_note
  - invoice (path): string, required=True

quote/{invitation_key}/download
  - invitation_key (path): string, required=True
```

#### Query Parameters (from FormRequest)
```
quickbooks/authorized
  - code (query): string, required=True
  - state (query): string, required=True
  - realmId (query): string, required=True

token_hash_router
  - hash (query): string, required=True
```

#### Response Schemas
```
password/reset/{token}
  Response properties: 4
    - root: string (required)
    - token: string (required)
    - account: integer (required)
    - email: string (required)

client/login/{company_key?}
  Response properties: 2
    - account: integer (required)
    - company: string (required)
```

### Implementation Details

All extraction using **Query API only**:

1. **Route extraction** - `Query(PHP_LANGUAGE, "(scoped_call_expression scope: (name) @scope...)")`
2. **Controller classes** - `Query(PHP_LANGUAGE, "(class_declaration name: (name) @class_name) @class")`
3. **Methods** - `Query(PHP_LANGUAGE, "(method_declaration name: (name) @method_name) @method")`
4. **FormRequest parameters** - `Query(PHP_LANGUAGE, "(simple_parameter type: (named_type) @param_type...)")`
5. **Validation rules** - `Query(PHP_LANGUAGE, "(return_statement) @return")` → `Query(PHP_LANGUAGE, "(array_creation_expression) @array")`
6. **Response arrays** - Query API with `QueryCursor` pattern matching

### Output File

`openapi_query.json` - Complete OpenAPI 3.0.3 specification with:
- All GET endpoints
- Request parameters (path + query)
- Response schemas with proper types
- Required/optional field indicators
