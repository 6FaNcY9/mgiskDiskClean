<?php
/**
 * VtService — VirusTotal v2 API wrapper with MariaDB result cache.
 *
 * Usage:
 *   $vt = new \MailReview\VirusTotal\VtService($pdo, $config['vt_api_key'], $dataDir);
 *   $r  = $vt->check($sha256, $filePath);
 *   // $r = ['status' => 'clean'|'infected'|'pending'|'error'|'disabled', 'positives' => int]
 */
declare(strict_types=1);

namespace MailReview\VirusTotal;

class VtService
{
    private \PDO    $pdo;
    private string  $apiKey;
    private string  $dataDir;

    public function __construct(\PDO $pdo, string $apiKey, string $dataDir)
    {
        $this->pdo     = $pdo;
        $this->apiKey  = $apiKey;
        $this->dataDir = rtrim($dataDir, '/');
    }

    /**
     * Check a file against VirusTotal.
     * Returns ['status' => string, 'positives' => int].
     */
    public function check(string $sha256, string $filePath): array
    {
        if ($this->apiKey === '') {
            return ['status' => 'disabled', 'positives' => 0];
        }

        $cached = $this->getCached($sha256);

        // Non-pending cache hit → return immediately
        if ($cached !== null && $cached['status'] !== 'pending') {
            return ['status' => $cached['status'], 'positives' => (int)$cached['positives']];
        }

        // Pending with a scan_id → poll VT for the result
        if ($cached !== null && $cached['scan_id'] !== '') {
            return $this->pollScan($sha256, $cached['scan_id']);
        }

        // Cache miss → check VT by hash first (avoids uploading known files)
        $report = $this->fileReport($sha256);
        if ($report === null) {
            $this->upsert($sha256, 'error', '', 0);
            return ['status' => 'error', 'positives' => 0];
        }
        if ((int)($report['response_code'] ?? 0) === 1) {
            return $this->storeReport($sha256, $report);
        }

        // Hash unknown to VT → upload the file
        if (!is_file($filePath)) {
            $this->upsert($sha256, 'error', '', 0);
            return ['status' => 'error', 'positives' => 0];
        }
        $scan = $this->fileScan($filePath);
        if ($scan === null || empty($scan['scan_id'])) {
            $this->upsert($sha256, 'error', '', 0);
            return ['status' => 'error', 'positives' => 0];
        }
        $this->upsert($sha256, 'pending', (string)$scan['scan_id'], 0);
        return ['status' => 'pending', 'positives' => 0];
    }

    // ── Private helpers ────────────────────────────────────────────────────

    private function getCached(string $sha256): ?array
    {
        $st = $this->pdo->prepare(
            'SELECT status, scan_id, positives FROM vt_cache WHERE sha256 = ?'
        );
        $st->execute([$sha256]);
        return $st->fetch(\PDO::FETCH_ASSOC) ?: null;
    }

    private function pollScan(string $sha256, string $scanId): array
    {
        $report = $this->fileReport($sha256);
        if ($report === null || (int)($report['response_code'] ?? 0) !== 1) {
            return ['status' => 'pending', 'positives' => 0];
        }
        return $this->storeReport($sha256, $report);
    }

    private function storeReport(string $sha256, array $report): array
    {
        $positives = (int)($report['positives'] ?? 0);
        $status    = $positives === 0 ? 'clean' : 'infected';
        $scanId    = (string)($report['scan_id'] ?? '');
        $this->upsert($sha256, $status, $scanId, $positives);
        return ['status' => $status, 'positives' => $positives];
    }

    private function upsert(string $sha256, string $status, string $scanId, int $positives): void
    {
        $this->pdo->prepare(
            'INSERT INTO vt_cache (sha256, status, scan_id, positives, scanned_at)
             VALUES (?, ?, ?, ?, NOW())
             ON DUPLICATE KEY UPDATE
               status     = VALUES(status),
               scan_id    = VALUES(scan_id),
               positives  = VALUES(positives),
               scanned_at = NOW()'
        )->execute([$sha256, $status, $scanId, $positives]);
    }

    private function fileReport(string $sha256): ?array
    {
        $url = 'https://www.virustotal.com/vtapi/v2/file/report?'
             . http_build_query(['apikey' => $this->apiKey, 'resource' => $sha256]);
        $ctx = stream_context_create(['http' => ['timeout' => 15]]);
        $raw = @file_get_contents($url, false, $ctx);
        if ($raw === false) return null;
        $data = json_decode($raw, true);
        return is_array($data) ? $data : null;
    }

    private function fileScan(string $filePath): ?array
    {
        $boundary = '----VTBound' . bin2hex(random_bytes(8));
        $content  = file_get_contents($filePath);
        if ($content === false) return null;

        $body = "--{$boundary}\r\n"
              . "Content-Disposition: form-data; name=\"apikey\"\r\n\r\n"
              . $this->apiKey . "\r\n"
              . "--{$boundary}\r\n"
              . 'Content-Disposition: form-data; name="file"; filename="' . basename($filePath) . '"' . "\r\n"
              . "Content-Type: application/octet-stream\r\n\r\n"
              . $content . "\r\n"
              . "--{$boundary}--";

        $ctx = stream_context_create([
            'http' => [
                'method'  => 'POST',
                'header'  => "Content-Type: multipart/form-data; boundary={$boundary}\r\n",
                'content' => $body,
                'timeout' => 30,
            ],
        ]);
        $raw = @file_get_contents('https://www.virustotal.com/vtapi/v2/file/scan', false, $ctx);
        if ($raw === false) return null;
        $data = json_decode($raw, true);
        return is_array($data) ? $data : null;
    }
}
