<?php
/**
 * web/src/Services/ReviewService.php
 * Handles data fetching for review and admin UIs.
 */

declare(strict_types=1);

namespace MailReview\Services;

class ReviewService
{
    private \PDO $pdo;

    public function __construct(\PDO $pdo)
    {
        $this->pdo = $pdo;
    }

    /**
     * Get list of reports for admin dashboard.
     */
    public function getReports(): array
    {
        $stmt = $this->pdo->query('
            SELECT 
                r.report_id, 
                r.mailbox, 
                r.imported_at,
                (SELECT COUNT(*) FROM emails e WHERE e.report_id = r.report_id) as total_emails,
                (SELECT COUNT(*) FROM decisions d WHERE d.report_id = r.report_id AND d.decision != "") as reviewed_count
            FROM reports r
            ORDER BY r.imported_at DESC
        ');
        return $stmt->fetchAll();
    }

    /**
     * Get emails for a specific report, with decisions joined.
     */
    public function getReportEmails(string $reportId, int $limit = 50, int $offset = 0, array $filters = []): array
    {
        $sql = '
            SELECT 
                e.*,
                d.decision,
                d.note,
                d.updated_by,
                d.updated_at
            FROM emails e
            LEFT JOIN decisions d ON e.report_id = d.report_id AND e.stable_id = d.stable_id
            WHERE e.report_id = :report_id
        ';

        $params = [':report_id' => $reportId];

        if (!empty($filters['decision'])) {
             if ($filters['decision'] === 'none') {
                 $sql .= ' AND (d.decision IS NULL OR d.decision = "")';
             } else {
                 $sql .= ' AND d.decision = :decision';
                 $params[':decision'] = $filters['decision'];
             }
        }

        if (!empty($filters['only_duplicates'])) {
            $sql .= ' AND e.is_duplicate = 1';
        }

        if (!empty($filters['search'])) {
            $sql .= ' AND (e.subject LIKE :search OR e.sender LIKE :search)';
            $params[':search'] = '%' . $filters['search'] . '%';
        }

        // Sort by date (newest first) and then stable_id
        $sql .= ' ORDER BY e.date DESC, e.stable_id ASC';

        $sql .= ' LIMIT ' . (int)$limit . ' OFFSET ' . (int)$offset;

        $stmt = $this->pdo->prepare($sql);
        $stmt->execute($params);
        return $stmt->fetchAll();
    }

    public function countReportEmails(string $reportId, array $filters = []): int
    {
        $sql = '
            SELECT COUNT(*)
            FROM emails e
            LEFT JOIN decisions d ON e.report_id = d.report_id AND e.stable_id = d.stable_id
            WHERE e.report_id = :report_id
        ';

        $params = [':report_id' => $reportId];

        if (!empty($filters['decision'])) {
             if ($filters['decision'] === 'none') {
                 $sql .= ' AND (d.decision IS NULL OR d.decision = "")';
             } else {
                 $sql .= ' AND d.decision = :decision';
                 $params[':decision'] = $filters['decision'];
             }
        }

        if (!empty($filters['only_duplicates'])) {
            $sql .= ' AND e.is_duplicate = 1';
        }

        if (!empty($filters['search'])) {
             $sql .= ' AND (e.subject LIKE :search OR e.sender LIKE :search)';
             $params[':search'] = '%' . $filters['search'] . '%';
        }

        $stmt = $this->pdo->prepare($sql);
        $stmt->execute($params);
        return (int)$stmt->fetchColumn();
    }

    /**
     * Get aggregated overview for admin.
     */
    public function getAdminOverview(array $filters = []): array
    {
        $sql = '
            SELECT 
                d.decision,
                d.updated_by,
                COUNT(*) as count,
                MAX(d.updated_at) as last_updated
            FROM decisions d
            WHERE d.decision != ""
        ';
        
        $params = [];

        if (!empty($filters['updated_by'])) {
            $sql .= ' AND d.updated_by = :updated_by';
            $params[':updated_by'] = $filters['updated_by'];
        }

        if (!empty($filters['decision'])) {
             $sql .= ' AND d.decision = :decision';
             $params[':decision'] = $filters['decision'];
        }

        $sql .= ' GROUP BY d.decision, d.updated_by ORDER BY last_updated DESC';

        $stmt = $this->pdo->prepare($sql);
        $stmt->execute($params);
        return $stmt->fetchAll();
    }

    /**
     * Get all unique users who have made decisions.
     */
    public function getReviewers(): array
    {
        $stmt = $this->pdo->query('SELECT DISTINCT updated_by FROM decisions WHERE updated_by != "" ORDER BY updated_by');
        return $stmt->fetchAll(\PDO::FETCH_COLUMN);
    }
    }

    /**
     * Get attachment rows for one email from the per-mailbox SQLite index.
     *
     * The report must already be imported into MySQL so we can resolve the
     * mailbox name and then open the per-mailbox index.sqlite.
     *
     * Returns an empty array when the index does not exist (graceful degradation).
     * Each row: ['sha256', 'size', 'mime', 'original_filename', 'stored_path'].
     *
     * @return list<array{sha256:string, size:int, mime:string, original_filename:string, stored_path:string}>
     */
    public function getEmailAttachments(string $reportId, string $stableId, string $dataDir): array
    {
        // Resolve mailbox from the reports table.
        $stmt = $this->pdo->prepare(
            'SELECT mailbox FROM reports WHERE report_id = :report_id LIMIT 1'
        );
        $stmt->execute([':report_id' => $reportId]);
        $row = $stmt->fetch(\PDO::FETCH_ASSOC);
        if ($row === false) {
            return [];
        }
        $mailbox = (string)$row['mailbox'];

        $indexPath = rtrim($dataDir, '/') . '/mailboxes/' . $mailbox . '/index.sqlite';
        if (!is_file($indexPath)) {
            return [];
        }

        try {
            $db = new \SQLite3($indexPath, SQLITE3_OPEN_READONLY);
            $db->enableExceptions(true);
            $s = $db->prepare(
                'SELECT sha256, size, mime, original_filename, stored_path
                   FROM attachments
                  WHERE email_stable_id = :stable_id
                  ORDER BY original_filename ASC'
            );
            $s->bindValue(':stable_id', $stableId, SQLITE3_TEXT);
            $res = $s->execute();
            $rows = [];
            while ($r = $res->fetchArray(SQLITE3_ASSOC)) {
                $rows[] = [
                    'sha256'            => (string)($r['sha256']            ?? ''),
                    'size'              => (int)($r['size']               ?? 0),
                    'mime'             => (string)($r['mime']              ?? ''),
                    'original_filename' => (string)($r['original_filename'] ?? ''),
                    'stored_path'       => (string)($r['stored_path']       ?? ''),
                ];
            }
            $db->close();
            return $rows;
        } catch (\Exception $e) {
            // Non-fatal: index may be unavailable; degrade gracefully.
            error_log('getEmailAttachments SQLite error: ' . $e->getMessage());
            return [];
        }
    }
}
