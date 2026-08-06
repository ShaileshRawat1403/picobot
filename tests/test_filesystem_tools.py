"""Regression coverage for the filesystem tools (read/write/edit/list_dir).

These tools run on nearly every agent turn and had zero test coverage before
this file. Covers the documented behavior: pagination, allowed-dir
enforcement, exact and fuzzy-match editing, replace_all/ambiguous-match
handling, CRLF preservation, and directory listing/ignoring.
"""

from __future__ import annotations

from pathlib import Path

from picobot.agent.tools.filesystem import (
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
    _find_match,
)


# ---------------------------------------------------------------------------
# ReadFileTool
# ---------------------------------------------------------------------------


async def test_read_file_returns_numbered_lines(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("one\ntwo\nthree\n", encoding="utf-8")
    tool = ReadFileTool()
    result = await tool.execute(str(f))
    assert "1| one" in result
    assert "2| two" in result
    assert "3| three" in result
    assert "End of file" in result


async def test_read_file_paginates_with_offset_and_limit(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("\n".join(str(i) for i in range(1, 11)), encoding="utf-8")
    tool = ReadFileTool()
    result = await tool.execute(str(f), offset=3, limit=2)
    assert "3| 3" in result
    assert "4| 4" in result
    assert "5| 5" not in result
    assert "Showing lines 3-4 of 10" in result


async def test_read_file_missing_file_reports_error(tmp_path: Path):
    tool = ReadFileTool()
    result = await tool.execute(str(tmp_path / "nope.txt"))
    assert "not found" in result.lower()


async def test_read_file_empty_file(tmp_path: Path):
    f = tmp_path / "empty.txt"
    f.write_text("", encoding="utf-8")
    tool = ReadFileTool()
    result = await tool.execute(str(f))
    assert "Empty file" in result


async def test_read_file_offset_beyond_end_errors(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("one\ntwo\n", encoding="utf-8")
    tool = ReadFileTool()
    result = await tool.execute(str(f), offset=99)
    assert "beyond end of file" in result


async def test_read_file_rejects_path_outside_allowed_dir(tmp_path: Path):
    allowed = tmp_path / "workspace"
    allowed.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    tool = ReadFileTool(allowed_dir=allowed)
    result = await tool.execute(str(outside))
    assert "Error" in result
    assert "outside allowed directory" in result


async def test_read_file_relative_path_resolves_against_workspace(tmp_path: Path):
    (tmp_path / "sub.txt").write_text("hi", encoding="utf-8")
    tool = ReadFileTool(workspace=tmp_path)
    result = await tool.execute("sub.txt")
    assert "1| hi" in result


# ---------------------------------------------------------------------------
# WriteFileTool
# ---------------------------------------------------------------------------


async def test_write_file_creates_file_and_parent_dirs(tmp_path: Path):
    target = tmp_path / "nested" / "dir" / "out.txt"
    tool = WriteFileTool()
    result = await tool.execute(str(target), "hello world")
    assert "Successfully wrote" in result
    assert target.read_text(encoding="utf-8") == "hello world"


async def test_write_file_rejects_path_outside_allowed_dir(tmp_path: Path):
    allowed = tmp_path / "workspace"
    allowed.mkdir()
    outside = tmp_path / "escape.txt"
    tool = WriteFileTool(allowed_dir=allowed)
    result = await tool.execute(str(outside), "malicious")
    assert "Error" in result
    assert not outside.exists()


# ---------------------------------------------------------------------------
# _find_match (exact + fuzzy line-trimmed matching used by EditFileTool)
# ---------------------------------------------------------------------------


def test_find_match_exact():
    content = "line1\nline2\nline3\n"
    match, count = _find_match(content, "line2")
    assert match == "line2"
    assert count == 1


def test_find_match_falls_back_to_whitespace_trimmed_window():
    content = "def f():\n    return 1\n"
    # old_text has different indentation/whitespace than the actual content
    match, count = _find_match(content, "def f():\n  return 1")
    assert match == "def f():\n    return 1"
    assert count == 1


def test_find_match_returns_none_when_nothing_similar():
    content = "alpha\nbeta\n"
    match, count = _find_match(content, "completely different text")
    assert match is None
    assert count == 0


# ---------------------------------------------------------------------------
# EditFileTool
# ---------------------------------------------------------------------------


async def test_edit_file_replaces_exact_match(tmp_path: Path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\ny = 2\n", encoding="utf-8")
    tool = EditFileTool()
    result = await tool.execute(str(f), "x = 1", "x = 100")
    assert "Successfully edited" in result
    assert f.read_text(encoding="utf-8") == "x = 100\ny = 2\n"


async def test_edit_file_missing_file_reports_error(tmp_path: Path):
    tool = EditFileTool()
    result = await tool.execute(str(tmp_path / "nope.py"), "a", "b")
    assert "not found" in result.lower()


async def test_edit_file_ambiguous_match_requires_replace_all(tmp_path: Path):
    f = tmp_path / "a.py"
    f.write_text("dup\ndup\n", encoding="utf-8")
    tool = EditFileTool()
    result = await tool.execute(str(f), "dup", "single")
    assert "appears 2 times" in result
    # File must be untouched since the ambiguous edit was refused.
    assert f.read_text(encoding="utf-8") == "dup\ndup\n"


async def test_edit_file_replace_all_replaces_every_occurrence(tmp_path: Path):
    f = tmp_path / "a.py"
    f.write_text("dup\ndup\n", encoding="utf-8")
    tool = EditFileTool()
    result = await tool.execute(str(f), "dup", "single", replace_all=True)
    assert "Successfully edited" in result
    assert f.read_text(encoding="utf-8") == "single\nsingle\n"


async def test_edit_file_not_found_reports_similarity_diff(tmp_path: Path):
    f = tmp_path / "a.py"
    f.write_text("def greet():\n    print('hello')\n", encoding="utf-8")
    tool = EditFileTool()
    result = await tool.execute(str(f), "def greet():\n    print('hallo')\n", "x")
    assert "old_text not found" in result
    assert "similar" in result.lower()


async def test_edit_file_preserves_crlf_line_endings(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"one\r\ntwo\r\n")
    tool = EditFileTool()
    result = await tool.execute(str(f), "two", "TWO")
    assert "Successfully edited" in result
    assert f.read_bytes() == b"one\r\nTWO\r\n"


# ---------------------------------------------------------------------------
# ListDirTool
# ---------------------------------------------------------------------------


async def test_list_dir_lists_files_and_directories(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    tool = ListDirTool()
    result = await tool.execute(str(tmp_path))
    assert "a.txt" in result
    assert "sub" in result


async def test_list_dir_ignores_noise_directories(tmp_path: Path):
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "real.txt").write_text("x", encoding="utf-8")
    tool = ListDirTool()
    result = await tool.execute(str(tmp_path))
    assert "__pycache__" not in result
    assert "real.txt" in result


async def test_list_dir_recursive_finds_nested_files(tmp_path: Path):
    nested = tmp_path / "sub" / "deeper"
    nested.mkdir(parents=True)
    (nested / "leaf.txt").write_text("x", encoding="utf-8")
    tool = ListDirTool()
    result = await tool.execute(str(tmp_path), recursive=True)
    assert "leaf.txt" in result


async def test_list_dir_missing_directory_reports_error(tmp_path: Path):
    tool = ListDirTool()
    result = await tool.execute(str(tmp_path / "nope"))
    assert "not found" in result.lower()


async def test_list_dir_empty_directory(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    tool = ListDirTool()
    result = await tool.execute(str(empty))
    assert "empty" in result.lower()


async def test_list_dir_truncates_at_max_entries(tmp_path: Path):
    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
    tool = ListDirTool()
    result = await tool.execute(str(tmp_path), max_entries=3)
    assert "truncated, showing first 3 of 10" in result
