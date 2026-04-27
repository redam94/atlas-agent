"""Tests for markdown parser (passthrough with front-matter strip)."""

from atlas_knowledge.parsers.markdown import ParsedDocument, parse_markdown


def test_parse_markdown_returns_parsed_document():
    doc = parse_markdown("# Hello\n\nbody text here.", title="Notes")
    assert isinstance(doc, ParsedDocument)
    assert doc.title == "Notes"
    assert "Hello" in doc.text
    assert doc.source_type == "markdown"


def test_parse_markdown_uses_first_h1_when_title_unset():
    doc = parse_markdown("# Auto Title\n\nbody")
    assert doc.title == "Auto Title"


def test_parse_markdown_falls_back_to_untitled():
    doc = parse_markdown("no heading here, just text.")
    assert doc.title == "Untitled"


def test_parse_markdown_strips_yaml_front_matter():
    src = """---
slug: foo
date: 2026-04-27
---
# Real Title

body
"""
    doc = parse_markdown(src)
    assert "slug: foo" not in doc.text
    assert doc.title == "Real Title"
