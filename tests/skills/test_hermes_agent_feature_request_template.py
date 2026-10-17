"""The hermes-agent skill's feature-request draft mirrors the repo's issue form, so an
agent-filed suggestion (#15470) reads the same to triage as one filed through the web form."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FORM = REPO / ".github" / "ISSUE_TEMPLATE" / "feature_request.yml"
TEMPLATE = REPO / "skills" / "autonomous-ai-agents" / "hermes-agent" / "templates" / "feature-request.md"


def _form_fields():
    """{id: (label, required, options)} for the form's input fields; a line parser because
    skill tests stay stdlib-only and the form's shape is flat."""
    fields = {}
    for block in re.split(r"^  - type: ", FORM.read_text(encoding="utf-8"), flags=re.MULTILINE)[1:]:
        if not block.startswith(("textarea", "dropdown")):
            continue
        field_id = re.search(r"^    id: (\S+)", block, re.MULTILINE).group(1)
        label = re.search(r"^      label: (.+)$", block, re.MULTILINE).group(1).strip().strip('"')
        options = re.findall(r"^        - (.+)$", block, re.MULTILINE)
        fields[field_id] = (label, "required: true" in block, options)
    return fields


def _template_headings():
    return [ln[4:].strip() for ln in TEMPLATE.read_text(encoding="utf-8").splitlines() if ln.startswith("### ")]


def test_every_required_form_field_has_a_template_section():
    headings = _template_headings()
    required = [label for label, is_required, _ in _form_fields().values() if is_required]
    assert required and all(label in headings for label in required)


def test_feature_type_choices_match_the_form_dropdown():
    text = " ".join(TEMPLATE.read_text(encoding="utf-8").split())
    for option in _form_fields()["type"][2]:
        assert option in text, option
