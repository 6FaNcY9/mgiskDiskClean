# Running the Prototype on Windows

## Prerequisites

1. Install [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/) and start it
2. Open **PowerShell** or **Windows Terminal**, navigate to the project folder

```powershell
cd path\to\mrijaPageClean
```

## Quick Start

**1. Start the database:**
```powershell
docker compose up -d db
```

**2. Build the app image (first time, ~2-3 min):**
```powershell
docker compose build app
```

**3. Run database migrations:**
```powershell
docker compose run --rm app php web/src/cli/migrate.php
```

**4. Run all unit tests:**
```powershell
docker compose run --rm app python -m pytest tests/ -v
```

**5. Run end-to-end QA (fixture-based, no server needed):**
```powershell
docker compose run --rm app bash docker/qa-archive-docker.sh
```

Expected final output: `==> [qa-archive] ALL STEPS PASSED`

---

## Syncing Real Mailboxes

After running QA, you can sync real mailboxes (requires SSH access to the mail server):

```powershell
# Copy your SSH key into the container first (or mount ~/.ssh)
docker compose run --rm -v $HOME\.ssh:/root/.ssh:ro app \
  python -m maildir_report.sync_all --mailboxes-file data/mailboxes.txt

# Then import into MySQL
docker compose run --rm app php web/src/cli/import_archive.php
```

## Searching the Archive

```powershell
# Search all mailboxes
docker compose run --rm app php web/src/cli/search_archive.php --query "invoice"

# Search one mailbox
docker compose run --rm app php web/src/cli/search_archive.php --query "invoice" --mailbox gabriel.hangel
```

## Common Commands

| Command | Description |
|---------|-------------|
| `docker compose up -d db` | Start MariaDB in background |
| `docker compose build app` | Rebuild app image after code changes |
| `docker compose run --rm app bash` | Open interactive shell |
| `docker compose down` | Stop all services |
| `docker compose down -v` | Stop + delete database volume |

## Data Persistence

- Mailbox data: `./data/` (mounted into container)
- Database: Docker volume `mariadb_data` (persists across restarts)
- To back up the database: `docker compose exec db mysqldump -u mailreview -pmailreview mailreview > backup.sql`
