"""F8: guidance_version() reads the SKILL.md frontmatter, and falls back to
'unknown' (not a crash) if the version line is missing."""
import os

os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")

from app.core import guidance  # noqa: E402


def test_guidance_version_reads_frontmatter():
    guidance.guidance_version.cache_clear()
    try:
        assert guidance.guidance_version() == "2.5.0"
    finally:
        guidance.guidance_version.cache_clear()


def test_guidance_version_falls_back_to_unknown(monkeypatch):
    monkeypatch.setattr(guidance, "skill_markdown", lambda: "# SKILL\n\nno version line here\n")
    guidance.guidance_version.cache_clear()
    try:
        assert guidance.guidance_version() == "unknown"
    finally:
        guidance.guidance_version.cache_clear()  # restore real value for other tests
