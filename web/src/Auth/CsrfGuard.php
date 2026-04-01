<?php
/**
 * web/src/Auth/CsrfGuard.php — CSRF synchronizer token pattern.
 *
 * Implements the synchronizer token pattern per OWASP CSRF prevention:
 *   https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html
 *
 * Token lifecycle:
 *   1. generateToken()  — create and store a cryptographically random token in $_SESSION.
 *   2. getToken()       — retrieve the current token for embedding in forms/JSON.
 *   3. validateToken()  — compare submitted value against stored token using hash_equals().
 *
 * Security properties:
 *   - Tokens are 32 bytes of CSPRNG output (base64url-encoded = 43 chars).
 *   - Comparison uses hash_equals() to prevent timing attacks.
 *   - Token is stored in session under '_csrf_token' key.
 *   - validateToken() does NOT rotate the token (avoids breaking multi-tab use).
 *     The token rotates on login (session_regenerate_id) and logout only.
 *
 * Usage in a form:
 *   <?= '<input type="hidden" name="csrf_token" value="' . htmlspecialchars($csrf->getToken()) . '">' ?>
 *
 * Usage in validation:
 *   if (!$csrf->validateToken($_POST['csrf_token'] ?? '')) {
 *       http_response_code(403);
 *       exit('CSRF validation failed.');
 *   }
 */

declare(strict_types=1);

namespace MailReview\Auth;

class CsrfGuard
{
    private const SESSION_KEY = '_csrf_token';
    private const TOKEN_BYTES = 32;

    /**
     * Generate a new CSRF token and store it in the session.
     *
     * Replaces any existing token. Call this once per page load for form pages,
     * or rely on getToken() which auto-generates if absent.
     *
     * @return string  The new token (base64url-encoded, 43 chars).
     */
    public function generateToken(): string
    {
        $token = $this->encodeToken(random_bytes(self::TOKEN_BYTES));
        $_SESSION[self::SESSION_KEY] = $token;
        return $token;
    }

    /**
     * Return the current CSRF token, generating one if not yet set.
     *
     * @return string  The token (base64url-encoded, 43 chars).
     */
    public function getToken(): string
    {
        if (empty($_SESSION[self::SESSION_KEY])) {
            return $this->generateToken();
        }
        return (string)$_SESSION[self::SESSION_KEY];
    }

    /**
     * Validate a submitted CSRF token against the session-stored token.
     *
     * Uses hash_equals() to prevent timing attacks.
     *
     * @param  string $submitted  The token submitted with the request.
     * @return bool   True if valid, false otherwise.
     */
    public function validateToken(string $submitted): bool
    {
        $stored = (string)($_SESSION[self::SESSION_KEY] ?? '');
        if ($stored === '' || $submitted === '') {
            return false;
        }
        return hash_equals($stored, $submitted);
    }

    /**
     * Enforce CSRF on the current request; sends HTTP 403 and exits on failure.
     *
     * Reads the token from POST field 'csrf_token', or from the
     * 'X-CSRF-Token' request header (for JSON/AJAX requests).
     */
    public function enforce(): void
    {
        $submitted = $_POST['csrf_token']
            ?? $_SERVER['HTTP_X_CSRF_TOKEN']
            ?? '';
        if (!$this->validateToken((string)$submitted)) {
            http_response_code(403);
            header('Content-Type: text/plain; charset=utf-8');
            echo 'CSRF validation failed.';
            exit(0);
        }
    }

    // ── Private helpers ────────────────────────────────────────────────────────

    /** Encode bytes as base64url (URL-safe base64 without padding). */
    private function encodeToken(string $bytes): string
    {
        return rtrim(strtr(base64_encode($bytes), '+/', '-_'), '=');
    }
}
