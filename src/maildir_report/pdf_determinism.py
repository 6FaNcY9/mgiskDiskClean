"""
pdf_determinism.py — ReportLab deterministic PDF configuration for maildir_report.

Design rules
------------
- Call configure_deterministic_pdf() BEFORE creating any ReportLab canvas or
  Platypus document object.  The flag is process-global and affects all
  subsequent PDF generation in the same process.
- Idempotent: calling it multiple times is safe and has no side effects.
- The sole mechanism is ``rl_config.invariant = True``, which instructs
  ReportLab to suppress all non-deterministic output:
    - CreationDate and ModDate timestamps
    - Random PDF /ID array
    - Any other generation-time entropy

Public API
----------
configure_deterministic_pdf() -> None
    Set ``rl_config.invariant = True``.  Must be called before any PDF
    generation to guarantee byte-for-byte reproducibility.
"""

from __future__ import annotations

from reportlab import rl_config


def configure_deterministic_pdf() -> None:
    """Set ReportLab to deterministic (invariant) mode.

    After this call, two PDF generations with identical content and identical
    metadata will produce byte-for-byte identical output (verified via SHA-256).

    This function MUST be called before creating any ``canvas.Canvas`` or
    ``platypus.BaseDocTemplate`` instance.

    It is idempotent: calling it multiple times has no side effects.

    Implementation note
    -------------------
    ``rl_config.invariant = True`` is the official ReportLab mechanism for
    deterministic output.  It suppresses:
    - ``CreationDate`` and ``ModDate`` PDF metadata timestamps
    - Random PDF ``/ID`` array values
    - Any other process-time entropy injected into the output stream

    References
    ----------
    - https://stackoverflow.com/questions/79593400/create-reproducible-pdf-using-reportlab
    - ReportLab RML userguide: ``<document invariant="1">``
    """
    rl_config.invariant = True
