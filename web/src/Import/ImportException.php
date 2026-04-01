<?php
/**
 * web/src/Import/ImportException.php — Typed exception for report import errors.
 *
 * Carry an HTTP status code so the route handler can return the right response
 * without duplicating the decision logic.
 */

declare(strict_types=1);

namespace MailReview\Import;

class ImportException extends \RuntimeException
{
    public function __construct(
        string $message,
        private readonly int $httpStatus = 400,
        ?\Throwable $previous = null
    ) {
        parent::__construct($message, 0, $previous);
    }

    public function getHttpStatus(): int
    {
        return $this->httpStatus;
    }
}
