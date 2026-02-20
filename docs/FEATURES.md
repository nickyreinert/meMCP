## Plan: Add Features to Personal MCP Server

This plan addresses all 6 requests by modifying `db/models.py`, `app/main.py`, and `llm/enricher.py` to add text shrinking for LLM efficiency, enhanced search fields, comprehensive tag/skill endpoints, and a new graph visualization endpoint.

**Steps**

1.  **Implement Text Shrinking (`llm/enricher.py`)**
    *   Add `STOP_WORDS` constant (set of ~75 common English words).
    *   Add `_shrink_text(text: str, n_skip: int)` helper method to `LLMEnricher` class.
    *   Logic: Filter stop words first, then remove every $n$-th character (preserving readability while reducing tokens).
    *   Update `enrich_description` to use `_shrink_text` before sending to LLM.

2.  **Enhance Entity Queries (`db/models.py`)**
    *   Update `query_stages` to accept optional `tag` (generic), `skill`, and `technology` arguments.
    *   Update `query_oeuvre` to accept the same arguments.
    *   Modify SQL in these functions to `JOIN tags` when filters are present.

3.  **Update Routes for Filtering (`app/main.py`)**
    *   Update `GET /stages` and `GET /oeuvre` to accept query parameters: `?tag=`, `?skill=`, `?technology=`.
    *   Pass these parameters to the updated DB functions.

4.  **Add Tag Detail Endpoint (`db/models.py`, `app/main.py`)**
    *   Add `query_tag_detail(conn, tag_name)` to `db/models.py` (fetching all entities with that specific Generic tag).
    *   Add route `GET /tags/{tag}` in `app/main.py`.

5.  **Add Global Search (`db/models.py`, `app/main.py`)**
    *   Modify `list_entities` in `db/models.py` to include `OR tags.tag LIKE ?` in the search condition (requires joining tags if `search` param is present).
    *   Ensure `GET /search` in `app/main.py` uses this updated logic.

6.  **Implement Graph Endpoint (`db/models.py`, `app/main.py`)**
    *   Add `query_graph(conn)` to `db/models.py`.
    *   Logic: Fetch all **Entities** (nodes) and all **Tags** (nodes).
    *   Build links: Entity ID $\leftrightarrow$ Tag ID.
    *   Add route `GET /graph` in `app/main.py` serving a JSON structure: `{ "nodes": [...], "links": [...] }`.

**Verification**
*   **Text Shrinking:** Run existing `ingest.py` (or a small script) and inspect logs to see reduced prompt size.
*   **Search/Filtering:** Call `/stages?technology=Python` and verify response contains only Python-related stages.
*   **Graph:** Call `/graph` and check if JSON output is valid node-link format.
*   **Global Search:** Call `/search?q=Python` and ensure it finds entities that *only* have "Python" as a tag (even if not in title/desc).

**Decisions**
*   **Text Shrinking:** Chose "skip every n-th char" (decimation) over "first/last n" as it preserves the *structure* and *keywords* throughout the document, which is better for LLM feature extraction.
*   **Search:** Built upon standard SQL `LIKE` rather than FTS5 for simplicity and portability within the existing `sqlite3` setup.