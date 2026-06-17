# Initial Concept
Mailbox Review App: A self-contained tool for scanning Maildirs, generating PDF reports, and reviewing email cleanup decisions via a web interface.

# Product Definition: mgiskDiskClean (Mailbox Review App)

## Vision
To provide a secure, self-contained system for auditing and cleaning up large email mailboxes. The system bridges the gap between technical mailbox processing (scanning/deduplication) and human-led decision making (keep/delete/unsure) through a streamlined web interface and deterministic reporting.

## Core Features
- **Mailbox Scanning & Pipeline**: A Python-based engine that processes Maildirs, extracts attachments, and generates deterministic PDF reports.
- **Deduplication**: Intelligent identification of duplicate email groups to streamline the review process.
- **Web-Based Review UI**: A PHP application allowing coworkers to review emails, apply decisions, and add notes.
- **Admin Management**: Secure dashboard for tracking review progress and exporting final cleanup decisions.
- **Deterministic Reporting**: Every scan produces a manifest with SHA-256 hashes to ensure auditability and integrity of the review process.
- **Flexible Deployment**: Supports local development via Docker Compose and production deployment via FTP to shared hosting environments.

## User Personas
- **Technical Operator**: Runs the Python pipeline, manages migrations, and imports processed mailbox data into the web application.
- **Coworker (Reviewer)**: Logs into the web app to review emails in assigned mailboxes and makes cleanup decisions.
- **Administrator**: Oversees the entire process, monitors reviewer progress, and exports final decisions for offline application.

## Key Constraints
- **Security & Privacy**: Email content is sensitive. The app requires strict access control, secure credential management (via .env and local.php), and avoids public exposure of configuration files.
- **No Heavy Dependencies**: The PHP application is built without a framework (no Composer) to ensure maximum compatibility with restricted hosting environments.
- **Deterministic Output**: Reports and IDs must be stable across multiple runs to maintain the integrity of the audit trail.
