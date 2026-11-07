"""Keyboard input never means two things: hex letters avoid every single-key command."""
import pathlib
import re
import unittest

from hexfield.board import HEX_LETTERS, Board

ROOT = pathlib.Path(__file__).resolve().parents[1]


def html_scopes():
    """data-key labels per scope in index.html: the main screen, then each dialog."""
    html = (ROOT / "hexfield/web/static/index.html").read_text()
    main, _, dialogs = html.partition("<!-- ============ dialogs")
    scopes = {"main": re.findall(r'data-key="([^"]+)"', main)}
    for name, body in re.findall(r'<dialog id="([^"]+)".*?>(.*?)</dialog>', dialogs, re.S):
        scopes[name] = re.findall(r'data-key="([^"]+)"', body)
    return scopes


def web_shortcuts():
    """Single-letter keys on the main screen (the board letters skip these)."""
    return {k.lower() for k in html_scopes()["main"] if re.fullmatch(r"[A-Z]", k)}


def cli_commands():
    py = (ROOT / "hexfield/cli.py").read_text()
    block = py[py.index("def common"):]
    block = block[:block.index("\n    def ", 1)]
    words = set()
    for group in re.findall(r'cmd in \(([^)]*)\)|cmd == "([^"]*)"', block):
        for part in group:
            words.update(re.findall(r'"([^"]*)"', part) or ([part] if part else []))
    return words


class KeyTests(unittest.TestCase):
    def test_hex_letters_cover_the_board_once(self):
        b = Board(seed=1)
        labels = [b.hex_label(h) for h in range(b.H)]
        self.assertEqual(len(set(labels)), b.H)
        for h, s in enumerate(labels):
            self.assertEqual(b.hex_from_label(s.lower()), h)

    def test_keys_unique_per_scope(self):
        for scope, keys in html_scopes().items():
            dupes = {k for k in keys if keys.count(k) > 1}
            self.assertFalse(dupes, f"{scope} reuses keys {dupes}")

    def test_web_shortcuts_avoid_hex_letters(self):
        keys = web_shortcuts()
        self.assertIn("r", keys)  # parsed the main screen
        clash = {k.upper() for k in keys} & set(HEX_LETTERS)
        self.assertFalse(clash, f"web shortcuts reuse hex letters: {clash}")

    def test_cli_commands_avoid_hex_letters(self):
        cmds = cli_commands()
        self.assertIn("h", cmds)
        clash = {c.upper() for c in cmds if len(c) == 1} & set(HEX_LETTERS)
        self.assertFalse(clash, f"terminal commands reuse hex letters: {clash}")


if __name__ == "__main__":
    unittest.main()
