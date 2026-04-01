<?php
/**
 * web/src/Import/Importer.php — Report import: manifest + decisions seed into MySQL.
 *
 * Import flow:
 *   1. Validate mailbox name (allowlist: alphanumeric + dot + hyphen + underscore, no traversal).
 *   2. Resolve manifest path strictly under data_dir/mailboxes/<mailbox>/reports/.
 *      File is named <report_name>.manifest.json where report_name is the Maildir basename.
 *      The standard store-mailbox workflow always produces report_name = "maildir".
 *   3. Parse manifest JSON; hard-fail on unknown schema_version (no partial writes).
 *   4. Derive report_id = manifest["pdf_sha256"]; fail if null.
 *   5. Parse <report_name>.decisions.csv for per-email display fields (optional seed).
 *   6. Upsert reports row; upsert emails rows; all inside a single transaction.
 *
 * Path traversal guard:
 *   - mailbox name is matched against ^[A-Za-z0-9._-]+$ before any path construction.
 *   - resolved manifest path is verified to be under data_dir/mailboxes/ using realpath().
 *   - Only files that physically exist under that tree are allowed.
 *
 * Idempotence:
 *   - INSERT ... ON DUPLICATE KEY UPDATE on both reports and emails.
 *   - Re-import of the same report_id is safe and updates display fields.
 */

declare(strict_types=1);

namespace MailReview\Import;

use PDO;
use PDOException;

class Importer
{
    /** The only manifest schema version this importer understands. */
    private const SUPPORTED_SCHEMA_VERSION = '1.0';

    /** Regex for an allowlisted mailbox name. */
    private const MAILBOX_PATTERN = '/^[A-Za-z0-9._-]+$/';

    public function __construct(
        private readonly PDO    $pdo,
        private readonly string $dataDir
    ) {}

    /**
     * Import a report for the given mailbox name.
     *
     * @param  string $mailbox     Mailbox folder name under data_dir/mailboxes/<mailbox>/.
     * @param  string $reportName  Maildir basename used in filenames (default: 'maildir').
     *                             store-mailbox always uses 'maildir' as the rsync target,
     *                             producing <maildir>.manifest.json and <maildir>.decisions.csv.
     * @return array{report_id:string, email_count:int, decisions_seed_path:string}
     * @throws ImportException  On validation failure, unknown schema version, or DB error.
     */
    public function import(string $mailbox, string $reportName = 'maildir'): array
    {
        // ── 1. Allowlist mailbox name ────────────────────────────────────────
        if (!preg_match(self::MAILBOX_PATTERN, $mailbox)) {
            throw new ImportException(
                "Invalid mailbox name: only alphanumeric, dot, hyphen, and underscore allowed.",
                400
            );
        }
        if (!preg_match(self::MAILBOX_PATTERN, $reportName)) {
            throw new ImportException(
                "Invalid report_name: only alphanumeric, dot, hyphen, and underscore allowed.",
                400
            );
        }

        // ── 2. Resolve and guard manifest path ─────────────────────────────────────
        $reportsDir   = $this->dataDir . '/mailboxes/' . $mailbox . '/reports';
        $manifestPath = $reportsDir . '/' . $reportName . '.manifest.json';

        $this->assertPathUnderDataDir($manifestPath);

        if (!is_file($manifestPath)) {
            throw new ImportException(
                "Manifest file not found: data_dir/mailboxes/{$mailbox}/reports/{$reportName}.manifest.json",
                404
            );
        }

        // ── 3. Read + parse manifest ─────────────────────────────────────────────
        $raw = file_get_contents($manifestPath);
        if ($raw === false) {
            throw new ImportException("Cannot read manifest file.", 500);
        }

        $manifest = json_decode($raw, true, 512, JSON_THROW_ON_ERROR);
        if (!is_array($manifest)) {
            throw new ImportException("Manifest JSON is not an object.", 400);
        }

        // ── 4. Schema version check (hard fail — no partial writes) ──────────────
        $schemaVersion = $manifest['schema_version'] ?? null;
        if ($schemaVersion !== self::SUPPORTED_SCHEMA_VERSION) {
            throw new ImportException(
                "Unknown manifest schema_version: " . json_encode($schemaVersion) .
                ". Only \"" . self::SUPPORTED_SCHEMA_VERSION . "\" is supported.",
                400
            );
        }

        // ── 5. Derive report_id ──────────────────────────────────────────────────
        $reportId = $manifest['pdf_sha256'] ?? null;
        if (!is_string($reportId) || $reportId === '' || $reportId === 'null') {
            throw new ImportException(
                "Manifest pdf_sha256 is null or missing; cannot derive report_id.",
                400
            );
        }

        $generatedAt = $manifest['generated_at'] ?? '';

        // ── 6. Parse decisions CSV for per-email display fields ──────────────────
        $decisionsPath = $reportsDir . '/' . $reportName . '.decisions.csv';
        $this->assertPathUnderDataDir($decisionsPath);

        $emailRows          = $this->parseDecisionsCsv($decisionsPath, $manifest);
        $decisionsSeedPath  = is_file($decisionsPath) ? $decisionsPath : '';

        // ── 7. Transactional upsert ──────────────────────────────────────────────
        try {
            $this->pdo->beginTransaction();

            // Upsert reports row
            $stmt = $this->pdo->prepare(<<<SQL
                INSERT INTO reports
                    (report_id, mailbox, generated_at, pdf_path, manifest_path, decisions_seed_path)
                VALUES
                    (:report_id, :mailbox, :generated_at, :pdf_path, :manifest_path, :decisions_seed_path)
                ON DUPLICATE KEY UPDATE
                    mailbox              = VALUES(mailbox),
                    generated_at         = VALUES(generated_at),
                    pdf_path             = VALUES(pdf_path),
                    manifest_path        = VALUES(manifest_path),
                    decisions_seed_path  = VALUES(decisions_seed_path)
            SQL);

            $pdfPath = $reportsDir . '/' . $reportName . '.pdf';
            $stmt->execute([
                ':report_id'           => $reportId,
                ':mailbox'             => $mailbox,
                ':generated_at'        => $generatedAt,
                ':pdf_path'            => is_file($pdfPath) ? $pdfPath : '',
                ':manifest_path'       => $manifestPath,
                ':decisions_seed_path' => $decisionsSeedPath,
            ]);

            // Upsert emails rows
            $emailStmt = $this->pdo->prepare(<<<SQL
                INSERT INTO emails
                    (report_id, stable_id, folder, date, sender, subject,
                     total_size_bytes, is_duplicate, dup_group_id, dup_rank)
                VALUES
                    (:report_id, :stable_id, :folder, :date, :sender, :subject,
                     :total_size_bytes, :is_duplicate, :dup_group_id, :dup_rank)
                ON DUPLICATE KEY UPDATE
                    folder           = VALUES(folder),
                    date             = VALUES(date),
                    sender           = VALUES(sender),
                    subject          = VALUES(subject),
                    total_size_bytes = VALUES(total_size_bytes),
                    is_duplicate     = VALUES(is_duplicate),
                    dup_group_id     = VALUES(dup_group_id),
                    dup_rank         = VALUES(dup_rank)
            SQL);

            foreach ($emailRows as $row) {
                $emailStmt->execute($row);
            }

            $this->pdo->commit();
        } catch (PDOException $e) {
            $this->pdo->rollBack();
            throw new ImportException(
                "Database error during import: " . $e->getMessage(),
                500,
                $e
            );
        }

        return [
            'report_id'            => $reportId,
            'email_count'          => count($emailRows),
            'decisions_seed_path'  => $decisionsSeedPath,
        ];
    }

    // ── Private helpers ───────────────────────────────────────────────────────────

    /**
     * Assert that the given path is physically under data_dir/mailboxes/.
     * Uses realpath() on the parent directory to prevent traversal; the file
     * itself need not exist yet (realpath() fails on non-existent files).
     *
     * @throws ImportException  If the resolved path escapes the allowed tree.
     */
    private function assertPathUnderDataDir(string $path): void
    {
        $allowedRoot = realpath($this->dataDir . '/mailboxes');
        if ($allowedRoot === false) {
            throw new ImportException(
                "data_dir mailboxes root does not exist or is not accessible.",
                500
            );
        }

        // Normalise the path without requiring the file to exist:
        // resolve the parent directory, then re-append the basename.
        $parentDir = dirname($path);
        $resolvedParent = realpath($parentDir);

        if ($resolvedParent === false) {
            // Parent directory does not exist — path definitely not reachable.
            throw new ImportException(
                "Import path parent directory does not exist.",
                400
            );
        }

        $resolved = $resolvedParent . DIRECTORY_SEPARATOR . basename($path);

        if (!str_starts_with($resolved, $allowedRoot . DIRECTORY_SEPARATOR)) {
            throw new ImportException(
                "Import path is not under the configured data directory.",
                400
            );
        }
    }

    /**
     * Parse the decisions CSV file and merge with manifest stable_ids.
     *
     * Returns an array of row arrays ready for PDO binding.
     * If the CSV is absent, falls back to manifest email_stable_ids with
     * empty display fields (so emails rows are always created).
     *
     * @param  string                $csvPath   Absolute path to <mailbox>.decisions.csv.
     * @param  array<string,mixed>   $manifest  Parsed manifest array.
     * @return list<array<string,mixed>>
     */
    private function parseDecisionsCsv(string $csvPath, array $manifest): array
    {
        $reportId       = $manifest['pdf_sha256'];
        $stableIds      = $manifest['email_stable_ids'] ?? [];
        $dupGroups      = $manifest['dup_groups'] ?? [];

        // Build a lookup: stable_id -> dup info from manifest
        $dupByStableId = [];
        foreach ($dupGroups as $group) {
            $groupId  = $group['group_id'] ?? '';
            $members  = $group['member_email_ids'] ?? [];
            $canonId  = $group['canonical_email_id'] ?? '';
            foreach ($members as $idx => $sid) {
                // dup_rank: canonical = 0, others = 1,2,... by position in member list
                $rank = ($sid === $canonId) ? 0 : ($idx);
                $dupByStableId[$sid] = [
                    'dup_group_id' => $groupId,
                    'dup_rank'     => $rank,
                ];
            }
        }

        // If CSV exists, parse it for display fields
        if (is_file($csvPath)) {
            return $this->parseEmailsFromCsv($csvPath, $reportId, $dupByStableId);
        }

        // Fallback: manifest stable_ids only — display fields empty
        $rows = [];
        foreach ($stableIds as $sid) {
            $dupInfo = $dupByStableId[$sid] ?? null;
            $rows[] = [
                ':report_id'        => $reportId,
                ':stable_id'        => $sid,
                ':folder'           => '',
                ':date'             => '',
                ':sender'           => '',
                ':subject'          => '',
                ':total_size_bytes' => 0,
                ':is_duplicate'     => ($dupInfo !== null) ? 1 : 0,
                ':dup_group_id'     => $dupInfo['dup_group_id'] ?? '',
                ':dup_rank'         => $dupInfo['dup_rank'] ?? -1,
            ];
        }
        return $rows;
    }

    /**
     * Parse decisions CSV and return bound rows for the emails table.
     *
     * CSV columns (decisions_template.py order):
     *   stable_id, filepath, decision, folder, date, from, subject,
     *   total_size_bytes, attachment_count, attachment_total_bytes,
     *   attachment_names, is_duplicate, dup_group_id, dup_rank
     *
     * @param  string               $csvPath
     * @param  string               $reportId
     * @param  array<string,array>  $dupByStableId  Dup info from manifest (authoritative)
     * @return list<array<string,mixed>>
     */
    private function parseEmailsFromCsv(
        string $csvPath,
        string $reportId,
        array  $dupByStableId
    ): array {
        $fh = fopen($csvPath, 'r');
        if ($fh === false) {
            throw new ImportException("Cannot open decisions CSV file.", 500);
        }

        try {
            // Read header row
            $header = fgetcsv($fh);
            if ($header === false || $header === null) {
                throw new ImportException("Decisions CSV is empty or unreadable.", 400);
            }

            // Map column names to indexes
            $idx = array_flip($header);
            $required = ['stable_id', 'folder', 'date', 'from', 'subject',
                         'total_size_bytes', 'is_duplicate', 'dup_group_id', 'dup_rank'];
            foreach ($required as $col) {
                if (!isset($idx[$col])) {
                    throw new ImportException(
                        "Decisions CSV missing required column: {$col}",
                        400
                    );
                }
            }

            $rows = [];
            $lineNo = 1;
            while (($record = fgetcsv($fh)) !== false) {
                $lineNo++;
                if ($record === null) {
                    continue;
                }

                $sid = $record[$idx['stable_id']] ?? '';
                if ($sid === '') {
                    continue; // skip blank rows
                }

                $isDupStr  = strtolower(trim($record[$idx['is_duplicate']] ?? ''));
                $isDupBool = ($isDupStr === 'true' || $isDupStr === '1') ? 1 : 0;

                // dup_group_id and dup_rank from CSV; fallback to manifest data
                $dupInfo    = $dupByStableId[$sid] ?? null;
                $dupGroupId = trim($record[$idx['dup_group_id']] ?? '');
                $dupRankRaw = trim($record[$idx['dup_rank']] ?? '');
                $dupRank    = ($dupRankRaw !== '') ? (int)$dupRankRaw : -1;

                // If manifest has authoritative dup info, prefer it
                if ($dupInfo !== null) {
                    $dupGroupId = $dupInfo['dup_group_id'];
                    $dupRank    = $dupInfo['dup_rank'];
                    $isDupBool  = 1;
                }

                $rows[] = [
                    ':report_id'        => $reportId,
                    ':stable_id'        => $sid,
                    ':folder'           => trim($record[$idx['folder']] ?? ''),
                    ':date'             => trim($record[$idx['date']] ?? ''),
                    ':sender'           => trim($record[$idx['from']] ?? ''),
                    ':subject'          => trim($record[$idx['subject']] ?? ''),
                    ':total_size_bytes' => (int)($record[$idx['total_size_bytes']] ?? 0),
                    ':is_duplicate'     => $isDupBool,
                    ':dup_group_id'     => $dupGroupId,
                    ':dup_rank'         => $dupRank,
                ];
            }

            return $rows;
        } finally {
            fclose($fh);
        }
    }
}
