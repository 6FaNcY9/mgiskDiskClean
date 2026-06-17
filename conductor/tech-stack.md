# Technology Stack: mgiskDiskClean

## Languages
- **PHP 8.3**: Used for the web-based review application and administrative interface.
- **Python 3.11+**: Used for the mailbox processing pipeline, PDF generation, and deduplication logic.

## Frameworks & Libraries
- **Backend (PHP)**: No framework. Custom-built class library for session management, database access, and UI rendering.
- **Backend (Python)**: Uses standard Python library with `setuptools` for packaging.
- **Frontend**: Vanilla PHP/HTML/JS. No frontend framework (like React or Vue) is used.

## Data Storage
- **MySQL**: Primary database for storing imported report data, user accounts, and cleanup decisions.
- **SQLite**: Used by the Python pipeline for local mailbox indexing and state management.

## Infrastructure & Runtime
- **Docker Compose**: Supported local runtime for the entire stack (PHP app, MySQL database).
- **Apache**: Web server for serving the PHP application.
- **Nix (devenv)**: Optional developer shell for managing dependencies and local commands.
- **FTP Deployment**: The system is designed to be deployable to standard shared hosting environments via FTP.

## Development & Quality Assurance
- **Pytest**: Used for unit and integration testing of the Python pipeline.
- **Ruff**: Linting and formatting for Python code.
- **Docker/QA Scripts**: Custom shell scripts for running migrations, imports, and quality checks within the Docker environment.
