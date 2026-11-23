# Changelog

## Unreleased

* Hosting: the repository moved to `pungoyal/hexfield`, so the image is now
  `ghcr.io/pungoyal/hexfield`. `GET /healthz` names the build it runs, and
  `python -m hexfield.backup SOURCE DEST` writes a Hexfield home to one `.tar.gz` (the
  production stack runs it before every start).
* Picking a spot (setup, a build mode, the bandit) starts on a raw board: every open spot
  looks the same, with no letters, pip totals or best spot. Press Space to show them for that
  decision; Space again builds on the best spot. Phones keep showing pips.
* The game shows who it is waiting for: their card pulses, the status line names them,
  and online the tab title says "Your move".
* Online, a trade offer unanswered for 30 seconds counts as declined.
* Phones: the whole game fits one screen. A slim bar, the players as a strip of counters
  (tap for the full cards), the board from edge to edge, your cards as five counters, the
  builds and one big button. News is a single line across the top, never over the line
  that says what to do.
* Number markers, docks and the bandit are quieter: flat paper plaques with ink numbers
  and a five-dot gauge; the two busiest numbers are heavier and warmer, nothing glows.

## 3.0.0

Games saved by 2.x no longer load (see the renaming below). They are kept on disk; the saved
games list says how many there are, and opening one says which version wrote it.

* Phones: the turn recap and the replay controls sit under the board instead of on it,
  saved games are a list of cards with every action in reach, a hint button sits beside
  End turn, the panels follow in the order you look things up (players, coach, income,
  bank, log), and board spots are larger under a finger. The new game dialog and the bank
  trades no longer run off the screen or lose their explanations.
* Toasts keep clear of open dialogs and of the status line, and the result screen opens
  without any on top of it. Its five actions wrap instead of sliding out of the dialog.
* Take-backs in shared games: your own last decision, until someone else has decided.
  Around one screen it asks first; online every other player has to allow it.
* **No bot offers** (`Shift+M`): bots stop asking you to trade.
* Number markers, docks and the bandit have a quiet look of their own: flat paper plaques
  with ink numbers and a five-dot gauge of how often each is rolled (the two busiest
  numbers are heavier and warmer, nothing glows), and a plain dark tent for the bandit.
* Choosing Online or Pass and play keeps the table at four players unless you pick the bots
  yourself, and the dialog says what that mode is. An online host called "You" is "Host".
* Space ends the turn once nothing else is left to do.
* The coach's numbers are said one way everywhere: accuracy is the share of decisions rated
  best or good, and "fame given up" (1.00 is about one village) replaces "fame-eq".
* Saved games show the bots' strength and the seed, and unfinished ones can be deleted
  together.
* Runs on Python 3.11 and newer (CI tests 3.11 and 3.15), and the Dockerfile builds for the
  machine it is built on instead of arm64 only.

* Accounts for a public server (`--accounts`). Anyone can sign up with a passkey, with no
  password or email, and join the games they're invited to under their own name. Joining
  needs an account. The admin, whoever holds the host key, chooses which accounts may host,
  and each host sees only their own saved games. Passkeys are checked with the standard
  library alone (ES256), and accounts live in `accounts.db` beside the saved games.
  hex.vaari.dev runs with accounts on.
* `--behind-proxy`: behind a reverse proxy on another machine or container, the request
  allowance goes by the client's address in `X-Forwarded-For` instead of the proxy's, so
  players no longer share one allowance. hex.vaari.dev uses it.

* Hexfield is open source under the MIT license.
* CI uses Node 26 and the latest GitHub Actions, and runs on arm64.
* The published Docker image is linux/arm64, on `python:3.15-slim`.
* Deployment to hex.vaari.dev. Every push to `main` that passes CI publishes
  `ghcr.io/vaari-dev/hexfield`. With `DEPLOY_ENABLED` set, CI deploys it over SSH as its own
  Compose project behind the shared Caddy, writes the server's settings from GitHub, waits
  for the health check and puts the previous release back if it fails.
* Keys never appear in a URL, so a reverse proxy's access log can't record them. The
  browser sends its seat key in an `Authorization: Bearer` header instead of `?t=`, and the
  host link is `/#host=<key>`: the page posts the key to `/api/host` and drops it from the
  address bar. `/?host=` and `?t=` no longer work; an open tab picks up the change when
  reloaded.
* Hexfield has its own names. Resources: timber, clay, wool, grain and stone. Pieces:
  village, town and trail. Ports are docks, the desert is the badlands and the robber is
  the bandit. Development cards are ventures (ranger, pathfinder, hoard, windfall and
  landmark), victory points are fame, and the awards are Great Trail and Great Patrol.
  Terminal shortcuts follow the new names: `t c w g s` for resources, `v`, `tn`, `tr` and
  `vn` to build, `rg` and `pf` for the cards. Games saved by earlier versions no longer
  load.
* The board no longer zooms in on clustered choices (the trail beside a new village, a
  free trail, a town): the whole island stays in view for every decision.
* Ventures take the emphasis instead. A playable venture rises out of the tray, larger
  and ringed in gold, springing up once as it becomes playable, while the rest of the
  hand steps back. Pointing at a venture, focusing it or pressing and holding it opens
  a close-up with what it does and whether it can be played now. The status line names
  the ventures you can play and their keys, and the reason a venture is blocked sits on
  the card's edge instead of over its picture.

## 2.1.0

Web client

* On your move, the controls that advance the game are drawn larger, board targets arrive
  best-first, and the camera zooms onto clustered choices.
* All artwork is illustrated vector art drawn in the browser: a unique landscape per hex,
  coast and ports, shaded pieces, number tokens, card faces and dice.
* Undo is `U` on every screen.

Server

* Host key (`--host-key` / `HEXFIELD_HOST_KEY`) for servers you can't sit at, `--allow-host`
  for domain names, a Dockerfile, `GET /healthz`, clean stop on SIGTERM.
* Refuses unknown `Host` names (DNS rebinding) and cross-site posts; a proxied request is
  never treated as the host machine.
* Someone who is not the host only finds a game through its invite link.
* Content security policy and related headers on every response.
* Per-address request allowance, a cap on requests in flight, and a socket timeout.
* Static files are sent gzipped with an `ETag` and revalidated instead of re-downloaded.
* `--verbose` access log (seat keys left out); error details stay on the server.
* The saved-games folder is created private to your user.

## 2.0.0

Saved games and replays, 5-6 players, pass-and-play and online multiplayer, counter-offers,
bot styles, balanced dice, and the self-play and tuning tools.
