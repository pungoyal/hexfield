"""Tests never touch your saved games: they get a folder of their own."""

import os
import tempfile

os.environ["HEXFIELD_HOME"] = tempfile.mkdtemp(prefix="hexfield-tests-")
