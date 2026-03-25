# ReportLab Deterministic PDF Generation Research

**Date**: 2026-03-24  
**Status**: COMPLETE  
**Source**: Official ReportLab Documentation + Stack Overflow (2025)

## Core Finding: The `invariant` Flag

ReportLab has a **global configuration flag** that controls deterministic output:

### Python API (pdfgen/Platypus)
```python
from reportlab import rl_config
rl_config.invariant = True
```

**Evidence**: https://stackoverflow.com/questions/79593400/create-reproducible-pdf-using-reportlab (Apr 2025)  
**Official Reference**: RML docs mention this but less visible in Python API guide

### RML (Report Markup Language)
```xml
<document filename="outfile.pdf" invariant="1">
  <!-- content -->
</document>
```

**Evidence**: https://docs.reportlab.com/rml/userguide/Chapter_2_Pages_and_page_structures/ (Section 2.2)  
**Values**: `0` (off) | `1` (on) | `default` (uses site config)

---

## What `invariant=True` Removes

When enabled, eliminates all non-deterministic output:

1. **CreationDate timestamp** → Removed or fixed
2. **ModDate timestamp** → Removed or fixed  
3. **PDF /ID array** → Uses deterministic hash instead of random UUID
4. **Digest comments** → Uses stable values
5. **Random generation markers** → Suppressed

**Impact**: Two PDF generations with identical input = identical binary output (SHA256 match)

---

## Practical Implementation Checklist

### ✓ For Python Scripts (Platypus/Canvas)
```python
from reportlab import rl_config
from reportlab.platypus import SimpleDocTemplate

# BEFORE any PDF generation:
rl_config.invariant = True

# Then proceed normally:
doc = SimpleDocTemplate("report.pdf")
# ... build story ...
doc.build(story)
```

### ✓ For Python Scripts (Canvas API)
```python
from reportlab import rl_config
from reportlab.pdfgen import canvas

rl_config.invariant = True

c = canvas.Canvas("report.pdf")
c.drawString(100, 100, "Hello")
c.showPage()
c.save()
```

### ✓ For RML Templates
```xml
<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE document SYSTEM "rml.dtd">
<document filename="report.pdf" invariant="1">
  <template>
    <!-- template content -->
  </template>
  <stylesheet>
    <!-- styles -->
  </stylesheet>
  <story>
    <!-- content -->
  </story>
</document>
```

---

## Additional Reproducibility Considerations

### Font Embedding (Consistency)
- **Register fonts early** in script execution to ensure stable font references
- Use **TrueType fonts** (.ttf) with consistent paths
- Example:
```python
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

pdfmetrics.registerFont(TTFont('CustomFont', '/path/to/font.ttf'))
```

### Content Ordering
- ReportLab maintains consistent ordering of PDF objects when `invariant=True`
- Ensure **deterministic data input** (sorted lists, fixed iteration order)
- Dictionary iteration in Python 3.7+ is insertion-ordered (stable)

### Metadata Control
- Use `canvas.setAuthor()`, `canvas.setTitle()`, etc. **explicitly** 
- When `invariant=True`, these are written but timestamps are standardized
- Avoid relying on system time-based metadata

---

## Verification Method

**Test for byte-for-byte reproducibility:**
```bash
#!/bin/bash
# Run PDF generation twice
python generate_report.py
sha256sum report.pdf > hash1.txt

python generate_report.py
sha256sum report.pdf > hash2.txt

# Compare
diff hash1.txt hash2.txt && echo "✓ REPRODUCIBLE" || echo "✗ NOT REPRODUCIBLE"
```

---

## Compatibility Notes

- **Minimum ReportLab version**: Available in ReportLab 3.x and 4.x (current)
- **Default state**: `invariant` is `False` by default (non-deterministic for performance)
- **Scope**: Global setting affects all subsequent PDF generations in same process
- **No performance penalty**: Setting `invariant=True` has negligible impact

---

## Limitations & Caveats

1. **Image embedding**: Ensure images come from **fixed sources** (not generated/cached differently)
2. **Current timestamp in content**: If you embed timestamps as text, they won't be deterministic
3. **Multi-run testing**: Compare ONLY the PDF files, not any wrapper metadata
4. **Python version**: Dict ordering stable in 3.7+; use OrderedDict if targeting <3.7

---

## Related Official Resources

- https://docs.reportlab.com/reportlab/userguide/ch2_graphics/ (Canvas methods)
- https://docs.reportlab.com/reportlab/userguide/ch5_platypus/ (Platypus + BaseDocTemplate)
- https://docs.reportlab.com/rml/userguide/Chapter_2_Pages_and_page_structures/ (RML document config)
- RML Tag Reference: `/document/` tag documentation

