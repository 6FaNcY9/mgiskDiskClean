"""Scaffold smoke tests — verifies pytest and ReportLab are available."""

import importlib


def test_pytest_importable():
    """pytest itself is importable (meta-check for scaffold health)."""
    import pytest  # noqa: F401

    assert True


def test_reportlab_importable():
    """ReportLab is installed and importable."""
    reportlab = importlib.import_module("reportlab")
    assert hasattr(reportlab, "Version")


def test_reportlab_version():
    """ReportLab version is 4.x or newer."""
    import reportlab

    major = int(reportlab.Version.split(".")[0])
    assert major >= 4, f"Expected ReportLab >= 4, got {reportlab.Version}"
