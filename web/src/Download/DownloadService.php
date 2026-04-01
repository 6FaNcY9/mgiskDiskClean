<?php
/**
 * web/src/Download/DownloadService.php — Authenticated file download service.
 *
 * Security model
 * --------------
 * All file paths are resolved from trusted data sources (MySQL reports table,
 * per-mailbox SQLite index).  User input is NEVER used directly as a file path.
 *
 * Report artifact downloads (PDF / manifest / seed CSV):
 *   - Looks up the report row in MySQL by report_id.
 *   - Resolves the file under data_dir/mailboxes/<mailbox>/reports/.
 *   - Rejects paths that do not resolve under data_dir (traversal guard).
 *
 * Attachment downloads:
 *   - Keyed by (report_id, email_stable_id, attachment_sha256).
 *   - Finds the mailbox for the report from MySQL.
 *   - Opens the per-mailbox SQLite index (data_dir/mailboxes/<mailbox>/index.sqlite).
 *   - Looks up the attachment row by (email_stable_id, sha256) in the index.
 *   - Verifies email_stable_id belongs to report_id via MySQL emails table.
 *   - Resolves stored_path; verifies it is under data_dir/mailboxes/<mailbox>/attachments/.
 *   - Returns file info for the caller to stream.
 *
 * Path traversal guard
 * --------------------
 * assertPathUnderRoot() resolves the PARENT directory via realpath() (so the
 * file need not exist for the check), then re-appends the basename.  The
 * resolved path must start with allowedRoot + DIRECTORY_SEPARATOR.
 *
 * MIME type mapping
 * -----------------
 * A small static allowlist maps known extensions to safe MIME types.
 * Unknown types default to application/octet-stream with forced download,
 * preventing browsers from interpreting untrusted payloads as HTML/JS.
 */

declare(strict_types=1);

namespace MailReview\Download;

use PDO;
use SQLite3;

/**
 * Information about a file that can be streamed to the client.
 *
 * @phpstan-type FileInfo array{path:string, filename:string, mime:string, size:int}
 */
class DownloadService
{
    /** Regex for an allowlisted mailbox name (mirrors Importer). */
    private const MAILBOX_PATTERN = '/^[A-Za-z0-9._-]+$/';

    /** Regex for a report artifact filename (no path separators, no dots-only). */
    private const REPORT_FILE_PATTERN = '/^[A-Za-z0-9._-]+\.(pdf|json|csv)$/i';

    /** Regex for a SHA-256 hex string (64 lowercase hex digits). */
    private const SHA256_PATTERN = '/^[0-9a-f]{64}$/';

    /**
     * @var array<string,string> Extension → MIME type for known safe formats.
     *   Anything outside this map gets application/octet-stream.
     */
    private const MIME_MAP = [
        'pdf'  => 'application/pdf',
        'json' => 'application/json',
        'csv'  => 'text/csv; charset=utf-8',
        'txt'  => 'text/plain; charset=utf-8',
        'png'  => 'image/png',
        'jpg'  => 'image/jpeg',
        'jpeg' => 'image/jpeg',
        'gif'  => 'image/gif',
        'webp' => 'image/webp',
        'zip'  => 'application/zip',
        'gz'   => 'application/gzip',
    ];

    public function __construct(
        private readonly PDO    $pdo,
        private readonly string $dataDir
    ) {}

    // ── Report artifact download ───────────────────────────────────────────────

    /**
     * Resolve a report artifact (PDF, manifest JSON, seed CSV) for download.
     *
     * @param  string $reportId  Report identifier (pdf_sha256 hex string).
     * @param  string $file      Filename requested (e.g. "maildir.pdf").
     *                           Must match REPORT_FILE_PATTERN — no path separators.
     * @return array{path:string, filename:string, mime:string, size:int}
     * @throws DownloadException  404 if report not found; 400 if file param invalid.
     */
    public function resolveReportArtifact(string $reportId, string $file): array
    {
        // Validate requested filename: only safe characters, known extensions.
        if (!preg_match(self::REPORT_FILE_PATTERN, $file)) {
            throw new DownloadException(
                'Invalid file parameter: only alphanumeric, dot, hyphen, underscore with .pdf/.json/.csv extension allowed.',
                400
            );
        }

        // Look up the report to get the mailbox name.
        $row = $this->fetchReportRow($reportId);

        $mailbox = $row['mailbox'];
        $this->assertValidMailboxName($mailbox);

        // Build the expected path under reports/.
        $reportsDir = $this->dataDir . '/mailboxes/' . $mailbox . '/reports';
        $filePath   = $reportsDir . '/' . $file;

        // Strict path guard: resolved path must be under data_dir.
        $this->assertPathUnderRoot($filePath, $this->dataDir);

        if (!is_file($filePath)) {
            throw new DownloadException(
                "Report file not found: {$file}",
                404
            );
        }

        $mime = $this->mimeForPath($filePath);

        return [
            'path'     => $filePath,
            'filename' => basename($filePath),
            'mime'     => $mime,
            'size'     => (int)filesize($filePath),
        ];
    }

    // ── Attachment download ────────────────────────────────────────────────────

    /**
     * Resolve an attachment file for download.
     *
     * The lookup tuple (report_id, email_stable_id, sha256) must all match;
     * any mismatch returns 404.
     *
     * @param  string $reportId      Report identifier.
     * @param  string $emailStableId Stable email identifier within the report.
     * @param  string $sha256        SHA-256 hex of the attachment content (64 hex chars).
     * @return array{path:string, filename:string, mime:string, size:int}
     * @throws DownloadException  404 on any mismatch; 400 on bad parameters.
     */
    public function resolveAttachment(
        string $reportId,
        string $emailStableId,
        string $sha256
    ): array {
        // Validate sha256 format to reject obvious injection attempts early.
        if (!preg_match(self::SHA256_PATTERN, $sha256)) {
            throw new DownloadException(
                'Invalid sha256 parameter: must be 64 lowercase hex characters.',
                400
            );
        }

        // 1. Look up the report to get mailbox; ensures report_id is valid.
        $reportRow = $this->fetchReportRow($reportId);
        $mailbox   = $reportRow['mailbox'];
        $this->assertValidMailboxName($mailbox);

        // 2. Verify email_stable_id belongs to this report_id (MySQL check).
        $emailStmt = $this->pdo->prepare(
            'SELECT 1 FROM emails WHERE report_id = :report_id AND stable_id = :stable_id LIMIT 1'
        );
        $emailStmt->execute([':report_id' => $reportId, ':stable_id' => $emailStableId]);
        if ($emailStmt->fetchColumn() === false) {
            // Mismatch: stable_id does not belong to this report.
            throw new DownloadException(
                'Attachment not found for the given report_id and stable_id.',
                404
            );
        }

        // 3. Open the per-mailbox SQLite index to resolve stored_path.
        $indexPath = $this->dataDir . '/mailboxes/' . $mailbox . '/index.sqlite';
        $this->assertPathUnderRoot($indexPath, $this->dataDir);

        if (!is_file($indexPath)) {
            throw new DownloadException(
                'Mailbox index not found; run index-mailbox to rebuild it.',
                404
            );
        }

        [$storedPath, $originalFilename, $mime] = $this->lookupAttachmentInIndex(
            $indexPath,
            $emailStableId,
            $sha256
        );

        // 4. Validate the resolved stored_path is under the mailbox attachments dir.
        $attachmentDir = $this->dataDir . '/mailboxes/' . $mailbox . '/attachments';
        $this->assertPathUnderRoot($storedPath, $attachmentDir);

        if (!is_file($storedPath)) {
            throw new DownloadException(
                'Attachment file not found on disk. The index may be stale; run extract-attachments.',
                404
            );
        }

        // Use MIME from index if available; otherwise infer from stored filename.
        if ($mime === '' || $mime === 'application/octet-stream') {
            $mime = $this->mimeForPath($storedPath);
        }

        return [
            'path'     => $storedPath,
            'filename' => $originalFilename !== '' ? $originalFilename : basename($storedPath),
            'mime'     => $mime,
            'size'     => (int)filesize($storedPath),
        ];
    }

    // ── Private helpers ────────────────────────────────────────────────────────

    /**
     * Fetch a reports row by report_id; throws 404 if missing.
     *
     * @return array{report_id:string, mailbox:string, generated_at:string}
     */
    private function fetchReportRow(string $reportId): array
    {
        if ($reportId === '') {
            throw new DownloadException('Missing report_id.', 400);
        }

        $stmt = $this->pdo->prepare(
            'SELECT report_id, mailbox, generated_at FROM reports WHERE report_id = :report_id LIMIT 1'
        );
        $stmt->execute([':report_id' => $reportId]);
        $row = $stmt->fetch(PDO::FETCH_ASSOC);

        if ($row === false) {
            throw new DownloadException('Report not found.', 404);
        }

        return $row;
    }

    /**
     * Open the per-mailbox SQLite index and look up the attachment row.
     *
     * Uses PHP's SQLite3 extension (available without Composer) to query
     * the index written by index_mailbox.py.
     *
     * @return array{0:string, 1:string, 2:string}  [stored_path, original_filename, mime]
     * @throws DownloadException  404 if the (stable_id, sha256) pair is not found.
     */
    private function lookupAttachmentInIndex(
        string $indexPath,
        string $emailStableId,
        string $sha256
    ): array {
        // SQLite3 extension is bundled with PHP; no PDO SQLite required.
        $db = new \SQLite3($indexPath, SQLITE3_OPEN_READONLY);
        $db->enableExceptions(true);

        try {
            $stmt = $db->prepare(
                'SELECT stored_path, original_filename, mime
                   FROM attachments
                  WHERE email_stable_id = :stable_id
                    AND sha256          = :sha256
                  LIMIT 1'
            );
            $stmt->bindValue(':stable_id', $emailStableId, SQLITE3_TEXT);
            $stmt->bindValue(':sha256',    $sha256,        SQLITE3_TEXT);

            $result = $stmt->execute();
            if ($result === false) {
                throw new DownloadException('Attachment lookup failed.', 500);
            }

            $row = $result->fetchArray(SQLITE3_ASSOC);
        } finally {
            $db->close();
        }

        if ($row === false || $row === null) {
            throw new DownloadException(
                'Attachment not found in index (stable_id + sha256 mismatch).',
                404
            );
        }

        return [
            (string)($row['stored_path']       ?? ''),
            (string)($row['original_filename'] ?? ''),
            (string)($row['mime']              ?? ''),
        ];
    }

    /**
     * Assert that a path (or its closest existing ancestor) resolves under $root.
     *
     * Uses the parent-directory pattern: realpath() on the parent (which must
     * exist), then re-appends the basename.  This allows checking paths for
     * files that may not exist yet without getting a false null from realpath().
     *
     * @throws DownloadException  400 if the path escapes the allowed root.
     */
    private function assertPathUnderRoot(string $path, string $root): void
    {
        $normalizedRoot = rtrim((string)realpath($root), '/\\');
        if ($normalizedRoot === '') {
            throw new DownloadException('Configured data root does not exist.', 500);
        }

        // Walk up until we find an existing ancestor we can realpath().
        $check    = $path;
        $suffixes = [];
        while ($check !== '' && $check !== '/' && $check !== '.' && !file_exists($check)) {
            $suffixes[] = basename($check);
            $check      = dirname($check);
        }

        $resolvedBase = realpath($check);
        if ($resolvedBase === false) {
            throw new DownloadException('Path could not be resolved.', 400);
        }

        // Reconstruct: base + reversed suffixes.
        $resolvedPath = $resolvedBase;
        foreach (array_reverse($suffixes) as $part) {
            $resolvedPath .= DIRECTORY_SEPARATOR . $part;
        }

        // The resolved path must start with normalizedRoot/.
        $guard = $normalizedRoot . DIRECTORY_SEPARATOR;
        if (!str_starts_with($resolvedPath, $guard)) {
            throw new DownloadException(
                'Access denied: requested path is outside the allowed data directory.',
                400
            );
        }
    }

    /**
     * Assert the mailbox name is safe (mirrors Importer).
     *
     * @throws DownloadException  400 if invalid.
     */
    private function assertValidMailboxName(string $mailbox): void
    {
        if (!preg_match(self::MAILBOX_PATTERN, $mailbox)) {
            throw new DownloadException('Invalid mailbox name in report record.', 400);
        }
    }

    /**
     * Determine a safe MIME type for the given file path.
     *
     * Prefers the static allowlist by extension; falls back to
     * application/octet-stream (forces download, no browser interpretation).
     */
    private function mimeForPath(string $path): string
    {
        $ext = strtolower(ltrim(pathinfo($path, PATHINFO_EXTENSION), '.'));
        return self::MIME_MAP[$ext] ?? 'application/octet-stream';
    }
}
