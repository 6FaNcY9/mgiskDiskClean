# Pytest Fixture Strategy for Maildir/Email Testing

**Research Date**: 2026-03-24  
**Status**: Complete fixture specification with edge case catalog  
**Audience**: Tasks 1-4, final verification design

---

## WHAT YOU'LL FIND HERE

This research provides **production-ready fixture patterns** for deterministic, edge-case-aware email parsing tests.

### Files

1. **fixture-strategy.md** (666 lines)
   - Architecture: Unit vs. integration fixtures
   - Core patterns: seeded factories, parametrized edges, Maildir corpus
   - Scope discipline: function, module, session recommendations
   - Full implementation examples with docstrings

2. **edge-case-catalog.md** (341 lines)
   - 10 critical edge cases from CPython issues (2024-2026)
   - Real test cases demonstrating each defect
   - Expected vs. wrong behavior clarifications
   - Security implications (header injection, encoding attacks)
   - Parametrized test template

3. **implementation-quick-start.md** (332 lines)
   - 30-minute setup walkthrough
   - Ready-to-copy conftest.py
   - First test suite example
   - Troubleshooting common fixture pitfalls

---

## QUICK FACTS

| Aspect | Recommendation |
|--------|-----------------|
| **Determinism** | Use `Faker.seed(CONSTANT)` for reproducible data |
| **Parametrization** | Parametrize edge cases (min 9 RFC violations) |
| **Scope Strategy** | `function` for isolation, `session` for expensive setup |
| **Policy** | Test both `policy.strict` and `policy.default` |
| **Error Handling** | Explicit tests for corrupt/unreadable files |
| **Folder Layout** | `tests/conftest.py` + `tests/fixtures/` optional |
| **Seed Value** | FIXTURE_SEED = 42 (immutable constant) |

---

## FIXTURE HIERARCHY

```
┌─────────────────────────────────────────────┐
│  Integration Fixtures (Maildir Corpus)     │ session-scoped, reused
├─────────────────────────────────────────────┤
│  Unit Message Fixtures (Parametrized)      │ module-scoped, edge cases
├─────────────────────────────────────────────┤
│  Defect & Fallback Fixtures (Error Cases) │ function-scoped, isolation
└─────────────────────────────────────────────┘
```

---

## CORE PATTERNS AT A GLANCE

### 1. Seeded Faker (Reproducible)
```python
@pytest.fixture(scope="session")
def faker_deterministic():
    fake = Faker()
    Faker.seed(42)  # Constant seed
    return fake
```

### 2. Parametrized Edge Cases
```python
@pytest.fixture(params=EDGE_CASE_MESSAGES, scope="module")
def edge_case_message_bytes(request):
    return request.param  # Each edge case runs separately
```

### 3. Maildir Corpus (Session)
```python
@pytest.fixture(scope="session")
def maildir_corpus(tmp_path_factory):
    # Create realistic Maildir with new/, cur/, tmp/
    # Reused across all tests
    return maildir
```

### 4. Strict Policy
```python
@pytest.fixture(scope="session")
def strict_policy():
    return policy.strict  # Enforces defect recording
```

---

## EDGE CASES COVERED

✓ Broken header continuation (RFC 2822 violation)  
✓ RFC 2047 encoding with special characters (security)  
✓ Header folding with embedded newlines (injection risk)  
✓ Missing header-body separator  
✓ Invalid header field names  
✓ Message/RFC822 nesting (forwarded emails)  
✓ Duplicate header keys  
✓ Base64/Quoted-Printable wrapping  
✓ Unreadable/binary files  
✓ Attachment filename encoding  

**Total**: 10 critical edge cases, each with test template.

---

## IMPLEMENTATION PATH

### Phase 1: Setup (10 min)
1. Copy `conftest.py` from quick-start
2. Install `pytest` + `faker`
3. Run first test suite

### Phase 2: Integrate (20 min)
1. Adapt edge cases to your parser's requirements
2. Add parametrized variants for Maildir sizes
3. Test corrupt file handling

### Phase 3: Validate (15 min)
1. Verify determinism (run tests 2x, same results)
2. Check strict mode enforces policy
3. Confirm unreadable files don't crash

---

## CRITICAL WARNINGS

⚠️ **Don't Use Real PII**: Faker generates synthetic data. Never copy production mailboxes.  
⚠️ **Strict Mode Non-Negotiable**: Tests must enforce `email.policy.strict` for correctness.  
⚠️ **Scope Conflicts**: Function scope by default; only broaden scope for immutable objects.  
⚠️ **Parametrization Explosion**: Stacking fixtures creates O(n×m) test cases—use intentionally.  
⚠️ **Cleanup Discipline**: Use `yield` or `tmp_path` for automatic cleanup.  

---

## KEY REFERENCES

| Topic | Source | Lines |
|-------|--------|-------|
| Fixture architecture | fixture-strategy.md | §1 |
| Parametrization patterns | fixture-strategy.md | §2.2, §3.2 |
| Edge cases | edge-case-catalog.md | All 10 cases |
| RFC violations | CPython issues #504152, #127794, #132105, #121284 | |
| Implementation | implementation-quick-start.md | Full example |
| Scope discipline | fixture-strategy.md | §1.2 |

---

## NEXT STEPS FOR TASKS 1-4

1. **Task 1** (Parser): Use edge cases from catalog for unit tests
2. **Task 2** (Maildir reader): Use `maildir_corpus` fixture for integration tests
3. **Task 3** (PDF report): Use parametrized test results as data
4. **Task 4** (Verification): Strict mode + defect factory for acceptance tests

All fixtures are **deterministic** and **isolated**—perfect for TDD.

---

**Generated**: 2026-03-24  
**Total Research**: 1,500 lines across 4 documents  
**Estimated Implementation Time**: 45 minutes  
**Test Coverage**: 10+ edge cases + 3+ Maildir variants + defect handling

