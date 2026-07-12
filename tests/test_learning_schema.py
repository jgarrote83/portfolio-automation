"""Learning Loop v1.0 — deterministic proposal-schema validator tests
(src/learning/schema.py). Pure, no I/O/Azure/network. Run:
    PYTHONPATH=src pytest tests/test_learning_schema.py
"""
import difflib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from learning.schema import validate_cycle_output  # noqa: E402

_BASE_FILE = "src/config/risk-limits.json"
_BASE_CONTENT = "A: 1\nB: 2\nC: 3\n"
_MODIFIED_CONTENT = "A: 1\nB: 99\nC: 3\n"
_DIFF = "".join(difflib.unified_diff(
    _BASE_CONTENT.splitlines(keepends=True), _MODIFIED_CONTENT.splitlines(keepends=True),
    lineterm="\n",
))
_DIFF_BASE = {_BASE_FILE: _BASE_CONTENT}


def _proposal(**overrides):
    base = {
        "id": "AMD-2026-08-01",
        "class": 1,
        "title": "Example proposal",
        "change_summary": "Short change summary",
        "data_summary": "Some stat, n=20",
        "target_file": _BASE_FILE,
        "diff": _DIFF,
        "evidence": ["TradeHistory: x=1"],
        "evidence_n": 20,
        "expected_effect": "fewer bad trades",
        "falsifier": "if it doesn't improve, revert",
        "review_by": "2027-01-01",
    }
    base.update(overrides)
    return base


def _doc(proposals=None, narrative="Reviewed the month.", mode="full"):
    return {"narrative": narrative, "mode": mode, "proposals": proposals or []}


def _validate(doc, **kw):
    return validate_cycle_output(json.dumps(doc), _DIFF_BASE, **kw)


# --- JSON parse / top-level shape ---------------------------------------------------

def test_json_parse_failure():
    res = validate_cycle_output("not json{", _DIFF_BASE)
    assert res["valid"] is False
    assert "JSON parse failed" in res["errors"][0]
    assert res["parsed"] is None


def test_top_level_not_an_object():
    res = validate_cycle_output(json.dumps([1, 2, 3]), _DIFF_BASE)
    assert res["valid"] is False
    assert "must be a JSON object" in res["errors"][0]


def test_missing_narrative():
    doc = _doc()
    del doc["narrative"]
    res = _validate(doc)
    assert res["valid"] is False
    assert any("narrative" in e for e in res["errors"])


def test_invalid_mode():
    res = _validate(_doc(mode="something_else"))
    assert res["valid"] is False
    assert any("mode" in e for e in res["errors"])


def test_valid_minimal_doc():
    res = _validate(_doc())
    assert res["valid"] is True
    assert res["errors"] == []


# --- observation-only mode -----------------------------------------------------------

def test_observation_only_flag_requires_matching_mode():
    doc = _doc(proposals=[_proposal(**{"class": 0, "diff": None, "target_file": None})], mode="full")
    res = _validate(doc, observation_only=True)
    assert res["valid"] is False
    assert any("observation_only" in e for e in res["errors"])


def test_observation_only_rejects_non_class0_proposal():
    doc = _doc(mode="observation_only", proposals=[_proposal()])  # class 1
    res = _validate(doc, observation_only=True)
    assert res["valid"] is False
    assert any("observation-only mode permits class-0" in e for e in res["errors"])


def test_observation_only_class0_passes():
    doc = _doc(mode="observation_only", proposals=[_proposal(**{
        "class": 0, "diff": None, "target_file": None, "evidence_n": None,
    })])
    res = _validate(doc, observation_only=True)
    assert res["valid"] is True


# --- change_summary / data_summary char caps ------------------------------------------

def test_change_summary_over_cap():
    doc = _doc(proposals=[_proposal(change_summary="x" * 121)])
    res = _validate(doc)
    assert res["valid"] is False
    assert any("change_summary" in e and "121" in e for e in res["errors"])


def test_data_summary_over_cap():
    doc = _doc(proposals=[_proposal(data_summary="x" * 141)])
    res = _validate(doc)
    assert res["valid"] is False
    assert any("data_summary" in e and "141" in e for e in res["errors"])


def test_missing_change_summary():
    doc = _doc(proposals=[_proposal(change_summary="")])
    res = _validate(doc)
    assert res["valid"] is False
    assert any("change_summary" in e for e in res["errors"])


# --- target-file allowlist -----------------------------------------------------------

def test_allowlist_rejects_code_file():
    doc = _doc(proposals=[_proposal(target_file="src/shared/quadrants.py")])
    res = _validate(doc)
    assert res["valid"] is False
    assert any("not in the allowlist" in e for e in res["errors"])


def test_allowlist_accepts_all_four_files():
    for path in (
        "src/config/project-instructions.md", "src/config/risk-limits.json",
        "src/config/sleeve-roles.json", "config/flex-candidates.json",
    ):
        base = {path: _BASE_CONTENT}
        doc = _doc(proposals=[_proposal(target_file=path)])
        res = validate_cycle_output(json.dumps(doc), base)
        assert res["valid"] is True, f"{path}: {res['errors']}"


# --- class 1-2: diff required + must apply cleanly -----------------------------------

def test_class1_missing_diff():
    doc = _doc(proposals=[_proposal(diff="")])
    res = _validate(doc)
    assert res["valid"] is False
    assert any("missing a 'diff'" in e for e in res["errors"])


def test_class1_diff_does_not_apply():
    bad_base = {_BASE_FILE: "totally different content\n"}
    doc = _doc(proposals=[_proposal()])
    res = validate_cycle_output(json.dumps(doc), bad_base)
    assert res["valid"] is False
    assert any("does not apply cleanly" in e for e in res["errors"])


def test_class1_diff_applies_cleanly_passes():
    res = _validate(_doc(proposals=[_proposal()]))
    assert res["valid"] is True


# --- class 2: evidence_n >= 10 ---------------------------------------------------------

def test_class2_evidence_n_below_threshold():
    doc = _doc(proposals=[_proposal(**{"class": 2, "evidence_n": 9})])
    res = _validate(doc)
    assert res["valid"] is False
    assert any("evidence_n" in e for e in res["errors"])


def test_class2_evidence_n_at_threshold_passes():
    doc = _doc(proposals=[_proposal(**{"class": 2, "evidence_n": 10})])
    res = _validate(doc)
    assert res["valid"] is True


def test_class2_evidence_n_missing():
    doc = _doc(proposals=[_proposal(**{"class": 2, "evidence_n": None})])
    res = _validate(doc)
    assert res["valid"] is False


# --- class 3: no diff, spec_draft + implementation_brief required --------------------

def test_class3_with_diff_rejected():
    doc = _doc(proposals=[_proposal(**{
        "class": 3, "diff": "some diff", "spec_draft": "draft", "implementation_brief": "brief",
        "target_file": None,
    })])
    res = _validate(doc)
    assert res["valid"] is False
    assert any("must not carry a 'diff'" in e for e in res["errors"])


def test_class3_missing_spec_draft():
    doc = _doc(proposals=[_proposal(**{
        "class": 3, "diff": None, "target_file": None, "implementation_brief": "brief",
    })])
    res = _validate(doc)
    assert res["valid"] is False
    assert any("spec_draft" in e for e in res["errors"])


def test_class3_missing_implementation_brief():
    doc = _doc(proposals=[_proposal(**{
        "class": 3, "diff": None, "target_file": None, "spec_draft": "draft",
    })])
    res = _validate(doc)
    assert res["valid"] is False
    assert any("implementation_brief" in e for e in res["errors"])


def test_class3_valid():
    doc = _doc(proposals=[_proposal(**{
        "class": 3, "diff": None, "target_file": None,
        "spec_draft": "# Draft\n...", "implementation_brief": "1. do x\n2. do y",
    })])
    res = _validate(doc)
    assert res["valid"] is True


# --- caps: <=3 non-class-0, <=1 class-3 -----------------------------------------------

def test_cap_exceeded_four_non_class0():
    proposals = [_proposal(id=f"AMD-2026-08-0{i}") for i in range(1, 5)]
    res = _validate(_doc(proposals=proposals))
    assert res["valid"] is False
    assert any("exceed the cap of 3" in e for e in res["errors"])


def test_cap_exactly_three_passes():
    proposals = [_proposal(id=f"AMD-2026-08-0{i}") for i in range(1, 4)]
    res = _validate(_doc(proposals=proposals))
    assert res["valid"] is True


def test_class0_excluded_from_cap():
    proposals = [_proposal(id=f"AMD-2026-08-0{i}") for i in range(1, 4)]
    proposals += [_proposal(**{
        "id": "AMD-2026-08-09", "class": 0, "diff": None, "target_file": None, "evidence_n": None,
    })]
    res = _validate(_doc(proposals=proposals))
    assert res["valid"] is True  # 3 non-class-0 + 1 class-0 = still within the cap


def test_class3_cap_exceeded():
    def c3(i):
        return _proposal(**{
            "id": f"AMD-2026-08-0{i}", "class": 3, "diff": None, "target_file": None,
            "spec_draft": "d", "implementation_brief": "b",
        })
    res = _validate(_doc(proposals=[c3(1), c3(2)]))
    assert res["valid"] is False
    assert any("class-3" in e and "exceed the cap of 1" in e for e in res["errors"])


def test_revert_exempt_from_caps():
    proposals = [_proposal(id=f"AMD-2026-08-0{i}") for i in range(1, 4)]
    proposals.append(_proposal(**{
        "id": "AMD-2026-08-09", "is_revert": True, "re_review_of": "AMD-2026-06-01",
    }))
    res = _validate(_doc(proposals=proposals))
    assert res["valid"] is True  # the 4th is a revert, exempt from the 3-cap


# --- forced re-review rule -------------------------------------------------------------

def test_due_amendment_not_re_reviewed_fails():
    res = _validate(_doc(proposals=[_proposal()]), due_amendment_ids=["AMD-2026-06-01"])
    assert res["valid"] is False
    assert any("AMD-2026-06-01" in e and "not re-reviewed" in e for e in res["errors"])


def test_due_amendment_re_reviewed_passes():
    doc = _doc(proposals=[_proposal(re_review_of="AMD-2026-06-01")])
    res = _validate(doc, due_amendment_ids=["AMD-2026-06-01"])
    assert res["valid"] is True


# --- misc --------------------------------------------------------------------------

def test_duplicate_ids_rejected():
    doc = _doc(proposals=[_proposal(), _proposal()])
    res = _validate(doc)
    assert res["valid"] is False
    assert any("duplicate proposal id" in e for e in res["errors"])


def test_invalid_class_rejected():
    doc = _doc(proposals=[_proposal(**{"class": 7})])
    res = _validate(doc)
    assert res["valid"] is False
    assert any("invalid 'class'" in e for e in res["errors"])


def test_proposals_not_a_list():
    doc = _doc()
    doc["proposals"] = "not a list"
    res = _validate(doc)
    assert res["valid"] is False
    assert any("'proposals' must be a list" in e for e in res["errors"])


def test_multiple_errors_all_collected():
    """A cycle failing several rules at once reports all of them, not just the first."""
    doc = _doc(proposals=[_proposal(change_summary="x" * 200, target_file="src/shared/quadrants.py")])
    res = _validate(doc)
    assert res["valid"] is False
    assert len(res["errors"]) >= 2
