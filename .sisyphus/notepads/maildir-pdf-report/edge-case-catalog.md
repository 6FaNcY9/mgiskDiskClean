# Edge Case Catalog for Email/Maildir Testing

**Reference**: CPython Issues & Real-World Email Problems (2024-2026)

---

## CRITICAL EDGE CASES

### 1. Broken Header Continuation (RFC 2822 §2.2.3)

**Issue**: [CPython #504152](https://bugs.python.org/issue504152)  
**RFC Requirement**: Continuation lines MUST start with whitespace (space or tab).

**Test Case**:
```python
b"From: sender@example.com\r\n"
b"Subject: Long subject line that\r\n"
b"not-indented-continuation\r\n"  # Missing leading space/tab
b"\r\n"
b"Body"
```

**Expected Behavior**:
- ✓ Permissive policy: Parses, records defect
- ✓ Strict policy: Raises or records defect
- ✗ **WRONG**: Silently treats as body

**Why It Matters**: Unfolding broken headers can leak content into body, breaking MIME parsing.

---

### 2. RFC 2047 Encoding with Special Characters

**Issue**: [CPython #121284](https://github.com/python/cpython/issues/121284)  
**Security**: Unquoted specials (commas, semicolons) in address headers can alter meaning.

**Test Case**:
```python
b"From: sender@example.com\r\n"
b"To: =?utf-8?b?TmfGsOG7nWkgYmjhuq1u?= <to@example.com>, another@example.com\r\n"
b"\r\n"
b"Body"
```

**Expected Behavior**:
- ✓ Parsing preserves encoded-word integrity
- ✗ **WRONG**: Refolding removes encoding, leaks comma

**Why It Matters**: Malicious actors can inject headers or alter routing via encoded-word tricks.

---

### 3. Header Folding with Embedded Newlines

**Issue**: [CPython #132105](https://github.com/python/cpython/issues/132105)  
**Security**: RFC 2047-encoded headers ending in `\n` can fold with double line endings.

**Test Case**:
```python
msg = EmailMessage()
msg['Subject'] = 'Some text\n'  # Embedded newline in encoded payload
msg.set_payload("Attachment content", subtype="octet-stream")
serialized = msg.as_bytes()
# Reparse: may contain duplicate line breaks
```

**Expected Behavior**:
- ✓ Folding preserves structural integrity
- ✗ **WRONG**: Double `\r\n\r\n` allows header injection

**Why It Matters**: Extra blank lines allow injection of fake headers/attachments.

---

### 4. Missing Header-Body Separator

**Issue**: [CPython #26686](https://bugs.python.org/issue26686)  
**RFC Requirement**: Headers and body separated by blank line (`\r\n\r\n`).

**Test Cases**:
```python
# Case A: No separator at all
b"From: sender@example.com\r\n"
b"Subject: Test\r\n"
b"This is body, not a header"

# Case B: Only \n, not \r\n\r\n
b"From: sender@example.com\n"
b"Subject: Test\n"
b"\n"
b"Body"
```

**Expected Behavior**:
- ✓ Detect missing separator and record defect
- ✓ Treat everything as headers (safe fail)
- ✗ **WRONG**: Stop parsing headers and treat rest as body

**Why It Matters**: If malformed message is treated as having headers when it doesn't, attachment defects go undetected.

---

### 5. Invalid Header Field Names (RFC 5322)

**Issue**: [CPython #127794](https://github.com/python/cpython/issues/127794)  
**RFC Requirement**: Header names: `[\041-\176]+` (printable ASCII, no space).

**Test Case**:
```python
b"Invalid Header Name: value\r\n"  # Space in field name
b"From: sender@example.com\r\n"
b"\r\n"
b"Body"
```

**Expected Behavior**:
- ✓ Strict policy: Rejects or records defect
- ✗ **WRONG**: Accepts, then fails when re-parsing

**Why It Matters**: Invalid headers accepted during creation but rejected during serialization cause idempotency failures.

---

### 6. Message/RFC822 Nesting (MIME Forwarding)

**Edge Case**: Nested email-within-email structures.

**Test Case**:
```python
b"From: outer@example.com\r\n"
b"Content-Type: message/rfc822\r\n"
b"\r\n"
b"From: inner@example.com\r\n"
b"Subject: Nested Message\r\n"
b"Content-Type: application/octet-stream\r\n"
b"\r\n"
b"\x80\x81\x82\x83"  # Binary payload in nested message
```

**Expected Behavior**:
- ✓ Parser recognizes `message/rfc822` MIME type
- ✓ Recursively parses inner message
- ✓ Handles binary payload without crash
- ✗ **WRONG**: Treats inner message as flat text

**Why It Matters**: Forwarded emails must preserve attachment structure; flat parsing loses data.

---

### 7. Duplicate Header Keys

**Edge Case**: Same header appears multiple times (allowed for some headers).

**Test Case**:
```python
b"From: sender@example.com\r\n"
b"X-Custom: first\r\n"
b"X-Custom: second\r\n"  # Duplicate key
b"Received: from server1\r\n"
b"Received: from server2\r\n"  # Legal duplicate
b"\r\n"
b"Body"
```

**Expected Behavior**:
- ✓ `Received` headers: Collect all (legal)
- ✓ `X-Custom`: Application-defined behavior
- ✗ **WRONG**: Last value overwrites first

**Why It Matters**: Email traceback (Received headers) requires all entries; overwriting loses routing info.

---

### 8. Base64/Quoted-Printable Encoding Edge Cases

**Edge Case**: Long attachments that must be line-wrapped.

**Test Case**:
```python
b"From: sender@example.com\r\n"
b"Content-Transfer-Encoding: base64\r\n"
b"\r\n"
b"UmFyIRoHAM+QcwAADQAAAAAAAABKRXQgkC4ApAMAAEAHAAACJLrQXYFUfkgdMwkAIAAAAGEw"
b"ZjEwZi5qcwDwrrI/DB2NDI0TzcGb3Gpb8HzsS0UlpwELvdyWnVaBQt7Sl2zbJpx1qqFCGGk6"
```

**Expected Behavior**:
- ✓ Recognizes base64 encoding
- ✓ Properly decodes wrapped lines
- ✗ **WRONG**: Treats line breaks as part of payload

**Why It Matters**: Improper unwrapping corrupts binary attachments.

---

### 9. Unreadable/Binary File Handling

**Edge Case**: Files that can't be decoded as UTF-8.

**Test Cases**:
```python
# Case A: Binary garbage
b"\x80\x81\x82\x83\xFF"

# Case B: Empty file
b""

# Case C: Partial header only
b"From: sender@example.com\r\n"
b"Subject: No body separator"
```

**Expected Behavior**:
- ✓ Parser attempts graceful handling
- ✓ Records error/defect instead of crashing
- ✓ Continues processing other files
- ✗ **WRONG**: Raises unhandled exception

**Why It Matters**: Maildir corpus may contain corrupted files; tests must not fail on single bad file.

---

### 10. Attachment Filename Encoding

**Edge Case**: Non-ASCII filenames in RFC 2047 encoding.

**Test Case**:
```python
b"From: sender@example.com\r\n"
b"Content-Disposition: attachment; filename=\"=?utf-8?b?VGVzdC5kw7Zj?=\"\r\n"
b"\r\n"
b"File content"
```

**Expected Behavior**:
- ✓ Decodes filename to `Test.döc`
- ✓ Handles Unicode correctly
- ✗ **WRONG**: Leaves as-is or corrupts

**Why It Matters**: Filenames with special characters must be properly decoded for filesystem operations.

---

## PARAMETRIZED TEST TEMPLATE

```python
# tests/test_edge_cases.py

import pytest
from email.parser import BytesParser
from email import policy

EDGE_CASES = [
    pytest.param(
        b"From: sender@example.com\r\n"
        b"Subject: Broken continuation\r\n"
        b" not-indented\r\n"
        b"\r\n"
        b"Body",
        "broken_header_continuation",
        should_defect=True,
        strict_raises=False,
    ),
    pytest.param(
        b"Invalid Header: value\r\n"
        b"From: sender@example.com\r\n"
        b"\r\n"
        b"Body",
        "invalid_header_name",
        should_defect=True,
        strict_raises=False,
    ),
    # ... more cases
]

@pytest.mark.parametrize("msg_bytes,case_id,should_defect,strict_raises", EDGE_CASES)
def test_edge_case_permissive(msg_bytes, case_id, should_defect, strict_raises):
    """Default policy: permissive, defects recorded."""
    parser = BytesParser(policy=policy.default)
    msg = parser.parsebytes(msg_bytes)
    
    if should_defect:
        assert len(msg.defects) > 0, f"Case {case_id}: Expected defects"
    assert msg is not None

@pytest.mark.parametrize("msg_bytes,case_id,should_defect,strict_raises", EDGE_CASES)
def test_edge_case_strict(msg_bytes, case_id, should_defect, strict_raises):
    """Strict policy: defects must be handled."""
    parser = BytesParser(policy=policy.strict)
    
    if strict_raises:
        with pytest.raises(Exception):
            parser.parsebytes(msg_bytes)
    else:
        msg = parser.parsebytes(msg_bytes)
        if should_defect:
            assert len(msg.defects) > 0
```

---

## MAILDIR-SPECIFIC EDGE CASES

### 11. Maildir Flag Syntax

**Edge Case**: Files named with Maildir flags (`:2,FLAGS`).

**Structure**:
```
maildir/
  new/
    1234567890.V1I1A1M1,S=1234:2,
    # :2, = flags section (RFC 6104)
    # Flags: D (draft), F (flagged), P (passed), R (replied), S (seen), T (trashed)
cur/
    1234567891.V1I1A1M1,S=5678:2,S  # Marked as Seen
```

**Test**: Preserve flag information when parsing.

### 12. Large Maildir Performance

**Edge Case**: Thousands of files in single folder.

**Test**: Corpus with 100+ messages in `new/` folder.

### 13. Concurrent Access Simulation

**Edge Case**: Files being added/removed during iteration.

**Test**: Use `os.walk()` safely; handle ENOENT on deleted files.

---

## REFERENCES

- [RFC 5322 - Internet Message Format](https://www.rfc-editor.org/rfc/rfc5322.html)
- [RFC 2047 - MIME Part Three](https://www.rfc-editor.org/rfc/rfc2047.html)
- [RFC 6104 - Maildir Specification](https://www.rfc-editor.org/rfc/rfc6104.html)
- CPython email module issues: #504152, #26686, #127794, #121284, #132105

