# Changelog

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
