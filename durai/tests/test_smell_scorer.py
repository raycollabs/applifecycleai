"""Sample: Test detection of code smells and score adjustment invariants."""
import pytest
from durai.smell_scorer import (
    find_python_smells,
    analyze_code_smells,
    score_with_smell_guard,
)


class TestSmellScorer:
    def test_finds_broad_exception_pass(self):
        # Sample: Analyze function catching Exception with pass. Verify BROAD_EXCEPTION smell is reported.
        bad_code = (
            "def handle_trade():\n"
            "    try:\n"
            "        do_work()\n"
            "    except Exception:\n"
            "        pass\n"
        )
        smells = find_python_smells("trade.py", bad_code)
        assert any(s["kind"] == "BROAD_EXCEPTION" for s in smells)

    def test_finds_hardcoded_secrets(self):
        # Sample: Source containing api_key string. Verify HARDCODED_SECRET smell is detected.
        secret_code = "API_KEY = 'secret_live_tok_99182391293'\n"
        smells = find_python_smells("config.py", secret_code)
        assert any(s["kind"] == "HARDCODED_SECRET" for s in smells)

    def test_finds_raw_sql_format(self):
        # Sample: Query string formatted with %s or f-string. Verify RAW_SQL_FORMAT detected.
        sql_code = 'query = f"SELECT * FROM trades WHERE account = \'{acc}\'"\n'
        smells = find_python_smells("db.py", sql_code)
        assert any(s["kind"] == "RAW_SQL_FORMAT" for s in smells)

    def test_score_guard_enforces_executed_checks(self):
        # Sample: Base score 4 without passing executed checks. Verify score drops to 0.
        clean_report = {"penalty": 0, "smells": [], "clean": True}
        assert score_with_smell_guard(4, False, clean_report) == 0
        assert score_with_smell_guard(4, True, clean_report) == 4

    def test_score_guard_applies_smell_penalty(self):
        # Sample: Base score 4 with penalty 2 and passing checks. Verify final score is 2.
        dirty_report = {"penalty": 2, "smells": [{"kind": "HARDCODED_SECRET"}], "clean": False}
        assert score_with_smell_guard(4, True, dirty_report) == 2
