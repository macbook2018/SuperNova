"""Frozen PostgreSQL trigger definitions for revision 0015 and metadata-based test schemas."""

# Older binaries write only the v1 tables. Their writes must invalidate a previously
# published v2 reader even when the process writing the rows predates the index API.
DOCUMENT_FUNCTION = """
CREATE FUNCTION sn_ki_document_changed() RETURNS trigger AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        PERFORM 1 FROM knowledge_index_states
        WHERE tenant_id = OLD.tenant_id AND knowledge_base_id = OLD.knowledge_base_id FOR UPDATE;
        UPDATE knowledge_index_states SET active_release_id = NULL, generation = generation + 1
        WHERE tenant_id = OLD.tenant_id AND knowledge_base_id = OLD.knowledge_base_id
          AND active_release_id IS NOT NULL;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        PERFORM 1 FROM knowledge_index_states
        WHERE tenant_id = NEW.tenant_id AND knowledge_base_id = NEW.knowledge_base_id FOR UPDATE;
        UPDATE knowledge_index_states SET active_release_id = NULL, generation = generation + 1
        WHERE tenant_id = NEW.tenant_id AND knowledge_base_id = NEW.knowledge_base_id
          AND active_release_id IS NOT NULL;
        IF NEW.status = 'deleted' THEN
            DELETE FROM knowledge_index_chunks WHERE tenant_id = NEW.tenant_id
              AND knowledge_base_id = NEW.knowledge_base_id AND document_id = NEW.id;
            DELETE FROM knowledge_document_index_revisions WHERE tenant_id = NEW.tenant_id
              AND knowledge_base_id = NEW.knowledge_base_id AND document_id = NEW.id;
        END IF;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql SET search_path FROM CURRENT
"""

CHUNKS_FUNCTION = """
CREATE FUNCTION sn_ki_chunks_changed() RETURNS trigger AS $$
BEGIN
    -- Include v1 state rows: publishing can change a NULL pointer while this writer is in flight.
    PERFORM s.tenant_id FROM knowledge_index_states s
    JOIN (SELECT DISTINCT tenant_id, knowledge_base_id FROM changed_rows) c
      USING (tenant_id, knowledge_base_id)
    ORDER BY s.tenant_id, s.knowledge_base_id FOR UPDATE OF s;
    UPDATE knowledge_index_states s SET active_release_id = NULL, generation = s.generation + 1
    FROM (SELECT DISTINCT tenant_id, knowledge_base_id FROM changed_rows) c
    WHERE s.tenant_id = c.tenant_id AND s.knowledge_base_id = c.knowledge_base_id
      AND s.active_release_id IS NOT NULL;
    IF TG_OP <> 'INSERT' THEN
        DELETE FROM knowledge_document_index_revisions r
        USING (SELECT DISTINCT tenant_id, knowledge_base_id, document_id, index_version
               FROM changed_rows) c
        WHERE (r.tenant_id, r.knowledge_base_id, r.document_id, r.index_version)
            = (c.tenant_id, c.knowledge_base_id, c.document_id, c.index_version);
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql SET search_path FROM CURRENT
"""

CREATE_LEGACY_GUARDS = (
    DOCUMENT_FUNCTION,
    CHUNKS_FUNCTION,
    """CREATE TRIGGER ki_documents_insert_delete AFTER INSERT OR DELETE ON knowledge_documents
       FOR EACH ROW EXECUTE FUNCTION sn_ki_document_changed()""",
    """CREATE TRIGGER ki_documents_update AFTER UPDATE ON knowledge_documents
       FOR EACH ROW WHEN (
         (NEW.tenant_id, NEW.knowledge_base_id, NEW.status, NEW.active_index_version,
          NEW.sha256, NEW.chunk_count) IS DISTINCT FROM
         (OLD.tenant_id, OLD.knowledge_base_id, OLD.status, OLD.active_index_version,
          OLD.sha256, OLD.chunk_count))
       EXECUTE FUNCTION sn_ki_document_changed()""",
    """CREATE TRIGGER ki_chunks_insert AFTER INSERT ON knowledge_chunks
       REFERENCING NEW TABLE AS changed_rows FOR EACH STATEMENT
       EXECUTE FUNCTION sn_ki_chunks_changed()""",
    """CREATE TRIGGER ki_chunks_delete AFTER DELETE ON knowledge_chunks
       REFERENCING OLD TABLE AS changed_rows FOR EACH STATEMENT
       EXECUTE FUNCTION sn_ki_chunks_changed()""",
    """CREATE TRIGGER ki_chunks_update AFTER UPDATE ON knowledge_chunks
       REFERENCING OLD TABLE AS changed_rows FOR EACH STATEMENT
       EXECUTE FUNCTION sn_ki_chunks_changed()""",
)

DROP_LEGACY_GUARDS = (
    "DROP TRIGGER ki_chunks_update ON knowledge_chunks",
    "DROP TRIGGER ki_chunks_delete ON knowledge_chunks",
    "DROP TRIGGER ki_chunks_insert ON knowledge_chunks",
    "DROP TRIGGER ki_documents_update ON knowledge_documents",
    "DROP TRIGGER ki_documents_insert_delete ON knowledge_documents",
    "DROP FUNCTION sn_ki_chunks_changed()",
    "DROP FUNCTION sn_ki_document_changed()",
)
