<?php
declare(strict_types=1);

$cfgPath = __DIR__ . '/../../config/local.php';
if (!is_file($cfgPath)) { http_response_code(500); exit; }
$config = require $cfgPath;

spl_autoload_register(function (string $c): void {
    $map = [
        'MailReview\\Auth\\SessionManager' => __DIR__ . '/../../src/Auth/SessionManager.php',
        'MailReview\\Auth\\CsrfGuard'      => __DIR__ . '/../../src/Auth/CsrfGuard.php',
    ];
    if (isset($map[$c])) require_once $map[$c];
});
$sm = new \MailReview\Auth\SessionManager($config['session'] ?? []);
$sm->start();
$authEnabled = $config['auth']['enabled'] ?? true;
if ($authEnabled) {
    if (!$sm->isAuthenticated()) { http_response_code(401); exit; }
    if ($sm->getRole() !== 'admin') { http_response_code(403); exit; }
}
if ($_SERVER['REQUEST_METHOD'] !== 'POST') { http_response_code(405); exit; }

header('Content-Type: application/json; charset=utf-8');

$csrf = new \MailReview\Auth\CsrfGuard();
if ($authEnabled) {
    $csrf->enforce();
}

$updateUrl = rtrim((string)($config['update_server_url'] ?? ''), '/');
if ($updateUrl === '') {
    http_response_code(503);
    echo json_encode(['ok' => false, 'error' => 'no_update_server']);
    exit;
}

// Always fetch the manifest from the configured update server. The browser may
// display a cached copy, but it must not choose what artifact the server imports.
$ctx = stream_context_create(['http' => ['timeout' => 8]]);
$raw = @file_get_contents($updateUrl . '/updates/manifest.json', false, $ctx);
$manifest = $raw ? json_decode($raw, true) : null;

$dbManifest = is_array($manifest['database'] ?? null) ? $manifest['database'] : $manifest;
if (!is_array($manifest) || empty($dbManifest['filename']) || empty($dbManifest['sha256'])) {
    http_response_code(422);
    echo json_encode(['ok' => false, 'error' => 'no_manifest']);
    exit;
}

$filename = basename((string)($dbManifest['filename'] ?? ''));
if (!preg_match('/^mrija-[\dT]+Z\.sql\.gz$/', $filename)) {
    http_response_code(422);
    echo json_encode(['ok' => false, 'error' => 'invalid_filename']);
    exit;
}

$dumpUrl = $updateUrl . '/updates/' . $filename;
$tmpFile = sys_get_temp_dir() . '/' . $filename;
$attachmentsManifest = is_array($manifest['attachments'] ?? null) ? $manifest['attachments'] : null;
$attachmentsFile = '';
$attachmentsTmp = '';

function downloadAndVerify(string $url, string $tmpFile, string $expectedSha256, string $errorPrefix): ?array
{
    $ctx = stream_context_create(['http' => ['timeout' => 1800]]);
    $in = @fopen($url, 'rb', false, $ctx);
    if ($in === false) {
        return ['status' => 502, 'error' => $errorPrefix . '_download_failed'];
    }
    $out = @fopen($tmpFile, 'wb');
    if ($out === false) {
        fclose($in);
        return ['status' => 500, 'error' => $errorPrefix . '_temp_failed'];
    }
    stream_copy_to_stream($in, $out);
    fclose($in);
    fclose($out);

    $actual = hash_file('sha256', $tmpFile);
    if (!hash_equals($expectedSha256, (string)$actual)) {
        @unlink($tmpFile);
        return ['status' => 422, 'error' => $errorPrefix . '_sha256_mismatch'];
    }
    return null;
}

set_time_limit(3600);

// Download and verify DB first.
$err = downloadAndVerify($dumpUrl, $tmpFile, (string)($dbManifest['sha256'] ?? ''), 'db');
if ($err !== null) {
    http_response_code((int)$err['status']);
    echo json_encode(['ok' => false, 'error' => $err['error']]);
    exit;
}

// Download and verify attachments before applying DB, so partial updates are less likely.
if ($attachmentsManifest !== null) {
    $attachmentsFile = basename((string)($attachmentsManifest['filename'] ?? ''));
    if (!preg_match('/^mrija-attachments-[\dT]+Z\.tar\.zst$/', $attachmentsFile)) {
        @unlink($tmpFile);
        http_response_code(422);
        echo json_encode(['ok' => false, 'error' => 'invalid_attachments_filename']);
        exit;
    }
    if (($attachmentsManifest['format'] ?? 'tar.zst') !== 'tar.zst') {
        @unlink($tmpFile);
        http_response_code(422);
        echo json_encode(['ok' => false, 'error' => 'unsupported_attachments_format']);
        exit;
    }
    $attachmentsTmp = sys_get_temp_dir() . '/' . $attachmentsFile;
    $attachmentsUrl = $updateUrl . '/updates/' . $attachmentsFile;
    $err = downloadAndVerify($attachmentsUrl, $attachmentsTmp, (string)($attachmentsManifest['sha256'] ?? ''), 'attachments');
    if ($err !== null) {
        @unlink($tmpFile);
        http_response_code((int)$err['status']);
        echo json_encode(['ok' => false, 'error' => $err['error']]);
        exit;
    }
}

$db   = $config['db'] ?? [];
$host = escapeshellarg($db['host'] ?? '127.0.0.1');
$port = (int)($db['port'] ?? 3306);
$user = escapeshellarg($db['user'] ?? 'mailreview');
$pass = escapeshellarg($db['password'] ?? '');
$name = escapeshellarg($db['dbname'] ?? 'mailreview');

$cmd = "zcat " . escapeshellarg($tmpFile)
     . " | mysql -h $host -P $port -u $user -p$pass $name 2>&1";

$output   = [];
$exitCode = 0;
exec($cmd, $output, $exitCode);
@unlink($tmpFile);

if ($exitCode !== 0) {
    if ($attachmentsTmp !== '') { @unlink($attachmentsTmp); }
    http_response_code(500);
    echo json_encode(['ok' => false, 'error' => 'mysql_failed', 'detail' => implode("\n", $output)]);
    exit;
}

$attachmentsInstalled = false;
if ($attachmentsTmp !== '') {
    $dataDir = rtrim((string)($config['data_dir'] ?? (getenv('DEVENV_ROOT') ? getenv('DEVENV_ROOT') . '/data' : '/app/data')), '/');
    if (!is_dir($dataDir) && !mkdir($dataDir, 0755, true)) {
        @unlink($attachmentsTmp);
        http_response_code(500);
        echo json_encode(['ok' => false, 'error' => 'data_dir_failed']);
        exit;
    }

    $listCmd = "tar --zstd -tf " . escapeshellarg($attachmentsTmp) . " 2>&1";
    $archiveMembers = [];
    $listExit = 0;
    exec($listCmd, $archiveMembers, $listExit);
    if ($listExit !== 0) {
        @unlink($attachmentsTmp);
        http_response_code(500);
        echo json_encode(['ok' => false, 'error' => 'attachments_list_failed', 'detail' => implode("\n", $archiveMembers)]);
        exit;
    }
    foreach ($archiveMembers as $member) {
        $member = trim((string)$member);
        if (
            $member === ''
            || $member[0] === '/'
            || str_contains($member, '\\')
            || str_contains($member, '../')
            || str_contains($member, '/..')
            || !preg_match('#^mailboxes/[a-zA-Z0-9._-]+/attachments(?:/|$)#', $member)
        ) {
            @unlink($attachmentsTmp);
            http_response_code(422);
            echo json_encode(['ok' => false, 'error' => 'unsafe_attachments_archive']);
            exit;
        }
    }

    $extractCmd = "tar --zstd --no-same-owner -xf "
        . escapeshellarg($attachmentsTmp)
        . " -C "
        . escapeshellarg($dataDir)
        . " 2>&1";
    $extractOutput = [];
    $extractExit = 0;
    exec($extractCmd, $extractOutput, $extractExit);
    @unlink($attachmentsTmp);
    if ($extractExit !== 0) {
        http_response_code(500);
        echo json_encode(['ok' => false, 'error' => 'attachments_extract_failed', 'detail' => implode("\n", $extractOutput)]);
        exit;
    }
    $attachmentsInstalled = true;
}

// Clear the manifest session cache so next check-update returns fresh data
unset($_SESSION['_update_manifest_cache'], $_SESSION['_update_manifest_ts']);

echo json_encode([
    'ok' => true,
    'version' => $manifest['version'],
    'attachments' => $attachmentsInstalled,
]);
