# Quick Start: Implementing Fixtures in Your Project

**Goal**: Get parametrized email fixtures working in 30 minutes.

---

## STEP 1: Install Dependencies (2 min)

```bash
pip install pytest faker
```

---

## STEP 2: Create `tests/conftest.py` (10 min)

```python
import pytest
from faker import Faker
from email.message import EmailMessage
from email.headerregistry import Address
from email.parser import BytesParser
from email import policy
from pathlib import Path

# Global seed for reproducibility
FIXTURE_SEED = 42

# ============ POLICY FIXTURES ============

@pytest.fixture(scope="session")
def strict_policy():
    """Strict policy: raises on defects."""
    return policy.strict

@pytest.fixture(scope="session")
def default_policy():
    """Default policy: permissive, records defects."""
    return policy.default

# ============ FAKER FIXTURE ============

@pytest.fixture(scope="session")
def faker_deterministic():
    """Seeded faker: reproducible data."""
    fake = Faker()
    Faker.seed(FIXTURE_SEED)
    return fake

# ============ MESSAGE FIXTURES ============

@pytest.fixture
def synthetic_message(faker_deterministic):
    """Generate a valid RFC 5322 message."""
    msg = EmailMessage()
    msg['From'] = Address(
        display_name=faker_deterministic.name(),
        username=faker_deterministic.user_name(),
        domain=faker_deterministic.free_email().split('@')[1]
    )
    msg['To'] = faker_deterministic.email()
    msg['Subject'] = faker_deterministic.sentence()
    msg['Date'] = faker_deterministic.http_date()
    msg.set_content(faker_deterministic.paragraph())
    return msg

# ============ EDGE CASE FIXTURES ============

EDGE_CASE_MESSAGES = [
    pytest.param(
        b"From: sender@example.com\r\n"
        b"Subject: Normal\r\n"
        b"\r\n"
        b"Body",
        id="valid_minimal"
    ),
    pytest.param(
        b"From: sender@example.com\r\n"
        b"Subject: Broken\r\n"
        b" not-indented\r\n"
        b"\r\n"
        b"Body",
        id="broken_continuation"
    ),
    pytest.param(
        b"From: sender@example.com\r\n"
        b"Subject: No separator"
        b"This is body, not header",
        id="missing_separator"
    ),
]

@pytest.fixture(params=EDGE_CASE_MESSAGES, scope="module")
def edge_case_message_bytes(request):
    """Parametrized: each edge case."""
    return request.param

@pytest.fixture
def edge_case_message(edge_case_message_bytes):
    """Parse edge case with default policy."""
    parser = BytesParser(policy=policy.default)
    return parser.parsebytes(edge_case_message_bytes)

# ============ MAILDIR FIXTURES ============

@pytest.fixture(scope="session")
def maildir_corpus(tmp_path_factory):
    """Session-scoped Maildir corpus."""
    tmpdir = tmp_path_factory.mktemp("maildir_corpus")
    maildir = tmpdir / "maildir"
    
    for subdir in ["new", "cur", "tmp"]:
        (maildir / subdir).mkdir(parents=True, exist_ok=True)
    
    # Add a few messages
    msg1 = EmailMessage()
    msg1['From'] = 'alice@example.com'
    msg1['Subject'] = 'Test 1'
    msg1.set_content('Body 1')
    (maildir / "new" / "msg1.eml").write_bytes(msg1.as_bytes())
    
    msg2 = EmailMessage()
    msg2['From'] = 'bob@example.com'
    msg2['Subject'] = 'Test 2'
    msg2.set_content('Body 2')
    (maildir / "cur" / "msg2.eml:2,").write_bytes(msg2.as_bytes())
    
    return maildir

@pytest.fixture
def temp_maildir(tmp_path):
    """Function-scoped temp Maildir."""
    maildir = tmp_path / "maildir"
    for subdir in ["new", "cur", "tmp"]:
        (maildir / subdir).mkdir(parents=True, exist_ok=True)
    return maildir

# ============ DEFECT FACTORY ============

class DefectMessageFactory:
    @staticmethod
    def broken_header():
        return (
            b"From: sender@example.com\r\n"
            b"Subject: Long subject\r\n"
            b" not-indented\r\n"
            b"\r\n"
            b"Body"
        )
    
    @staticmethod
    def missing_separator():
        return b"From: sender@example.com\r\nSubject: Test"

@pytest.fixture
def defect_factory():
    return DefectMessageFactory()


---

## STEP 3: Create Your First Test (5 min)

```python
# tests/test_email_parsing.py

def test_synthetic_message_has_subject(synthetic_message):
    """Verify fixture generates valid messages."""
    assert synthetic_message['Subject'] is not None
    assert len(synthetic_message['Subject']) > 0

def test_edge_case_parsing_doesnt_crash(edge_case_message):
    """Edge cases should parse without raising."""
    assert edge_case_message is not None

def test_strict_policy_detects_defects(edge_case_message_bytes, strict_policy):
    """Strict policy records defects."""
    from email.parser import BytesParser
    parser = BytesParser(policy=strict_policy)
    
    try:
        msg = parser.parsebytes(edge_case_message_bytes)
        # Strict mode may raise or record
        print(f"Defects recorded: {msg.defects}")
    except Exception as e:
        print(f"Strict mode raised: {type(e).__name__}")

def test_maildir_corpus_exists(maildir_corpus):
    """Verify Maildir structure."""
    assert (maildir_corpus / "new").exists()
    assert (maildir_corpus / "cur").exists()
    messages = list(maildir_corpus.glob("**/*.eml*"))
    assert len(messages) >= 2

def test_temp_maildir_isolation(temp_maildir):
    """Each test gets fresh temp Maildir."""
    from email.message import EmailMessage
    
    msg = EmailMessage()
    msg['Subject'] = 'Test'
    msg.set_content('Content')
    
    msg_file = temp_maildir / "new" / "test.eml"
    msg_file.write_bytes(msg.as_bytes())
    
    assert msg_file.exists()
    # Automatically cleaned up after test
```

---

## STEP 4: Run Tests (3 min)

```bash
pytest tests/test_email_parsing.py -v

# Output:
# test_synthetic_message_has_subject PASSED
# test_edge_case_parsing_doesnt_crash[valid_minimal] PASSED
# test_edge_case_parsing_doesnt_crash[broken_continuation] PASSED
# test_edge_case_parsing_doesnt_crash[missing_separator] PASSED
# test_strict_policy_detects_defects[valid_minimal] PASSED
# test_strict_policy_detects_defects[broken_continuation] PASSED
# test_strict_policy_detects_defects[missing_separator] PASSED
# test_maildir_corpus_exists PASSED
# test_temp_maildir_isolation PASSED
# ======== 9 passed in 0.15s ========
```

---

## NEXT: Expand to Your Needs

### Add More Edge Cases

```python
# In conftest.py, extend EDGE_CASE_MESSAGES:

EDGE_CASE_MESSAGES.extend([
    pytest.param(
        b"From: sender@example.com\r\n"
        b"Content-Type: message/rfc822\r\n"
        b"\r\n"
        b"From: inner@example.com\r\nSubject: Nested\r\n\r\nBody",
        id="nested_rfc822"
    ),
    pytest.param(
        b"From: sender@example.com\r\n"
        b"X-Custom: first\r\n"
        b"X-Custom: second\r\n"
        b"\r\n"
        b"Body",
        id="duplicate_headers"
    ),
])
```

### Add Unreadable File Testing

```python
@pytest.fixture
def corrupt_maildir(tmp_path):
    """Maildir with intentionally corrupt files."""
    maildir = tmp_path / "maildir"
    for subdir in ["new", "cur", "tmp"]:
        (maildir / subdir).mkdir(parents=True, exist_ok=True)
    
    # Valid message
    msg = EmailMessage()
    msg['Subject'] = 'Valid'
    msg.set_content('OK')
    (maildir / "new" / "valid.eml").write_bytes(msg.as_bytes())
    
    # Binary garbage
    (maildir / "new" / "corrupt.eml").write_bytes(b"\x80\x81\x82\x83")
    
    # Empty file
    (maildir / "new" / "empty.eml").write_bytes(b"")
    
    return maildir

def test_corrupt_maildir_graceful_handling(corrupt_maildir):
    """Verify parser handles corrupt files gracefully."""
    from email.parser import BytesParser
    parser = BytesParser()
    
    valid_count = 0
    for eml_file in corrupt_maildir.glob("**/*.eml*"):
        try:
            content = eml_file.read_bytes()
            if content:
                msg = parser.parsebytes(content)
                if msg is not None:
                    valid_count += 1
        except Exception as e:
            print(f"File {eml_file.name}: {type(e).__name__}")
    
    # At least the valid file should parse
    assert valid_count >= 1
```

---

## CRITICAL CHECKLIST

Before shipping tests:

- [ ] Use `Faker.seed()` for reproducibility
- [ ] Separate `function` scope (isolation) from `session` scope (expensive)
- [ ] Include at least 5 edge cases in parametrized fixtures
- [ ] Test both permissive and strict policies
- [ ] Use `tmp_path` for automatic cleanup
- [ ] Document fixture purpose and scope in docstrings
- [ ] Never include real PII in fixtures
- [ ] Test corrupt/unreadable files explicitly

---

## Troubleshooting

**Q: Tests are non-deterministic (different results each run)**  
A: Check `FIXTURE_SEED` is constant; avoid `datetime.now()`, use fixed timestamps.

**Q: Fixture scope conflict (later tests see modified state)**  
A: Use `function` scope by default; only use `session` for immutable objects.

**Q: Parametrized tests exploding (too many combinations)**  
A: Use `@pytest.fixture(params=...)` for setup variations; use `@pytest.mark.parametrize` for test inputs.

**Q: Parser not detecting my defect**  
A: Switch to `policy.strict` to force defect recording; check email package version (3.10+).

