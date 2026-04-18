"""Tests for issue #233 — markdown rendering in the console chat panel.

The console renders every message as a two-part block:

    [ts] icon nick:
      <markdown body rendered here>

We unit-test:

* the pure ``build_message_header`` helper (header construction)
* end-to-end rendering of a ``rich.markdown.Markdown`` body through a
  ``rich.console.Console`` so the assertions exercise the same Rich
  pipeline ``RichLog`` uses internally.

The Textual app itself is not spun up — that surface is covered by the
existing ``tests/test_console_*.py`` files.
"""

from __future__ import annotations

import io
import re
from datetime import datetime

from rich.console import Console
from rich.markdown import Markdown

from culture.console.widgets.chat import build_message_header


def _ts(timestamp: float) -> str:
    """Render ``timestamp`` the same way ``build_message_header`` does.

    Local-time formatting depends on the runner's timezone, so we derive
    the expected ``HH:MM`` here instead of hard-coding it.
    """
    return datetime.fromtimestamp(timestamp).strftime("%H:%M")


# ---------------------------------------------------------------------------
# build_message_header
# ---------------------------------------------------------------------------


class TestBuildMessageHeader:
    """The header is ``[ts] icon nick:``, with ``ts`` dim and ``nick`` bold."""

    def test_plain_text_content(self):
        header = build_message_header(0.0, "🤖", "thor-claude")
        assert header.plain == f"{_ts(0.0)} 🤖 thor-claude:"

    def test_no_icon_omits_icon_segment(self):
        header = build_message_header(0.0, "", "spark-ori")
        assert header.plain == f"{_ts(0.0)} spark-ori:"

    def test_timestamp_is_dim_styled(self):
        header = build_message_header(0.0, "🤖", "thor-claude")
        # First span: timestamp marked dim.
        spans = [(s.start, s.end, str(s.style)) for s in header.spans]
        assert any("dim" in style for _, _, style in spans)

    def test_nick_is_bold_styled(self):
        header = build_message_header(0.0, "🤖", "thor-claude")
        spans = [(s.start, s.end, str(s.style)) for s in header.spans]
        assert any("bold" in style for _, _, style in spans)

    def test_brackets_in_nick_are_literal(self):
        # No nick contains [bold] in practice, but the contract is that
        # build_message_header returns a Text — so any markup-looking
        # substring is rendered verbatim, never reparsed.
        header = build_message_header(0.0, "", "[bold]X[/]")
        assert header.plain == f"{_ts(0.0)} [bold]X[/]:"


# ---------------------------------------------------------------------------
# Markdown rendering integration — same path RichLog takes
# ---------------------------------------------------------------------------


def _render(renderable, *, width: int = 80) -> str:
    """Render ``renderable`` through a Rich ``Console`` and return raw output.

    ``record=True`` is unsuitable here because ``export_text`` strips ANSI
    sequences — we need them so we can assert on bold/italic/hyperlink
    escapes. Use a ``StringIO`` file with ``force_terminal=True`` so Rich
    emits the same ANSI it would emit to a real terminal.
    """
    buf = io.StringIO()
    console = Console(
        file=buf,
        width=width,
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
    )
    console.print(renderable)
    return buf.getvalue()


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


class TestMarkdownInlineFormatting:
    """Inline markdown elements render with the right ANSI styling."""

    def test_plain_text_passes_through(self):
        out = _render(Markdown("hello world"))
        assert "hello world" in _strip_ansi(out)

    def test_bold(self):
        out = _render(Markdown("**important**"))
        # Bold is SGR 1.
        assert "\x1b[1m" in out
        assert "important" in _strip_ansi(out)

    def test_italic(self):
        out = _render(Markdown("*sigh*"))
        # Italic is SGR 3.
        assert "\x1b[3m" in out
        assert "sigh" in _strip_ansi(out)

    def test_inline_code(self):
        out = _render(Markdown("the `name` field"))
        assert "name" in _strip_ansi(out)
        # Rich styles inline code with a distinct style — not just plain text.
        assert _strip_ansi(out) != out, "inline code should produce ANSI styling"

    def test_link_emits_osc8_hyperlink(self):
        out = _render(Markdown("[Anthropic](https://anthropic.com)"))
        # OSC 8 hyperlinks: ESC ] 8 ; ; URL ST ... ESC ] 8 ; ; ST
        assert "\x1b]8;" in out, "link should produce OSC 8 hyperlink escape"
        assert "https://anthropic.com" in out
        assert "Anthropic" in _strip_ansi(out)


class TestMarkdownBlockElements:
    """Block elements render across multiple lines / with structure."""

    def test_heading(self):
        out = _render(Markdown("# Title"))
        assert "Title" in _strip_ansi(out)
        # Headings carry styling.
        assert _strip_ansi(out) != out

    def test_fenced_code_block_python(self):
        text = "```python\ndef f():\n    return 1\n```"
        out = _render(Markdown(text))
        plain = _strip_ansi(out)
        assert "def f():" in plain
        assert "return 1" in plain
        # Pygments-via-Rich syntax highlighting emits ANSI.
        assert _strip_ansi(out) != out

    def test_bullet_list(self):
        text = "- one\n- two\n- three"
        out = _render(Markdown(text))
        plain = _strip_ansi(out)
        assert "one" in plain
        assert "two" in plain
        assert "three" in plain

    def test_table(self):
        text = "| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"
        out = _render(Markdown(text))
        plain = _strip_ansi(out)
        # All cell values present.
        for cell in ("a", "b", "1", "2", "3", "4"):
            assert cell in plain


class TestRichMarkupNotReinterpreted:
    """Issue #233 footgun: ``[bold]X[/]`` in agent text must stay literal.

    Rich's ``Markdown`` does **not** parse Rich markup — ``[bold]X[/]`` ends
    up as plain text inside the rendered paragraph. This test guards against
    a future regression where someone passes the body as a markup string.
    """

    def test_rich_markup_is_literal(self):
        out = _render(Markdown("danger: [bold]X[/] do not parse this"))
        plain = _strip_ansi(out)
        assert "[bold]X[/]" in plain
        # And specifically: there should not be a bold-on escape immediately
        # before the literal "X" — confirm the bold SGR open code (\x1b[1m)
        # does not wrap a bare "X" in this output.
        assert "\x1b[1mX\x1b[" not in out
