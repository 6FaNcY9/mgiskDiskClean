from pathlib import Path


INDEX = Path("web/public/index.php")


def test_theme_variables_present():
    """Verify that CSS theme variables are defined in the output."""
    content = INDEX.read_text(encoding="utf-8")

    # Essential space-grey theme variables
    expected_vars = [
        "--theme-bg",
        "--theme-sidebar-bg",
        "--theme-border",
        "--theme-text",
        "--theme-text-dim",
        "--theme-accent"
    ]
    
    for var in expected_vars:
        assert var in content, f"CSS variable {var} missing from index.php"


def test_three_panel_shell_and_theme_toggle_present():
    """Verify the production review shell and theme controls are wired in."""
    content = INDEX.read_text(encoding="utf-8")

    for marker in [
        'id="sidebar"',
        'id="panel-mid"',
        'id="panel-detail"',
        'id="theme-btn"',
        'id="theme-pop"',
        "toggleMode()",
        "data-accent",
        "data-mode",
    ]:
        assert marker in content, f"Expected UI marker {marker} missing from index.php"


def test_review_decision_ui_wired():
    """Verify review controls and AJAX save endpoint are present."""
    content = INDEX.read_text(encoding="utf-8")

    for marker in [
        'class="review-box"',
        'id="review-decision"',
        'id="review-notes"',
        'id="review-save"',
        'id="review-status"',
        'name="csrf-token"',
        "/api/review-decision.php",
        "email_stable_id",
        "review_decision",
    ]:
        assert marker in content, f"Expected review marker {marker} missing from index.php"


def test_email_review_components_wired():
    """Verify Phase 3 sidebar, list, detail, VT badge, and preview UI wiring."""
    content = INDEX.read_text(encoding="utf-8")

    for marker in [
        "$mailboxStats",
        "archive_emails",
        "archive_attachments",
        "vt_cache",
        "vt-clean",
        "vt-infected",
        "vt-pending",
        "download.php?",
        "'inline'=>'1'",
        "toggleAtt(",
        "att-preview",
    ]:
        assert marker in content, f"Expected review component marker {marker} missing"


def test_mobile_responsive_polish_present():
    """Verify the final mobile optimization contract is present."""
    content = INDEX.read_text(encoding="utf-8")

    for marker in [
        "@media (max-width: 900px)",
        "#app{flex-direction:column",
        "#sidebar,#panel-mid,#panel-detail{width:100%",
        ".review-row{grid-template-columns:1fr}",
    ]:
        assert marker in content, f"Expected responsive marker {marker} missing"
