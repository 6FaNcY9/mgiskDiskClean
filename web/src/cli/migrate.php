<?php
/**
 * web/src/cli/migrate.php — Migration runner CLI.
 *
 * Runs pending SQL migrations from web/migrations/ against the configured MySQL.
 * Called by the devenv `db-migrate` script.
 *
 * Usage: php web/src/cli/migrate.php [--socket <path>] [--config <path>]
 */

declare(strict_types=1);

// ── CLI only ───────────────────────────────────────────────────────────────
if (PHP_SAPI !== 'cli') {
    fwrite(STDERR, "This script must be run from the command line.\n");
    exit(1);
}

// ── Parse args ─────────────────────────────────────────────────────────────
$opts = getopt('', ['socket:', 'config:', 'help', 'h']);
if (isset($opts['help']) || isset($opts['h'])) {
    fwrite(STDOUT, <<<USAGE
    Usage: php web/src/cli/migrate.php [OPTIONS]

      Run pending SQL migrations in web/migrations/ against the configured
      MariaDB/MySQL instance. Tracks applied migrations in a schema_migrations table.

    Options:
      --socket <path>   MariaDB Unix socket (overrides config; default: DEVENV_STATE/mysql.sock)
      --config <path>   Path to local.php config (default: web/config/local.php)
      --help            Show this message and exit

    Exit codes:
      0  All migrations applied (or nothing to do)
      1  Error

    USAGE);
    exit(0);
}

$scriptDir  = dirname(__DIR__, 2); // web/
$configPath = $opts['config'] ?? ($scriptDir . '/config/local.php');

if (!is_file($configPath)) {
    fwrite(STDERR, "ERROR: Config not found: $configPath\n");
    fwrite(STDERR, "  Copy web/config/local.php.example -> web/config/local.php\n");
    exit(1);
}

/** @var array<string,mixed> $config */
$config = require $configPath;
$dbCfg  = $config['db'] ?? [];

// Allow --socket CLI override
$socket = $opts['socket'] ?? $dbCfg['socket'] ?? (getenv('DEVENV_STATE') . '/mysql.sock');

// ── Build DSN ──────────────────────────────────────────────────────────────
if ($socket && file_exists($socket)) {
    $dsn = "mysql:unix_socket=$socket;dbname={$dbCfg['dbname']};charset={$dbCfg['charset']}";
} else {
    $host = $dbCfg['host'] ?? '127.0.0.1';
    $port = $dbCfg['port'] ?? 3306;
    $dsn  = "mysql:host=$host;port=$port;dbname={$dbCfg['dbname']};charset={$dbCfg['charset']}";
}

// ── Connect ────────────────────────────────────────────────────────────────
try {
    $pdo = new PDO($dsn, $dbCfg['user'] ?? '', $dbCfg['password'] ?? '', [
        PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES   => false,
    ]);
} catch (PDOException $e) {
    fwrite(STDERR, "ERROR: DB connection failed: " . $e->getMessage() . "\n");
    exit(1);
}

// ── Bootstrap migrations tracking table ───────────────────────────────────
$pdo->exec(<<<SQL
    CREATE TABLE IF NOT EXISTS schema_migrations (
        migration  VARCHAR(255) NOT NULL PRIMARY KEY,
        applied_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
SQL);

// ── Discover migration files ───────────────────────────────────────────────
$migrationsDir = $scriptDir . '/migrations';
$files = glob($migrationsDir . '/*.sql');
if ($files === false) {
    fwrite(STDERR, "ERROR: Cannot read migrations directory: $migrationsDir\n");
    exit(1);
}
sort($files);

// ── Fetch already-applied ──────────────────────────────────────────────────
$applied = $pdo->query('SELECT migration FROM schema_migrations ORDER BY migration')
               ->fetchAll(PDO::FETCH_COLUMN);
$appliedSet = array_flip($applied);

// ── Apply pending ──────────────────────────────────────────────────────────
$count = 0;
foreach ($files as $file) {
    $name = basename($file);
    if (isset($appliedSet[$name])) {
        fwrite(STDOUT, "  [skip] $name\n");
        continue;
    }
    $sql = file_get_contents($file);
    if ($sql === false) {
        fwrite(STDERR, "ERROR: Cannot read $file\n");
        exit(1);
    }
    try {
        $pdo->exec($sql);
        $stmt = $pdo->prepare('INSERT INTO schema_migrations (migration) VALUES (?)');
        $stmt->execute([$name]);
        fwrite(STDOUT, "  [apply] $name\n");
        $count++;
    } catch (PDOException $e) {
        fwrite(STDERR, "ERROR: Migration $name failed: " . $e->getMessage() . "\n");
        exit(1);
    }
}

fwrite(STDOUT, "==> Migrations complete. Applied: $count\n");
exit(0);
