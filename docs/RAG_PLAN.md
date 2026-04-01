# RAG Documentation Tool — Implementation Plan

## Overview

Extend `workbench-mcp` with a Retrieval-Augmented Generation (RAG) layer that lets
the MCP client ask natural-language questions about any indexed project — e.g.
_"What endpoints does the AuthController expose?"_ or _"How does the Angular
LoginComponent call the API?"_ — and get a synthesised answer backed by real
source chunks.

**Embedding model:** `gemini-embedding-exp-03-07` (1536 dims, 8 192-token context,
code-aware, sent to Google API under your existing subscription)  
**Generation model:** `gemini-2.0-flash` (synthesises answers from retrieved chunks)  
**Vector store:** pgvector on your existing PostgreSQL instance (zero new services)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  INDEX PHASE  (run once, then on change)                        │
│                                                                 │
│  Source files (swagger / .cs / .ts / tests / css / csproj etc.) │
│      │                                                          │
│      ▼                                                          │
│  Parsers  ──►  Chunks  ──►  Gemini Embedding API               │
│                                  │                              │
│                                  ▼                              │
│                         pgvector  rag.documents                 │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  QUERY PHASE  (every MCP tool call)                             │
│                                                                 │
│  User query string                                              │
│      │                                                          │
│      ▼                                                          │
│  Gemini Embedding API  ──►  query vector                        │
│      │                                                          │
│      ▼                                                          │
│  pgvector cosine search  ──►  top-k chunks                      │
│      │                                                          │
│      ▼                                                          │
│  Gemini Flash  ──►  synthesised answer + cited sources          │
└─────────────────────────────────────────────────────────────────┘
```

---

## Architecture Options for VS Code Chat Integration

This section defines the two implementation styles we discussed for MCP + RAG.
Both are valid. The difference is **where the final natural-language answer is
generated**.

### Option A — MCP-Orchestrated Full RAG (Server returns final answer)

In this model, the MCP server performs **all** steps:

1. Embed query
2. Retrieve nearest chunks from `rag.documents`
3. Call generation model (Gemini Flash)
4. Return a ready-to-display text answer + citations

```text
VS Code Chat -> MCP tool `answer_docs(...)`
                 -> embed query
                 -> vector search
                 -> LLM synthesis
                 -> return answer + sources
```

#### Suggested MCP tool contract

```json
{
  "answer": "The AuthController exposes POST /api/auth/login and POST /api/auth/refresh-token...",
  "sources": [
    {"source": "POST /api/auth/login", "score": 0.89},
    {"source": "POST /api/auth/refresh-token", "score": 0.85}
  ],
  "chunks_used": 6,
  "provider": "gemini",
  "model": "gemini-2.0-flash"
}
```

#### Pros

- Consistent response format controlled server-side
- Easy to enforce policy/guardrails in one place
- Clients receive a final answer without prompt engineering

#### Cons

- Higher server complexity and cloud coupling
- MCP now requires LLM provider credentials and usage budget
- Harder to swap generation behavior per client

---

### Option B — Retrieval MCP (Server returns evidence, chat synthesizes)

In this model, MCP performs retrieval only:

1. Embed query
2. Retrieve top-k chunks from `rag.documents`
3. Return chunks/scores/metadata
4. VS Code chat model composes final answer from those chunks

```text
VS Code Chat -> MCP tool `search_docs(...)`
                 -> embed query
                 -> vector search
                 -> return chunks + scores
VS Code Chat model -> synthesizes final text answer with citations
```

#### Suggested MCP tool contract

```json
{
  "query": "What endpoints does AuthController expose?",
  "results": [
    {
      "source": "POST /api/auth/login",
      "title": "Auth - Login",
      "content": "HTTP Method and Path: POST /api/auth/login ...",
      "metadata": {"method": "POST", "path": "/api/auth/login", "tags": ["Auth"]},
      "score": 0.89
    },
    {
      "source": "POST /api/auth/refresh-token",
      "title": "Auth - Refresh Token",
      "content": "HTTP Method and Path: POST /api/auth/refresh-token ...",
      "metadata": {"method": "POST", "path": "/api/auth/refresh-token", "tags": ["Auth"]},
      "score": 0.85
    }
  ],
  "count": 2,
  "threshold": 0.65
}
```

#### Pros

- MCP is simpler and cheaper to run
- You can change answer style/model in chat without changing server code
- Easier debugging: retrieval quality is visible directly

#### Cons

- Final wording depends on client model behavior
- If client does not enforce grounding, citation quality may vary

---

### Important Clarification: Embeddings-Only Response Is Not Enough

Returning only vectors (e.g., `[0.013, -0.221, ...]`) is usually not useful for
chat-based QA. A client needs either:

1. **retrieved chunk text** (`content` + `source` + `score`) to produce an answer, or
2. a server endpoint that already returns the final generated answer.

So the practical contract for Option B is **evidence-first retrieval output**,
not raw embeddings.

---

### Recommended Rollout Path

Start with **Option B** and evolve to Option A only if needed:

1. Implement `search_docs` + `list_doc_sources` (retrieval only)
2. Validate quality in this VS Code chat
3. If you need stricter formatting/policy, add `answer_docs` (Option A)

This staged path gives fastest delivery, lowest risk, and clear observability.

---

## Full Repository Coverage Policy ("Document Everything")

Your requirement is to document **all project artifacts**, not only API and UI
runtime code. This includes test files, style files, project manifests, and
dependency declarations.

### Coverage matrix

| File Type | Examples | Parser Strategy | Chunk Strategy | `doc_type` |
|-----------|----------|-----------------|----------------|------------|
| .NET API contracts | `swagger.json`, `openapi.json` | Structured JSON parser | One chunk per endpoint | `endpoint` |
| C# source | `*.cs` | Regex + lightweight syntax scanning | One chunk per class/method | `backend-code` |
| TypeScript source | `*.ts` | TS-aware regex scanner | One chunk per class/function/interface | `frontend-code` |
| Unit/integration tests | `*.spec.ts`, `*.test.ts`, `*Tests.cs` | Test-aware parser (framework detection) | One chunk per test suite/case block | `test` |
| Stylesheets | `*.css`, `*.scss`, `*.sass`, `*.less` | Rule/block extractor | One chunk per selector group/component stylesheet | `style` |
| .NET project files | `*.csproj`, `Directory.Build.props`, `NuGet.Config` | XML parser | One chunk per package/property group | `dependency` |
| JS package manifests | `package.json`, `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock` | JSON/text parser | One chunk per package group (runtime/dev/peer) | `dependency` |
| CI/build/config | `.github/workflows/*.yml`, `docker-compose.yml`, `Dockerfile`, `.editorconfig`, `tsconfig*.json` | YAML/JSON/text parser | One chunk per logical section | `config` |
| Markdown docs | `README.md`, `docs/**/*.md` | Markdown section parser | One chunk per heading section | `documentation` |

### Noise control rules (critical for quality and cost)

Even with full coverage, indexing should avoid low-value noise:

1. Skip generated outputs: `bin/`, `obj/`, `dist/`, `.angular/`, `node_modules/`
2. Skip minified bundles: `*.min.js`, `*.min.css`
3. Skip oversized binary-like files > `RAG_MAX_FILE_BYTES`
4. Treat lockfiles as **summarized chunks** (top dependencies + critical versions)
5. Tag every chunk with `doc_type`, `language`, `path`, and `project` for filtering

### Retrieval behavior with full coverage

Use filters to keep answers precise:

- contract questions → `doc_type in ('endpoint', 'backend-code', 'frontend-code')`
- dependency questions → `doc_type = 'dependency'`
- test behavior questions → `doc_type = 'test'`
- style/UI questions → `doc_type = 'style'`

This prevents test/css/dependency chunks from polluting endpoint answers.

---

## Final File Layout

The following tree shows only the new files/directories to be created.
Everything else under `src/workbench_mcp/` already exists.

```
workbench-mcp/
│
├── pyproject.toml                      ← add new deps (google-genai, tenacity)
├── .env                                ← add new RAG env vars (see Phase 6)
│
├── scripts/
│   └── index_docs.py                   ← CLI: parse → embed → upsert
│
└── src/workbench_mcp/
    ├── config.py                       ← add RagSettings block
    ├── server.py                       ← register_docs_tools(server)
    │
    ├── rag/
    │   ├── __init__.py
    │   ├── embedder.py                 ← Gemini embedding client (rate-limit aware)
    │   ├── store.py                    ← pgvector upsert + cosine search
    │   ├── chunker.py                  ← splits files into semantic chunks
    │   └── parsers/
    │       ├── __init__.py
    │       ├── openapi.py              ← swagger.json → chunks (Step 4, highest ROI)
    │       ├── dotnet.py               ← .cs controller files → chunks (Step 7)
    │       ├── angular.py              ← .ts service/component files → chunks (Step 8)
    │       ├── tests.py                ← test suites/spec files → chunks
    │       ├── styles.py               ← css/scss rule blocks → chunks
    │       ├── manifests.py            ← csproj/package/lockfiles → chunks
    │       └── generic_text.py         ← fallback parser for config/docs text files
    │
    └── tools/
        └── docs.py                     ← MCP tools: search_docs, list_doc_sources
```

---

## Phase 1 — pgvector + `rag` Schema

### 1.1 Install the extension (run once on your PostgreSQL server)

```sql
-- Connect as superuser and run:
CREATE EXTENSION IF NOT EXISTS vector;
```

Verify with:
```sql
SELECT extversion FROM pg_extension WHERE extname = 'vector';
-- Expected: 0.8.x or higher
```

### 1.2 Schema migration

Run this once against your `cptapp_code_first` database (or whichever DB the
workbench-mcp `.env` points to).

```sql
CREATE SCHEMA IF NOT EXISTS rag;

-- Main documents table
CREATE TABLE IF NOT EXISTS rag.documents (
    id          BIGSERIAL PRIMARY KEY,
    project     TEXT        NOT NULL,   -- logical project name, e.g. "api", "frontend"
    doc_type    TEXT        NOT NULL,   -- "endpoint" | "component" | "service" | "model" | "openapi"
    source      TEXT        NOT NULL,   -- origin path/key, e.g. "POST /auth/login" or "auth.service.ts"
    title       TEXT,                   -- human-readable label shown in citations
    content     TEXT        NOT NULL,   -- raw text chunk (ALWAYS kept; used for re-embedding)
    metadata    JSONB,                  -- arbitrary structured data: controller name, tags, etc.
    embedding   VECTOR(1536),           -- gemini-embedding-exp-03-07 output
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

-- HNSW index: fast approximate nearest-neighbour with cosine distance
-- m=16 and ef_construction=64 are safe defaults for < 500 K vectors
CREATE INDEX IF NOT EXISTS rag_embedding_hnsw_idx
    ON rag.documents USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- B-tree index for cheap project/type filtering before vector search
CREATE INDEX IF NOT EXISTS rag_documents_project_doctype_idx
    ON rag.documents (project, doc_type);

-- Unique constraint: one chunk per (project, doc_type, source)
-- Allows ON CONFLICT DO UPDATE for idempotent re-indexing
ALTER TABLE rag.documents
    ADD CONSTRAINT rag_documents_unique_source
    UNIQUE (project, doc_type, source);
```

**Why HNSW over IVFFlat?**  
HNSW does not require a training step (`VACUUM` / `CREATE INDEX` without data
being present). You can index rows one-by-one without rebuilding. For < 1 M
vectors on a local PostgreSQL instance it gives excellent query times (< 5 ms).

---

## Phase 2 — `rag/embedder.py`

This module is the **single point of contact** with the Gemini Embedding API.
All rate-limiting, retries, and batching live here so the rest of the codebase
never needs to worry about them.

### Responsibilities

- Accept a list of text strings and return a list of `list[float]` (1536 dims each)
- Respect the Gemini Embedding API's requests-per-minute (RPM) limit
  (currently 1 500 RPM on the free tier / 3 000 RPM on paid)
- Retry on transient errors with exponential back-off using `tenacity`
- Log token usage so you can monitor costs

### Key design decisions

| Decision | Rationale |
|----------|-----------|
| `task_type="RETRIEVAL_DOCUMENT"` for indexing | Gemini differentiates document vs. query embeddings; using the right type improves retrieval quality |
| `task_type="RETRIEVAL_QUERY"` for search | Same reason — query vectors are optimised differently |
| Batch size = 100 | Gemini supports up to 100 texts per `batch_embed_contents` call |
| Max retries = 5, wait = exponential 1 s → 60 s | Handles rate-limit spikes without hammering the API |

### Skeleton

```python
# src/workbench_mcp/rag/embedder.py
"""Gemini embedding client with rate-limiting, batching, and retry."""
from __future__ import annotations

import logging
import time
from typing import Literal

import google.generativeai as genai
from tenacity import retry, stop_after_attempt, wait_exponential

from workbench_mcp.config import get_settings

LOGGER = logging.getLogger(__name__)
TaskType = Literal["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]

_BATCH_SIZE = 100


def _get_client() -> genai.GenerativeModel:
    """Initialise the Gemini SDK once (lazy)."""
    settings = get_settings()
    genai.configure(api_key=settings.gemini_api_key.get_secret_value())
    return genai


@retry(stop=stop_after_attempt(5), wait=wait_exponential(min=1, max=60))
def embed_texts(
    texts: list[str],
    task_type: TaskType = "RETRIEVAL_DOCUMENT",
) -> list[list[float]]:
    """Return one embedding vector per input text.

    Processes in batches of 100 to stay within API limits.
    Uses task_type='RETRIEVAL_DOCUMENT' when indexing,
    and task_type='RETRIEVAL_QUERY' when searching.
    """
    settings = get_settings()
    model_name = settings.gemini_embedding_model
    client = _get_client()
    results: list[list[float]] = []

    for batch_start in range(0, len(texts), _BATCH_SIZE):
        batch = texts[batch_start : batch_start + _BATCH_SIZE]
        LOGGER.debug("Embedding batch %d texts with %s", len(batch), model_name)
        response = client.embed_content(
            model=model_name,
            content=batch,
            task_type=task_type,
        )
        results.extend(e["values"] for e in response["embedding"])

    return results
```

---

## Phase 3 — `rag/store.py`

This module owns all SQL that touches the `rag` schema. It uses the existing
`DatabaseClient` connection pattern (psycopg, autocommit, timeout via
`set_config`).

### Responsibilities

- **Upsert** a document chunk (insert or update on conflict)
- **Cosine search** given a query vector and optional filters
- **List sources** for the `list_doc_sources` MCP tool
- **Delete by project** for full re-indexing runs

### Key SQL patterns

**Upsert** — idempotent, safe to call on every index run:
```sql
INSERT INTO rag.documents
    (project, doc_type, source, title, content, metadata, embedding, updated_at)
VALUES
    (%s, %s, %s, %s, %s, %s::jsonb, %s::vector, now())
ON CONFLICT (project, doc_type, source)
DO UPDATE SET
    title      = EXCLUDED.title,
    content    = EXCLUDED.content,
    metadata   = EXCLUDED.metadata,
    embedding  = EXCLUDED.embedding,
    updated_at = now();
```

**Cosine search** with optional project/type filter:
```sql
SELECT
    id, project, doc_type, source, title, content, metadata,
    1 - (embedding <=> %s::vector) AS similarity
FROM rag.documents
WHERE (%s::text IS NULL OR project  = %s::text)
  AND (%s::text IS NULL OR doc_type = %s::text)
  AND 1 - (embedding <=> %s::vector) >= %s::float
ORDER BY embedding <=> %s::vector
LIMIT %s::integer;
```

**List sources** summary:
```sql
SELECT project, doc_type, COUNT(*) AS chunks, MAX(updated_at) AS last_indexed
FROM rag.documents
WHERE (%s::text IS NULL OR project = %s::text)
GROUP BY project, doc_type
ORDER BY project, doc_type;
```

---

## Phase 4 — `rag/parsers/openapi.py`

The OpenAPI parser is the **highest-return first step**: your .NET 8 API already
generates a `swagger.json` (or `openapi.json`) that contains every endpoint,
HTTP method, route, parameters, request body schema, and response schemas.
No AST parsing, no language knowledge required.

### Input

A `swagger.json` / `openapi.json` file conforming to OpenAPI 3.x or Swagger 2.x.
Typical location after `dotnet run` or `dotnet publish`:
- `wwwroot/swagger/v1/swagger.json`
- Or fetched live from `https://your-api/swagger/v1/swagger.json`

### Output (one chunk per endpoint)

```python
@dataclass
class DocumentChunk:
    project:   str          # e.g. "api"
    doc_type:  str          # "endpoint"
    source:    str          # e.g. "POST /auth/login"
    title:     str          # e.g. "AuthController — Login"
    content:   str          # full natural-language description (fed to Gemini)
    metadata:  dict         # { "tags": [...], "operationId": "...", "method": "POST", ... }
```

### Chunking strategy for an endpoint

The content string assembled for each endpoint:
```
HTTP Method and Path:
  POST /api/auth/login

Summary:
  Authenticates a user and returns a JWT token pair.

Description:
  Validates credentials against the identity database. On success returns
  an access token (15 min TTL) and a refresh token (7 day TTL).

Tags: Authentication

Parameters:
  (none)

Request Body (application/json):
  {
    "email":    string  (required) — User's email address
    "password": string  (required) — Plain-text password (HTTPS only)
  }

Responses:
  200 OK — LoginResponse
    {
      "accessToken":  string — JWT access token
      "refreshToken": string — Opaque refresh token
      "expiresIn":    integer — Seconds until access token expiry
    }
  401 Unauthorized — ErrorResponse
    { "message": string }
  422 Unprocessable Entity — ValidationProblemDetails
```

This prose format is significantly more retrievable than raw JSON because the
embedding model can match natural-language queries against natural-language
descriptions.

### Algorithm outline

```
1. Load swagger.json
2. Resolve $ref schemas into inline definitions (recursive)
3. For each path in paths:
   For each method in (get, post, put, patch, delete):
     a. Extract: summary, description, tags, operationId, parameters,
        requestBody schema, responses schemas
     b. Render as the prose template above → content string
     c. source = "METHOD /path"  (e.g. "POST /api/auth/login")
     d. title  = "{tags[0]} — {summary}" if tags else operationId
     e. metadata = { method, path, tags, operationId, status_codes }
     f. Yield DocumentChunk
```

---

## Phase 5 — `tools/docs.py` + Generation

### Tool 1 — `search_docs`

```python
@mcp.tool()
def search_docs(
    query: str,
    project: str | None = None,    # "api", "frontend", or None for all projects
    doc_type: str | None = None,   # "endpoint", "component", "service", "model", or None
    limit: int = 8,                # top-k chunks to retrieve (max 20)
) -> dict[str, Any]:
    """Search indexed project documentation using natural language.

    Embeds the query, retrieves the most relevant documentation chunks
    from pgvector, then uses Gemini to synthesise a grounded answer.

    Returns:
        answer:       Gemini-generated response grounded in the retrieved chunks
        sources:      List of source labels cited (e.g. ["POST /auth/login"])
        chunks_used:  Number of chunks fed to the generation prompt
        similarity_scores: Dict of source → cosine similarity score
    """
```

**Generation prompt template:**
```
You are a technical documentation assistant for the following projects: {projects}.

Answer the user's question using ONLY the documentation excerpts provided below.
If the answer cannot be found in the excerpts, say so clearly.
Always cite the source of each fact in the format [source].

--- DOCUMENTATION EXCERPTS ---
[1] Source: POST /api/auth/login
{chunk content}

[2] Source: auth.service.ts — login()
{chunk content}
...
--- END EXCERPTS ---

Question: {query}

Answer:
```

### Tool 2 — `list_doc_sources`

```python
@mcp.tool()
def list_doc_sources(
    project: str | None = None,
) -> dict[str, Any]:
    """List all indexed documentation sources.

    Returns a summary grouped by project and doc_type,
    including chunk counts and last indexed timestamps.
    """
```

Example output:
```json
{
  "sources": [
    { "project": "api",      "doc_type": "endpoint",  "chunks": 87, "last_indexed": "2026-03-31T10:00:00Z" },
    { "project": "frontend", "doc_type": "service",   "chunks": 34, "last_indexed": "2026-03-31T10:05:00Z" },
    { "project": "frontend", "doc_type": "component", "chunks": 61, "last_indexed": "2026-03-31T10:05:00Z" }
  ],
  "total_chunks": 182
}
```

---

## Phase 6 — Configuration

### New env vars to add to `.env`

```ini
# ── RAG / Gemini ──────────────────────────────────────────────────────────────
# Your Gemini API key (same one used for generation if applicable)
GEMINI_API_KEY=your-gemini-api-key-here

# Embedding model — update this when Google releases a newer stable version;
# then run: python scripts/index_docs.py --re-embed-all
GEMINI_EMBEDDING_MODEL=gemini-embedding-exp-03-07

# Must match the output dimension of the embedding model above.
# gemini-embedding-exp-03-07 → 1536
# If you change models, you MUST also: ALTER TABLE rag.documents ALTER COLUMN embedding TYPE VECTOR(new_dim)
GEMINI_EMBEDDING_DIMS=1536

# Generation model used to synthesise answers from retrieved chunks
GEMINI_GENERATION_MODEL=gemini-2.0-flash

# Cosine similarity threshold (0.0–1.0).
# Chunks scoring below this are discarded before generation.
# Raise to 0.75 for stricter relevance; lower to 0.55 for broader recall.
RAG_SIMILARITY_THRESHOLD=0.65

# Maximum chunks fed to the generation prompt per search_docs call.
# Higher = more context but higher token cost.
RAG_DEFAULT_LIMIT=8
```

### New fields in `config.py` (`Settings` class)

```python
# RAG / Gemini — all optional so existing users are unaffected
gemini_api_key: SecretStr | None = None
gemini_embedding_model: str = "gemini-embedding-exp-03-07"
gemini_embedding_dims: int = 1536
gemini_generation_model: str = "gemini-2.0-flash"
rag_similarity_threshold: float = 0.65
rag_default_limit: int = 8
```

---

## Phase 7 — `rag/parsers/dotnet.py`

Parses `.cs` controller files to extract individual action methods.

### Target patterns

```csharp
// XML doc comment → goes into content
/// <summary>Retrieves a paginated list of sales.</summary>
/// <param name="storeId">Target store identifier.</param>
/// <returns>Paginated list of SaleDto</returns>

[HttpGet("stores/{storeId}/sales")]
[Authorize(Roles = "Admin,Manager")]
public async Task<ActionResult<PagedResult<SaleDto>>> GetSales(
    [FromRoute] long storeId,
    [FromQuery] SalesQueryParams query)
```

### Extraction strategy

1. Use regex (no full C# parser needed) to find:
   - `[Http{Method}("{route}")]` attributes → HTTP method and relative route
   - XML `<summary>`, `<param>`, `<returns>` → description text
   - `[Authorize(Roles = "...")]` → permission requirements
   - Method signature → parameter types and names
   - Return type → response DTO type name
2. Cross-reference DTO types against model files in the same project directory
   to inline property lists
3. Combine into the same prose template used by the OpenAPI parser

### One chunk per action method

```
Controller: SalesController
HTTP Method and Path: GET /api/stores/{storeId}/sales

Summary:
  Retrieves a paginated list of sales.

Authorization: Roles = Admin, Manager

Parameters:
  storeId  (route)  bigint   — Target store identifier
  page     (query)  int      — Page number (default 1)
  pageSize (query)  int      — Items per page (default 20)

Returns: PagedResult<SaleDto>
  SaleDto properties:
    Id          bigint
    SoldAt      datetime
    Amount      decimal
    CustomerName string
    ProductName  string
```

---

## Phase 8 — `rag/parsers/angular.py`

Parses TypeScript `.ts` files for Angular services and components.

### Service files — one chunk per public method

Target pattern:
```typescript
/**
 * Authenticates user and stores tokens.
 * @param credentials LoginRequest object
 * @returns Observable<LoginResponse>
 */
login(credentials: LoginRequest): Observable<LoginResponse> {
  return this.http.post<LoginResponse>(`${this.baseUrl}/auth/login`, credentials);
}
```

Chunk content:
```
Service: AuthService
File: src/app/core/services/auth.service.ts

Method: login
Description: Authenticates user and stores tokens.

Parameters:
  credentials  LoginRequest

Returns: Observable<LoginResponse>

API Call: POST /auth/login
```

### Component files — one chunk per component

Target pattern:
```typescript
@Component({
  selector: 'app-login',
  templateUrl: './login.component.html',
})
export class LoginComponent {
  // public methods and @Input/@Output bindings
}
```

Chunk content:
```
Component: LoginComponent
Selector: app-login
File: src/app/features/auth/login/login.component.ts

Inputs:  redirectUrl: string
Outputs: loginSuccess: EventEmitter<User>

Public Methods:
  onSubmit() — Validates form and calls AuthService.login()
  onForgotPassword() — Navigates to password reset page
```

### TypeScript interfaces/DTOs — one chunk per interface

```typescript
export interface LoginRequest {
  email: string;
  password: string;
}
```

Chunk content:
```
Interface: LoginRequest
File: src/app/core/models/auth.model.ts

Properties:
  email     string  (required)
  password  string  (required)
```

---

## Phase 8B — `rag/parsers/tests.py` (Test files)

Capture behavior and edge cases from tests:

- Angular/Jest/Vitest: `*.spec.ts`, `*.test.ts`
- .NET: `*Tests.cs`, `*.IntegrationTests.cs`

Chunk format (one chunk per describe/class block):

```
Test Suite: AuthService Login
Framework: Jasmine/Karma
File: src/app/core/services/auth.service.spec.ts

Scenarios:
  - should call POST /auth/login with credentials
  - should map 401 to invalidCredentials error state
  - should persist tokens to local storage
```

Use metadata tags: `{ "doc_type": "test", "framework": "jasmine", "area": "auth" }`.

---

## Phase 8C — `rag/parsers/styles.py` (CSS/SCSS)

Index stylesheet intent for UI/support questions:

- `*.css`, `*.scss`, `*.sass`, `*.less`

Chunk format (one chunk per selector/component style block):

```
Stylesheet: login.component.scss
Selectors:
  .login-form, .login-form__error, .login-form__submit

Rules Summary:
  - Uses CSS variables for theme colors
  - Error state uses high-contrast red token
  - Submit button disabled style reduces opacity
```

Store concise summaries; avoid embedding full huge generated CSS.

---

## Phase 8D — `rag/parsers/manifests.py` (Dependencies and project manifests)

Index dependency and build metadata from:

- .NET: `*.csproj`, `Directory.Build.props`, `NuGet.Config`
- Node: `package.json`, lockfiles (`package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`)

Chunk format:

```
Manifest: api/MyApi.csproj
TargetFramework: net8.0
PackageReferences:
  - MediatR 12.2.0
  - FluentValidation.AspNetCore 11.3.0
  - Swashbuckle.AspNetCore 6.6.2
```

For lockfiles, produce summarized chunks (top direct dependencies + critical transitive pins)
to control token usage.

---

## Phase 9 — `scripts/index_docs.py` CLI

The indexing script ties everything together. It is run **outside** the MCP
server (directly in a terminal) whenever source files change.

### Usage

```bash
# Index your .NET API from its swagger.json (fastest, recommended first)
python scripts/index_docs.py \
    --project api \
    --type openapi \
    --path ../my-dotnet-api/wwwroot/swagger/v1/swagger.json

# Index your .NET API from .cs source files
python scripts/index_docs.py \
    --project api \
    --type dotnet \
    --path ../my-dotnet-api/src

# Index your Angular frontend
python scripts/index_docs.py \
    --project frontend \
    --type angular \
    --path ../my-angular-app/src

# Index all supported file types in one pass (recommended after parser rollout)
python scripts/index_docs.py \
  --project frontend \
  --type all \
  --path ../my-angular-app

# Index dependency manifests only (csproj/package/lockfiles)
python scripts/index_docs.py \
  --project api \
  --type manifests \
  --path ../my-dotnet-api

# Re-embed everything (use after a model change/deprecation)
# Reads existing content from DB, calls new embedding model, updates vectors
python scripts/index_docs.py --re-embed-all

# Dry run: print what would be indexed without writing to DB
python scripts/index_docs.py \
    --project api \
    --type openapi \
    --path ../my-api/swagger.json \
    --dry-run

# Delete all chunks for a project and re-index from scratch
python scripts/index_docs.py \
    --project api \
    --type openapi \
    --path ../my-api/swagger.json \
    --force-reindex
```

### Internal flow

```
parse(path, type)
    → list[DocumentChunk]          (titles, content, metadata)
        → embed_texts([c.content]) (Gemini API, batched, retried)
            → store.upsert_many()  (ON CONFLICT DO UPDATE)
                → print summary: N chunks indexed, M updated, elapsed Xs
```

---

## New Dependencies

Add to `pyproject.toml` under `dependencies`:

```toml
"google-generativeai>=0.8.0",   # Gemini Embedding + Generation SDK
"tenacity>=8.2.0",               # retry with exponential back-off
```

Install locally:
```bash
pip install "google-generativeai>=0.8.0" "tenacity>=8.2.0"
```

Or re-install the package in editable mode:
```bash
pip install -e .
```

---

## Re-embedding Safety Procedure

When Google deprecates `gemini-embedding-exp-03-07` and releases a new model:

1. Update `.env`: `GEMINI_EMBEDDING_MODEL=gemini-embedding-exp-XXXX`
2. Update `.env`: `GEMINI_EMBEDDING_DIMS=YYYY` (if dimensions changed)
3. If dimensions changed, run:
   ```sql
   ALTER TABLE rag.documents DROP COLUMN embedding;
   ALTER TABLE rag.documents ADD COLUMN embedding VECTOR(YYYY);
   DROP INDEX rag_embedding_hnsw_idx;
   CREATE INDEX rag_embedding_hnsw_idx
       ON rag.documents USING hnsw (embedding vector_cosine_ops)
       WITH (m = 16, ef_construction = 64);
   ```
4. Run: `python scripts/index_docs.py --re-embed-all`
   - This reads all existing `content` rows (raw text, always preserved)
   - Re-calls the new embedding model in batches of 100
   - Updates only the `embedding` column and `updated_at`
5. No source files need to be re-parsed. No data is lost.

**Cost estimate for re-embedding 10 000 chunks (average 500 tokens each):**  
10 000 × 500 = 5 M tokens → ~$0.005 at current Gemini pricing. Negligible.

---

## Execution Order (Prioritised)

| Step | Task | New files | Effort |
|------|------|-----------|--------|
| **1** | Install pgvector + run `rag` schema migration | SQL only | 30 min |
| **2** | `embedder.py` — Gemini embedding client | `rag/embedder.py` | 2 h |
| **3** | `store.py` — pgvector upsert + cosine search | `rag/store.py` | 2 h |
| **4** | `openapi.py` parser (no AST, highest ROI) | `rag/parsers/openapi.py` | 3 h |
| **5** | `docs.py` MCP tools + Gemini generation | `tools/docs.py` | 2 h |
| **6** | `index_docs.py` CLI + `chunker.py` | `scripts/`, `rag/chunker.py` | 2 h |
| **7** | `config.py` + `pyproject.toml` updates | existing files | 30 min |
| **8** | `dotnet.py` controller parser | `rag/parsers/dotnet.py` | 3 h |
| **9** | `angular.py` service/component parser | `rag/parsers/angular.py` | 3 h |
| **10** | `tests.py` parser for unit/integration tests | `rag/parsers/tests.py` | 2 h |
| **11** | `styles.py` parser for css/scss files | `rag/parsers/styles.py` | 2 h |
| **12** | `manifests.py` parser for csproj/package/lockfiles | `rag/parsers/manifests.py` | 2 h |
| **13** | `generic_text.py` fallback parser for config/docs | `rag/parsers/generic_text.py` | 1.5 h |

Steps **1–7** deliver a fully working RAG tool backed by your `swagger.json`
before writing a single AST parser. Steps 8–13 expand into full-repository
awareness (code + tests + styles + dependencies + configs).

---

## Acceptance Criteria

A successful implementation should pass these manual tests:

```
search_docs("What does the login endpoint expect as a request body?")
→ Answer cites POST /auth/login, describes email+password fields

search_docs("Which endpoints require Admin role?", project="api")
→ Answer lists all endpoints with [Authorize(Roles="Admin")] (after Phase 8)

search_docs("How does the Angular app call the sales API?", project="frontend")
→ Answer describes SalesService.getSales() and the HTTP call (after Phase 9)

search_docs("Which tests validate login 401 handling?", project="frontend", doc_type="test")
→ Answer cites `*.spec.ts` test suites and expected behavior (after Phase 10)

search_docs("Which NuGet and npm packages power authentication?", doc_type="dependency")
→ Answer cites `.csproj` and `package.json`/lockfile chunks (after Phase 12)

search_docs("Where are login error styles defined?", project="frontend", doc_type="style")
→ Answer cites `login.component.scss` selectors and rule summary (after Phase 11)

list_doc_sources()
→ Returns table of projects, doc_types, chunk counts, last indexed timestamps

search_docs("What is the capital of France?")
→ Answer: "This information is not found in the indexed documentation."
  (grounding check — Gemini must not hallucinate outside provided chunks)
```
