# Contributing

Thanks for helping. A few things keep Hexfield easy to work on:

* **No dependencies.** The game is standard-library Python (3.11 or newer) and a plain HTML/CSS/JS
  client with no build step. Please keep it that way. Tools for working on it (ruff, build,
  twine) are pinned in `pyproject.toml`'s `dev` group and never ship.
* **Tests and lint.** In a venv, `python3 -m pip install --group dev` and
  `git config core.hooksPath .githooks` once; the pre-commit hook then runs ruff and the
  tests. With Node 26 installed the tests include the browser client's. For changes to the browser client,
  also run `HEXFIELD_BROWSER_TESTS=1 python3 -m unittest tests.test_js` (needs Chrome or
  Chromium).
* **Seeds stay valid.** A saved game is a seed plus a list of decisions, so a change to
  the rules or the random streams must not change what an existing seed deals. New rules
  go behind an option that is off by default.
* **Bot strength is measured, not guessed.** If a change is meant to make the bots
  stronger, show a `tools/selfplay.py` result whose 95% interval is above 50%.
* **Keep commits focused.** Describe what changed and why, and add a line to
  `CHANGELOG.md` for anything a player would notice.
