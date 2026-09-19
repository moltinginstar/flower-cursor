from flower_cursor.cursor_report import CursorReportParser
from flower_cursor.lifecycle import CursorCell


def test_parser_extracts_reports_and_preserves_normal_input() -> None:
    parser = CursorReportParser()

    passthrough, reports = parser.feed(b"hello\x1b[3;9Rworld")

    assert passthrough == b"helloworld"
    assert reports[0].cell == CursorCell(2, 8)
    assert reports[0].raw == b"\x1b[3;9R"


def test_parser_handles_a_report_split_across_reads() -> None:
    parser = CursorReportParser()

    first_passthrough, first_reports = parser.feed(b"before\x1b[12;")
    second_passthrough, second_reports = parser.feed(b"4Rafter")

    assert first_passthrough == b"before"
    assert first_reports == ()
    assert second_passthrough == b"after"
    assert second_reports[0].cell == CursorCell(11, 3)


def test_parser_does_not_consume_other_csi_sequences() -> None:
    parser = CursorReportParser()

    passthrough, reports = parser.feed(b"\x1b[2J\x1b[H")

    assert passthrough == b"\x1b[2J\x1b[H"
    assert reports == ()
