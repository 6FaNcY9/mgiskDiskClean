<?php
/**
 * web/src/Auth/SessionManager.php — Session hardening and role management.
 *
 * Enforces OWASP session management recommendations:
 *   - HttpOnly, Secure, SameSite=Strict cookie flags.
 *   - session_regenerate_id(true) after successful login.
 *   - Idle timeout enforced from $_SESSION['_last_active'].
 *   - Roles: 'admin' | 'coworker'.
 *
 * Usage:
 *   $sm = new SessionManager($config['session']);
 *   $sm->start();
 *   $sm->requireAuth('/login.php');
 *   $sm->requireRole('admin', '/login.php');
 */

declare(strict_types=1);

namespace MailReview\Auth;

class SessionManager
{
    private const ROLE_ADMIN    = 'admin';
    private const ROLE_COWORKER = 'coworker';

    /** Idle timeout (seconds); overridable via config 'lifetime'. */
    private int $lifetime;

    /** Session cookie name. */
    private string $cookieName;

    public function __construct(array $sessionConfig = [])
    {
        $this->cookieName = $sessionConfig['name']     ?? 'mailreview_session';
        $this->lifetime   = (int)($sessionConfig['lifetime'] ?? 7200);
    }

    /**
     * Start or resume the session with hardened cookie parameters.
     *
     * Must be called before any output is sent (before headers).
     * Safe to call multiple times — no-ops if session already active.
     */
    public function start(): void
    {
        if (session_status() === PHP_SESSION_ACTIVE) {
            return;
        }

        // Set cookie params BEFORE session_start().
        // Secure=true: cookie only sent over HTTPS.
        // In local dev (HTTP), set Secure=false when behind plain HTTP server.
        // We detect this via the X-Forwarded-Proto header or the HTTPS env var.
        $isHttps = (
            (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off')
            || (!empty($_SERVER['HTTP_X_FORWARDED_PROTO']) && $_SERVER['HTTP_X_FORWARDED_PROTO'] === 'https')
        );

        session_set_cookie_params([
            'lifetime' => 0,              // Expire when browser closes (idle handled server-side)
            'path'     => '/',
            'domain'   => '',             // Current domain only
            'secure'   => $isHttps,       // HTTPS-only in production
            'httponly' => true,           // Block JS access
            'samesite' => 'Strict',       // CSRF mitigation layer
        ]);

        session_name($this->cookieName);
        session_start();

        // Enforce idle timeout
        $now = time();
        if (isset($_SESSION['_last_active']) && ($now - $_SESSION['_last_active']) > $this->lifetime) {
            // Session expired — destroy and restart clean
            $this->destroySession();
            session_start();
        }

        $_SESSION['_last_active'] = $now;
    }

    /**
     * Authenticate a user with a password and establish a session.
     *
     * @param  string $role         'admin' or 'coworker'
     * @param  string $password     Plaintext password to verify
     * @param  string $hash         BCrypt hash from config
     * @param  string $displayName  Display name (mandatory for coworker; optional for admin)
     * @return bool   True if login succeeded
     */
    public function login(string $role, string $password, string $hash, string $displayName = ''): bool
    {
        if ($hash === '' || !password_verify($password, $hash)) {
            return false;
        }

        // Regenerate session ID to prevent session fixation attacks.
        session_regenerate_id(true);

        $_SESSION['auth_role']         = $role;
        $_SESSION['auth_display_name'] = trim($displayName);
        $_SESSION['_last_active']      = time();

        return true;
    }

    /**
     * Destroy the session (logout).
     */
    public function logout(): void
    {
        $this->destroySession();
    }

    /**
     * Check if the current user is authenticated (any role).
     */
    public function isAuthenticated(): bool
    {
        return isset($_SESSION['auth_role']) && $_SESSION['auth_role'] !== '';
    }

    /**
     * Get the current user's role ('admin' | 'coworker' | '').
     */
    public function getRole(): string
    {
        return (string)($_SESSION['auth_role'] ?? '');
    }

    /**
     * Get the current user's display name.
     */
    public function getDisplayName(): string
    {
        return (string)($_SESSION['auth_display_name'] ?? '');
    }

    /**
     * Redirect to the given URL if the user is not authenticated, then exit.
     *
     * @param string $loginUrl  URL to redirect to (default: /login.php)
     */
    public function requireAuth(string $loginUrl = '/login.php'): void
    {
        if (!$this->isAuthenticated()) {
            header('Location: ' . $loginUrl, true, 302);
            exit(0);
        }
    }

    /**
     * Require a specific role; sends 403 and exits if role does not match.
     *
     * @param string $role      'admin' or 'coworker'
     * @param string $loginUrl  Redirect to this URL if not authenticated at all
     */
    public function requireRole(string $role, string $loginUrl = '/login.php'): void
    {
        $this->requireAuth($loginUrl);
        if ($this->getRole() !== $role) {
            http_response_code(403);
            header('Content-Type: text/plain; charset=utf-8');
            echo 'Forbidden: insufficient privileges.';
            exit(0);
        }
    }

    // ── Private helpers ────────────────────────────────────────────────────────

    private function destroySession(): void
    {
        // Clear all session data
        $_SESSION = [];

        // Delete the session cookie
        if (ini_get('session.use_cookies')) {
            $params = session_get_cookie_params();
            setcookie(
                session_name(),
                '',
                time() - 42000,
                $params['path'],
                $params['domain'],
                $params['secure'],
                $params['httponly']
            );
        }

        session_destroy();
    }
}
