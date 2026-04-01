<?php
/**
 * web/src/Download/DownloadException.php — Typed exception for download errors.
 *
 * Carries an HTTP status code so the route handler can return the right
 * response without duplicating decision logic.
 */

declare(strict_types=1);

namespace MailReview\Download;

class DownloadException extends \RuntimeException
{
    public function __construct(
        string $message,
        private readonly int $httpStatus = 404,
        ?\Throwable $previous = null
    ) {
        parent::__construct($message, 0, $previous);
    }

    public function getHttpStatus(): int
    {
        return $this->httpStatus;
    }
}
