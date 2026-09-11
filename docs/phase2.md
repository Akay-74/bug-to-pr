# Phase 2 — Repository Localization

## Objective

Implement the localization stage of Bug-to-PR Agent.

Input:
- Curated benchmark issue ID
- Repository at its exact `base_commit`
- Issue/problem statement

Output:
- Ranked Top-5 code locations with file, symbol, line range, scores, and retrieval evidence

Do not implement fix generation, verification, retries, model escalation, or PR creation in this phase.

## 1. Repository Indexing

Implement a deterministic indexer for the allowlisted benchmark repositories.

For each repository/base commit:

1. Check out the exact `base_commit` into an isolated workspace.
2. Enumerate relevant Python source files.
3. Exclude:
   - `.git/`
   - `__pycache__/`
   - build/dist artifacts
   - virtual environments
   - generated/binary files
   - unrelated frontend/node directories
4. Parse Python files with lightweight Python AST tooling only.
5. Extract symbols:
   - functions
   - methods
   - classes
6. Create retrieval chunks around symbols.
7. Preserve metadata for every chunk:
   - repository
   - commit hash
   - file path
   - symbol type
   - symbol name
   - start line
   - end line
8. For unusually large symbols, split into overlapping chunks.
9. Target approximately 200–800 tokens per chunk where practical.
10. Keep enough local context (imports/class context/nearby definitions) to make the chunk semantically useful.

Do not build a dependency graph or caller/callee graph.

## 2. Lexical Retrieval

Implement lexical search using `ripgrep`.

Search using:
- issue title/problem statement terms
- exception names
- function/class names
- identifiers
- quoted error messages
- distinctive technical phrases

Return matching chunks/files with a normalized lexical score.

Do not use an LLM for lexical retrieval.

## 3. Semantic Retrieval

Implement local code embeddings using:

`jinaai/jina-code-embeddings-0.5b`

Use the model in natural-language-to-code retrieval mode.

Embed:
- each repository chunk once per repository/base commit
- the issue/problem statement as the query

Cache embeddings deterministically so re-indexing the same repository at the same commit does not recompute them.

Cache layout may use:

`backend/cache/<repo>/<commit>/`

Store:
- embeddings
- chunk metadata
- model identifier/version
- embedding configuration

Use cosine similarity for semantic ranking.

Do not add a vector database.

## 4. Hybrid Ranking

Merge lexical and semantic candidates.

Initial ranking formula:

```text
final_score =
    0.45 * semantic_similarity +
    0.35 * lexical_score +
    0.20 * symbol_file_relevance
```

Where `symbol_file_relevance` rewards strong matches in symbol names, file names, and directly related metadata.

Normalize component scores before combining them.

Return Top-5 unique retrieval locations.

If multiple chunks from the same symbol/file dominate the result, deduplicate them so the Top-5 gives useful coverage.

## 5. Public Interface

Add a localization service that accepts an issue ID and returns structured results.

Expose through FastAPI:

```text
POST /api/localize/{issue_id}
GET  /api/localize/{issue_id}
```

The response must include, for each result:

```json
{
  "rank": 1,
  "file": "...",
  "symbol": "...",
  "symbol_type": "function|method|class",
  "start_line": 1,
  "end_line": 20,
  "semantic_score": 0.0,
  "lexical_score": 0.0,
  "relevance_score": 0.0,
  "final_score": 0.0,
  "matched_terms": []
}
```

## 6. Persistence

Persist localization results for benchmark runs so the same indexing/retrieval work is not repeated unnecessarily.

Reuse the existing PostgreSQL schema where practical. Do not introduce a new database product or vector database.

## 7. Evaluation

Create a deterministic localization evaluation command.

For each validated benchmark issue:

1. Run localization against the exact base commit.
2. Compare Top-1 and Top-3 results against the known regression-test file/symbol location.
3. Record:
   - Top-1 hit/miss
   - Top-3 hit/miss
   - returned locations
   - model identifier
   - index/cache identifier
4. Produce an aggregate report.

Evaluate at least:

- Jina Code Embeddings 0.5B
- CodeRankEmbed

Keep all non-embedding retrieval logic identical between the two experiments.

Do not change the chunking or ranking logic between the two model runs except for the embedding model itself.

## 8. Tests

Add tests for:

- Python symbol extraction
- chunk creation and splitting
- excluded paths
- deterministic cache keys
- embedding cache reuse
- cosine similarity
- lexical retrieval
- hybrid ranking
- deduplication
- API response schema
- localization evaluation

Use a small fixture repository for fast unit tests.

Do not run the full SWE-bench evaluation as part of normal unit tests.

## 9. Performance Requirements

Avoid recomputing repository embeddings on every query.

The expensive steps should be cached:

```text
repo + base_commit
        ↓
index once
        ↓
embedding cache
        ↓
reuse for localization queries
```

Keep the implementation suitable for the current benchmark size of roughly 10–12 repositories/issues.

Do not add FAISS, Chroma, Pinecone, Redis, Celery, or other infrastructure unless an existing implementation requires it and the current simple approach cannot meet the benchmark requirements.

## 10. Security Requirements

Do not weaken the Docker sandbox.

Repository contents must remain isolated during indexing where repository code is not executed.

Do not expose:
- GitHub tokens
- API keys
- database credentials

Do not execute repository code as part of localization.

## 11. Completion Criteria

Phase 2 is complete when:

- symbol-aware indexing works on the validated benchmark set
- Jina semantic retrieval works locally
- ripgrep lexical retrieval works
- hybrid Top-5 ranking works
- results are cached
- localization APIs work
- localization tests pass
- Top-1/Top-3 evaluation is reproducible
- Jina vs CodeRankEmbed evaluation can be run without changing the rest of the retrieval pipeline

Stop after Phase 2. Do not start Phase 3.
