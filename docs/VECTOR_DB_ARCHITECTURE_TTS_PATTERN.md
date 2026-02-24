# Vector Database Architecture - The TTS Pattern

**Date:** 2026-01-02
**Status:** Production Architecture

This document describes the rowid-based vector database pattern used across the system. This pattern has been proven stable in TTS services for months and is now used by both catalog and user_content vector databases.

---

## Core Concept

**The Pair Pattern:** SQLite database (.db) + Annoy index (.ann) work together via SQLite's implicit `rowid`.

**Key Insight:** SQLite's `rowid` is a persistent auto-incrementing integer (starting at 1) that serves as the mapping between Annoy indexes and database records. No separate mapping file or in-memory dict needed.

**The Mapping Formula:**
- `annoy_index = rowid - 1` (when building)
- `rowid = annoy_index + 1` (when searching)

---

## Database Schema Pattern

### Catalog (tracks table)
```sql
CREATE TABLE IF NOT EXISTS tracks (
    track_id TEXT UNIQUE NOT NULL,  -- The UUID string identifier
    metadata_json TEXT NOT NULL,
    created_at TEXT,
    title TEXT,
    ...
)
-- No explicit PRIMARY KEY = uses implicit INTEGER rowid
CREATE UNIQUE INDEX IF NOT EXISTS idx_track_id ON tracks(track_id)
```

### User Content (shoutouts table)
```sql
CREATE TABLE IF NOT EXISTS shoutouts (
    content_id TEXT UNIQUE NOT NULL,  -- The UUID string identifier
    metadata_json TEXT NOT NULL,
    created_at TEXT,
    ...
)
-- No explicit PRIMARY KEY = uses implicit INTEGER rowid
CREATE UNIQUE INDEX IF NOT EXISTS idx_content_id ON shoutouts(content_id)
```

### TTS (broadcast/shoutout tables)
```sql
CREATE TABLE IF NOT EXISTS broadcast (
    filename TEXT,
    title TEXT,
    embedding BLOB,
    voice TEXT
)
-- No explicit PRIMARY KEY = uses implicit INTEGER rowid
```

**Pattern:** No TEXT PRIMARY KEY. Use TEXT UNIQUE NOT NULL for the string identifier. Let SQLite manage the implicit integer rowid.

---

## Build Flow (Index Creation)

### Catalog Vector Database Service
**File:** `server/services/catalog_vector_database_service.py`

```python
def rebuild_indexes(self):
    # Query catalog.db WITH rowid
    conn = sqlite3.connect(str(catalog_svc.db_path))
    c = conn.cursor()
    c.execute("SELECT rowid, track_id, metadata_json FROM tracks ORDER BY rowid")

    for rowid, track_id, metadata_json in c.fetchall():
        track = json.loads(metadata_json)
        category_texts = self._extract_category_texts(track)

        # Build category embeddings (cached in .db files)
        category_embeddings = {...}
        combined_embedding = self._create_weighted_embedding(category_embeddings)

        # TTS Pattern: annoy_index = rowid - 1
        new_index.add_item(rowid - 1, combined_embedding)

    new_index.build(50)
    new_index.save("catalog_1.ann")
```

**Key Points:**
- Query with `ORDER BY rowid` for deterministic ordering
- Extract category texts on-the-fly from metadata_json
- Use `rowid - 1` as the Annoy index
- No in-memory dict needed

### User Content Vector Database Service
**File:** `server/services/user_content_vector_database_service.py`

```python
def rebuild_indexes(self, shoutouts_data: List[Dict[str, Any]] = None):
    # Query user_content.db WITH rowid
    conn = sqlite3.connect(str(self.user_content_service.db_path))
    c = conn.cursor()
    c.execute("SELECT rowid, content_id, metadata_json FROM shoutouts ORDER BY rowid")

    for rowid, content_id, metadata_json in c.fetchall():
        item = json.loads(metadata_json)
        category_texts = self._extract_category_texts(item)

        # Build embeddings
        category_embeddings = {...}
        combined_embedding = self._create_weighted_embedding(category_embeddings)

        # TTS Pattern: annoy_index = rowid - 1
        new_index.add_item(rowid - 1, combined_embedding)
```

**Same pattern as catalog.**

---

## Search Flow (Vector Similarity)

### Catalog Vector Search Service
**File:** `server/services/catalog_vector_search_service.py`

```python
def search_catalog_by_string(query: str, ...) -> List[Dict]:
    # Get nearest neighbors from Annoy (returns indexes: [0, 5, 12, ...])
    nearest_ids = annoy.get_nns_by_vector(query_embedding, top_n * 10)

    # Query catalog.db by rowid
    import sqlite3
    import json
    conn = sqlite3.connect(str(self.catalog.db_path))
    c = conn.cursor()

    results = []
    for annoy_idx in nearest_ids:
        # TTS Pattern: rowid = annoy_index + 1
        c.execute("SELECT track_id, metadata_json FROM tracks WHERE rowid = ?",
                  (annoy_idx + 1,))
        result = c.fetchone()

        if not result:
            continue

        track_id, metadata_json = result
        track = json.loads(metadata_json)

        # Extract category texts for re-ranking
        category_texts = self.vector_db._extract_category_texts(track)

        # Continue with re-ranking logic...
        similarity = calculate_similarity(...)
        results.append({'track_id': track_id, 'similarity': similarity, ...})

    conn.close()
    return results
```

### User Content Vector Search Service
**File:** `server/services/user_content_vector_search_service.py`

```python
def search_user_content_by_string(query: str, ...) -> List[Dict]:
    # Get nearest neighbors from Annoy
    nearest_ids = annoy.get_nns_by_vector(query_embedding, top_n * 10)

    # Query user_content.db by rowid
    import sqlite3
    import json
    conn = sqlite3.connect(str(self.vector_db.user_content_service.db_path))
    c = conn.cursor()

    results = []
    for annoy_idx in nearest_ids:
        # TTS Pattern: rowid = annoy_index + 1
        c.execute("SELECT content_id, metadata_json FROM shoutouts WHERE rowid = ?",
                  (annoy_idx + 1,))
        result = c.fetchone()

        if not result:
            continue

        content_id, metadata_json = result
        full_data = json.loads(metadata_json)

        # Extract category texts for re-ranking
        category_texts = self.vector_db._extract_category_texts(full_data)

        # Continue with re-ranking logic...
        results.append({'id': content_id, ...})

    conn.close()
    return results
```

**Same pattern as catalog.**

---

## Why This Pattern Works

### 1. Survives Restarts
- **Problem:** In-memory dicts lost on restart
- **Solution:** rowid persisted in SQLite database file

### 2. No Separate Files
- **Problem:** metadata.json redundant with database
- **Solution:** Query database directly for all metadata

### 3. Deterministic Mapping
- **Problem:** Enumerate-based indexing changes order
- **Solution:** `ORDER BY rowid` ensures consistent ordering

### 4. Proven Stable
- **TTS Services:** Used this pattern for months without issues
- **Now:** Catalog and user_content use same proven pattern

### 5. Simpler Architecture
- **Removed:** In-memory dicts (`music_track_data`, `user_content_data`)
- **Removed:** metadata.json files
- **Kept:** SQLite .db (metadata) + Annoy .ann (vectors)

---

## Startup Flow

### First Startup (No Database Files)
1. `catalog_database_service.initialize()` - Loads JSON → catalog.db (assigns rowids)
2. `user_content_database_service.initialize()` - Loads JSON → user_content.db (assigns rowids)
3. `catalog_vector_db_service.load_initial_data()` - Builds annoy using rowid pattern
4. `user_content_vector_db_service.load_initial_data()` - Builds annoy using rowid pattern

### Subsequent Restarts
1. catalog.db and user_content.db already exist (rowid preserved)
2. .ann files load successfully
3. Vector search queries by rowid - works perfectly!

**THE KEY TEST:** Restart server - vector search still works (rowid mapping persisted).

---

## File Structure

### Catalog Files
```
data/databases/catalog.db          # SQLite database (tracks with rowid)
data/databases/catalog.db-shm       # Shared memory (WAL mode)
data/databases/catalog.db-wal       # Write-ahead log
data/catalog_embeddings/catalog_1.ann   # Annoy index (vectors)
data/catalog_embeddings/*.db        # Category embedding caches
```

### User Content Files
```
data/databases/user_content.db      # SQLite database (shoutouts with rowid)
data/databases/user_content.db-shm
data/databases/user_content.db-wal
data/user_content_embeddings/user_content_1.ann  # Annoy index
data/user_content_embeddings/*.db   # Category embedding caches
```

### Source Files (Persist)
```
data/tracks/*.json                  # Original track metadata
data/audio/*.mp3                    # Original audio files
data/shoutouts/*.json               # User shoutout metadata
data/shoutouts/*.mp3                # User audio files
```

**Pattern:** Delete .db and .ann files to rebuild from JSON sources. System will recreate with rowid mapping intact.

---

## Implementation Files

### Catalog Services
1. **catalog_database_service.py** - Schema: `track_id TEXT UNIQUE NOT NULL` (implicit rowid)
2. **catalog_vector_database_service.py** - Builds: `annoy.add_item(rowid - 1, embedding)`
3. **catalog_vector_search_service.py** - Searches: `WHERE rowid = ?`, (annoy_idx + 1,)

### User Content Services
4. **user_content_database_service.py** - Schema: `content_id TEXT UNIQUE NOT NULL` (implicit rowid)
5. **user_content_vector_database_service.py** - Builds: `annoy.add_item(rowid - 1, embedding)`
6. **user_content_vector_search_service.py** - Searches: `WHERE rowid = ?`, (annoy_idx + 1,)

### TTS Services (Reference Implementation)
- **tts_vector_db_service.py** - Original stable implementation using this pattern

---

## Summary

**Database Pair:** .db (metadata) + .ann (vectors) = Complete system

**Mapping:** Annoy index ↔ SQLite rowid (deterministic, persistent)

**Build:** Query database `ORDER BY rowid`, use `add_item(rowid - 1, embedding)`

**Search:** Annoy returns indexes, query database `WHERE rowid = annoy_idx + 1`

**Stability:** No in-memory state to lose. Survives restarts. Proven pattern from TTS.
