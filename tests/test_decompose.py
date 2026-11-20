"""
Unit tests for the ambiguous-severity case: a request that names checks but
states no grading scale at all (realistic for a short pasted email -- see
STATUS.md Step 10's "Bug found and fixed" and Step 13's eval). Before
_fill_severity_defaults existed, this crashed severity.grade() with "No
severity rule matches ...: fix the scale mapping" the moment run_checks hit
a gap on an ungraded check_type. These tests are deliberately pure/fast (no
LLM call) -- they exercise the same backstop decompose() AND
decompose_agent.py both call, on synthetic input shaped exactly like what a
real ambiguous request decomposes to. Run: pytest -q
"""
from datetime import date, timedelta

import pytest

from agent.decompose import (
    DEFAULT_SEVERITY_ORDER,
    PeriodSpec,
    _fill_severity_defaults,
    _resolve_period,
    _resolve_relative_period,
    _spans_in,
)
from agent.severity import grade
from agent.verdict import Finding


def test_zero_severity_info_still_grades_every_extracted_check_type():
    """The exact scenario a one-line "please confirm calling hours, frequency,
    and WhatsApp consent were followed" request produces: multiple check_types,
    no severity_order, no severity_rules at all."""
    checks = [{"check_type": "CALL_HOURS"}, {"check_type": "CALL_FREQUENCY"},
              {"check_type": "CONSENT_BEFORE_CONTACT"}]
    order, rules = _fill_severity_defaults(checks, [], [])

    assert order == list(DEFAULT_SEVERITY_ORDER)  # falls back to ["CRITICAL"]
    for c in checks:
        # must not raise "No severity rule matches" -- that was the actual crash
        assert grade(Finding("whatever_gap_type", ["REC1"], "detail"), c["check_type"], rules) == "CRITICAL"


def test_partially_graded_request_only_fills_the_gaps():
    """A request that gives its own 3-level scale but only grades one of the
    two check_types it asks about (plausible: the letter's severity section
    covers 'calling hours' explicitly and is silent on frequency). The
    model-written rule must survive untouched; only the missing check_type
    gets a default -- and that default is the scale's WORST level, not an
    arbitrary pick (STATUS.md: "treat an ungraded gap as the worst level")."""
    checks = [{"check_type": "CALL_HOURS"}, {"check_type": "CALL_FREQUENCY"}]
    order, rules = _fill_severity_defaults(
        checks, ["CRITICAL", "MAJOR", "MINOR"], [{"check_type": "CALL_HOURS", "severity": "MAJOR"}],
    )

    assert order == ["CRITICAL", "MAJOR", "MINOR"]
    assert grade(Finding("outside_calling_hours", [], ""), "CALL_HOURS", rules) == "MAJOR"
    assert grade(Finding("daily_cap_exceeded", [], ""), "CALL_FREQUENCY", rules) == "CRITICAL"


def test_fully_graded_request_is_untouched():
    """Sanity check the backstop doesn't interfere when the request already
    grades everything (REQ-01/02/03's actual case -- none of them are
    ambiguous, which is exactly why this file exists)."""
    checks = [{"check_type": "CALL_HOURS"}]
    rules_in = [{"check_type": "CALL_HOURS", "severity": "MAJOR"}]
    order, rules_out = _fill_severity_defaults(checks, ["CRITICAL", "MAJOR", "MINOR"], rules_in)
    assert rules_out == rules_in


# ---------------------------------------------------------------- period spans (Step 19)
# "Q2 2026" / "June 2026" used to hard-fail with "Could not find an explicit audit
# period", forcing the user to retype exact from/to dates. These lock in what is
# accepted -- and, just as importantly, what still isn't.

def test_calendar_quarters():
    """Q1 = Jan-Mar, matching REQ-01's own data (subject 'Q1 2026',
    period 2026-01-01..2026-03-31) -- NOT the Indian fiscal Apr-Mar reading,
    which would mis-parse that very request."""
    assert (date(2026, 1, 1), date(2026, 3, 31)) in _spans_in("Q1 2026")
    assert (date(2026, 4, 1), date(2026, 6, 30)) in _spans_in("audit for Q2 2026 please")
    assert (date(2026, 10, 1), date(2026, 12, 31)) in _spans_in("Q4, 2026")


def test_month_spans():
    assert (date(2026, 6, 1), date(2026, 6, 30)) in _spans_in("June 2026")
    assert (date(2026, 1, 1), date(2026, 3, 31)) in _spans_in("January to March 2026")
    assert (date(2026, 1, 1), date(2026, 3, 31)) in _spans_in("Jan 2026 to Mar 2026")


def test_a_single_date_is_never_widened_to_its_whole_month():
    """The subtle one: "1 June 2026" contains the substring "June 2026". Without
    the optional-day guard in _MONTH_YEAR_RE it would yield a 1-30 June span, so
    a request naming one date could be silently audited over the entire month."""
    assert _spans_in("1 June 2026") == set()
    assert _spans_in("from 1 June 2026 to 30 June 2026") == set()


def test_relative_and_ambiguous_wording_still_refused():
    """Relative wording would make the audited window depend on when the run
    happened -- unacceptable for a reproducible compliance record. FY is left
    out because Apr-Mar vs Jan-Dec is genuinely ambiguous."""
    for text in ["last quarter", "previous month", "FY2026", "Q5 2026", "sometime in 2026"]:
        assert _spans_in(text) == set(), text


def test_resolve_period_accepts_a_quoted_quarter():
    period, src = _resolve_period(
        None, PeriodSpec(from_date="2026-04-01", to_date="2026-06-30", source_text="Q2 2026"),
        "please audit ACC100001 for Q2 2026",
    )
    assert period == {"from": "2026-04-01", "to": "2026-06-30"} and src == "extracted"


def test_resolve_period_rejects_a_span_the_request_never_stated(monkeypatch):
    """The model claiming a quarter that isn't in the quoted words must still fail.

    "audit our collections" matches neither the fast deterministic patterns nor
    anything an isolated re-read would find, so the LLM fallback is reached --
    mocked here to stay pure/fast/deterministic, same policy as the rest of this
    file. A live, unmocked run of exactly this case is confirmed in STATUS.md:
    the real Sarvam call independently agreed there's no period here too."""
    monkeypatch.setattr("agent.decompose._llm_period_from_quote", lambda q: None)
    with pytest.raises(ValueError):
        _resolve_period(
            None, PeriodSpec(from_date="2026-04-01", to_date="2026-06-30", source_text="audit our collections"),
            "audit our collections",
        )


def test_resolve_period_never_calls_the_llm_for_forms_the_fast_path_already_covers(monkeypatch):
    """Step 21: the deterministic regex path must still be tried FIRST -- an LLM
    fallback that fired on every call would add needless latency/cost to the
    already-covered, already-tested common case."""
    def _boom(quoted):
        raise AssertionError("fast path should have matched -- the LLM fallback should never be reached")
    monkeypatch.setattr("agent.decompose._llm_period_from_quote", _boom)
    period, src = _resolve_period(
        None, PeriodSpec(from_date="2026-04-01", to_date="2026-06-30", source_text="Q2 2026"),
        "please audit ACC100001 for Q2 2026",
    )
    assert period == {"from": "2026-04-01", "to": "2026-06-30"} and src == "extracted"


# ---------------------------------------------------------------- LLM-verified period fallback (Step 21)
# Forms the fixed regex in _spans_in/_dates_in never enumerated -- fiscal years,
# half-years, cross-year quarter ranges, weeks/fortnights. _resolve_period asks an
# isolated LLM call (mocked here) which is independently re-derived from JUST the
# quoted words, not the original extraction's own (possibly wrong) arithmetic --
# that's why the returned period below intentionally does NOT equal the "extracted"
# from_date/to_date the PeriodSpec was given.

def test_resolve_period_accepts_a_fiscal_year_via_the_llm_fallback(monkeypatch):
    monkeypatch.setattr("agent.decompose._llm_period_from_quote",
                        lambda q: (date(2025, 4, 1), date(2026, 3, 31)) if q == "FY2026" else None)
    period, src = _resolve_period(
        None, PeriodSpec(from_date="2026-01-01", to_date="2026-12-31", source_text="FY2026"),
        "please audit our collections for FY2026",
    )
    assert period == {"from": "2025-04-01", "to": "2026-03-31"} and src == "extracted"


def test_resolve_period_accepts_a_half_year_via_the_llm_fallback(monkeypatch):
    monkeypatch.setattr("agent.decompose._llm_period_from_quote",
                        lambda q: (date(2026, 1, 1), date(2026, 6, 30)) if q == "H1 2026" else None)
    period, src = _resolve_period(
        None, PeriodSpec(from_date="2026-01-01", to_date="2026-06-30", source_text="H1 2026"),
        "evidence for H1 2026 please",
    )
    assert period == {"from": "2026-01-01", "to": "2026-06-30"} and src == "extracted"


def test_resolve_period_accepts_a_cross_year_quarter_range_via_the_llm_fallback(monkeypatch):
    monkeypatch.setattr("agent.decompose._llm_period_from_quote",
                        lambda q: (date(2025, 10, 1), date(2026, 3, 31)) if q == "Q4 2025 to Q1 2026" else None)
    period, src = _resolve_period(
        None, PeriodSpec(from_date="2025-10-01", to_date="2026-03-31", source_text="Q4 2025 to Q1 2026"),
        "covering Q4 2025 to Q1 2026",
    )
    assert period == {"from": "2025-10-01", "to": "2026-03-31"} and src == "extracted"


def test_resolve_period_still_rejects_relative_units_it_does_not_understand(monkeypatch):
    """"last quarter"/"last year" are relative but outside the day/week/month units
    _resolve_relative_period understands -- the isolated LLM fallback is asked too
    (mocked here to correctly say "no fixed period", same as a live call would for
    genuinely relative wording) and the whole thing must still fail, never guess."""
    monkeypatch.setattr("agent.decompose._llm_period_from_quote", lambda q: None)
    with pytest.raises(ValueError):
        _resolve_period(
            None, PeriodSpec(from_date="2026-07-01", to_date="2026-09-30", source_text="last quarter"),
            "please audit last quarter",
        )


# ---------------------------------------------------------------- relative period resolver (Step 22)
# "last N days/weeks/months" resolved deterministically against a FIXED `today` (never
# the real wall clock in tests) -- this is plain date arithmetic, not an LLM call, so it's
# asserted exactly, the same way the rest of this file tests pure functions.

def test_last_n_months_means_n_complete_calendar_months_before_the_current_one():
    """Run on 20 Nov 2026, "last 2 months" means all of September and October --
    never a partial November, and never a rolling 60-day window."""
    start, end, note = _resolve_relative_period("only for last 2 months", today=date(2026, 11, 20))
    assert (start, end) == (date(2026, 9, 1), date(2026, 10, 31))
    assert "last 2 months" in note and "2026-11-20" in note


def test_last_month_with_no_number_means_one_month_back():
    start, end, note = _resolve_relative_period("evidence for last month please", today=date(2026, 11, 20))
    assert (start, end) == (date(2026, 10, 1), date(2026, 10, 31))


def test_last_n_months_including_this_month_extends_through_today():
    """"including this month" pulls the current, still-incomplete month in too --
    run on 20 Nov, "last 3 months including this month" means Sep 1 through the
    20th of November (today), not the end of November (which hasn't happened)."""
    start, end, note = _resolve_relative_period(
        "last 3 months, including this month please", today=date(2026, 11, 20))
    assert (start, end) == (date(2026, 9, 1), date(2026, 11, 20))


def test_last_n_weeks_and_last_n_days_use_the_same_complete_unit_logic():
    today = date(2026, 11, 20)  # a Friday
    monday_this_week = date(2026, 11, 16)
    start, end, _ = _resolve_relative_period("last 2 weeks", today=today)
    assert (start, end) == (monday_this_week - timedelta(weeks=2), monday_this_week - timedelta(days=1))
    start, end, _ = _resolve_relative_period("last 7 days", today=today)
    assert (start, end) == (today - timedelta(days=7), today - timedelta(days=1))
    start, end, _ = _resolve_relative_period("last 7 days including today", today=today)
    assert (start, end) == (today - timedelta(days=6), today)


def test_last_week_of_a_named_month_is_not_treated_as_relative():
    """"the last week OF January 2026" names an absolute week inside a stated
    month -- it must not be mistaken for "last week" relative to today."""
    assert _resolve_relative_period("evidence for the last week of January 2026", today=date(2026, 11, 20)) is None


def test_resolve_period_resolves_last_n_months_end_to_end_with_no_model_extraction():
    """The model is told to leave period=null for relative wording, so `extracted`
    is None here -- the relative resolver must still fire as the final fallback."""
    period, src = _resolve_period(
        None, None, "give evidence for ACC100450 only for last 2 months",
        today=date(2026, 11, 20),
    )
    assert period["from"] == "2026-09-01" and period["to"] == "2026-10-31"
    assert src == "extracted_relative"
    assert "note" in period


def test_entered_dates_still_win_over_anything_extracted():
    period, src = _resolve_period(
        {"from": "2026-01-01", "to": "2026-03-31"},
        PeriodSpec(from_date="2026-04-01", to_date="2026-06-30", source_text="Q2 2026"),
        "Q2 2026",
    )
    assert period == {"from": "2026-01-01", "to": "2026-03-31"} and src == "entered"


# ---------------------------------------------------------------- quoted source_text (Step 20)

def test_model_wrapped_quotes_are_stripped_before_the_verbatim_check():
    """Asked to copy words verbatim, the model often returns them ALREADY QUOTED:
    source_text came back as "'Q2 2026'" while the request says Q2 2026 with no
    quotes. The substring check then failed on the quote characters alone and the
    run died with "Could not find an explicit audit period" -- observed 3/3 live."""
    from agent.decompose import _unquote
    assert _unquote("'Q2 2026'") == "Q2 2026"
    assert _unquote('"covering 1 Jan 2026 to 31 Mar 2026"') == "covering 1 Jan 2026 to 31 Mar 2026"
    assert _unquote("“June 2026”") == "June 2026"
    # an apostrophe inside the span must survive
    assert _unquote("the lender's policy") == "the lender's policy"
    assert _unquote("Q2 2026") == "Q2 2026"


def test_resolve_period_accepts_a_quarter_the_model_quoted():
    period, src = _resolve_period(
        None, PeriodSpec(from_date="2026-04-01", to_date="2026-06-30", source_text="'Q2 2026'"),
        "do audit for ACC100001 for Q2 2026, do all checks",
    )
    assert period == {"from": "2026-04-01", "to": "2026-06-30"} and src == "extracted"
