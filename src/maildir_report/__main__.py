"""Allow ``python -m maildir_report`` invocation."""

import sys

from maildir_report.cli import main

sys.exit(main())
