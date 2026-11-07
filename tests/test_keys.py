"""Keyboard input never means two things: hex letters avoid every single-key command."""
import pathlib
import re
import unittest

from hexfield.board import HEX_LETTERS, Board

ROOT = pathlib.Path(__file__).resolve().parents[1]


def web_shortcuts():
    js = (ROOT / "hexfield/web/static/app.js").read_text()
    block = re.search(r"const SHORTCUTS = \{(.*?)\n\};", js, re.S).group(1)
    return set(re.findall(r'^\s*"?([^\s":]+)"?:', block, re.M))


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

    def test_web_shortcuts_avoid_hex_letters(self):
        keys = web_shortcuts()
        self.assertIn("r", keys)  # parsed the table
        clash = {k.upper() for k in keys if len(k) == 1} & set(HEX_LETTERS)
        self.assertFalse(clash, f"web shortcuts reuse hex letters: {clash}")

    def test_cli_commands_avoid_hex_letters(self):
        cmds = cli_commands()
        self.assertIn("h", cmds)
        clash = {c.upper() for c in cmds if len(c) == 1} & set(HEX_LETTERS)
        self.assertFalse(clash, f"terminal commands reuse hex letters: {clash}")


if __name__ == "__main__":
    unittest.main()
