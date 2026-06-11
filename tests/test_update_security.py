from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_apply_update_requires_admin_csrf_and_server_manifest():
    source = (ROOT / "web/public/api/apply-update.php").read_text(encoding="utf-8")

    assert "MailReview\\\\Auth\\\\CsrfGuard" in source
    assert "$csrf->enforce();" in source
    assert "$sm->getRole() !== 'admin'" in source
    assert "$updateUrl . '/updates/manifest.json'" in source
    assert "$payload['manifest']" not in source


def test_apply_update_validates_attachment_archive_members():
    source = (ROOT / "web/public/api/apply-update.php").read_text(encoding="utf-8")

    assert "tar --zstd -tf" in source
    assert "unsafe_attachments_archive" in source
    assert "^mailboxes/[a-zA-Z0-9._-]+/attachments" in source


def test_push_update_omits_attachments_manifest_when_disabled():
    source = (ROOT / "scripts/push-update.sh").read_text(encoding="utf-8")
    manifest_section = source[source.index("# Write fresh manifest.json"):]

    assert 'if [[ "${PUSH_ATTACHMENTS}" == "1" ]]; then' in source
    assert '"attachments": {' in manifest_section
    assert manifest_section.index('"attachments": {') < manifest_section.index("else")
