# Changelog

## Unreleased

* Hexfield is open source under the MIT license.
* Needs Python 3.15. CI uses Node 26 and the latest GitHub Actions, and runs on arm64.
* The Docker image is built for linux/arm64, on `python:3.15-slim`.
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
