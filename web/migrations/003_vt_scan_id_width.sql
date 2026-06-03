-- Widen vt_cache.scan_id: VT v2 scan IDs are ~75 chars ("<sha256>-<unix_ts>"),
-- so VARCHAR(64) silently truncated them, causing pollScan to loop on pending.
ALTER TABLE vt_cache MODIFY scan_id   VARCHAR(128)     NOT NULL DEFAULT '';
ALTER TABLE vt_cache MODIFY positives SMALLINT UNSIGNED NOT NULL DEFAULT 0;
