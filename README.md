# Hexfield

A hex-board settling and trading game for 3 to 6 players, with tough bots and a built-in
coach. Play in a coloured terminal or in the browser, alone or with friends. It's pure
Python with no dependencies, plus a plain HTML/CSS/JS client with no build step.

* **Bots that play well.** Each bot evaluates the position, plans its whole turn and
  never misses a win it can see. Bots have their own play styles, and they trade with you
  and with each other.
* **A coach.** It gives hints for the current decision, scores every player, reviews each
  move you make and writes an end-of-game report. Statistics across games show where your
  points go.
* **Fair hidden information.** The bots and the coach see only what a player at the table
  could see.
* **Saves and replays.** Every game is saved as you play. Undo any decision, resume
  later, replay a game move by move, or play on from any position.
* **Multiplayer.** Play pass-and-play on one screen, or online with one device each. The
  server is small enough to host for friends.

## Quick start

```
pip install .                          # adds the commands hexfield and hexfield-web
hexfield                               # you + 3 hard bots in the terminal
hexfield-web                           # the same in your browser, at http://127.0.0.1:8765
```

Running from a checkout with `python3 -m hexfield` and `python3 -m hexfield.web` works
just as well. Hexfield needs Python 3.11 or newer.

```
hexfield -o 2                          # you + 2 bots
hexfield -o 5                          # six players: the large board and the special building phase
hexfield -l medium                     # easier bots (easy | medium | hard)
hexfield --seed 1234 --seat 1          # replay the exact board and dice, you go first
hexfield --dice balanced --layout beginner   # dealt dice, the fixed beginner map
hexfield --fast                        # no pause between bot actions
hexfield --no-review --no-reasoning    # play "blind"
hexfield --resume                      # pick up your last unfinished game
hexfield --games                       # your saved games; --replay ID steps through one
hexfield --stats                       # accuracy across your finished games, and where the points go
```

## In the browser

The browser client uses the same engine, bots and coach. It has an SVG board drawn as
vector art (every hex is its own small landscape, and there are no image files), animated
dice and production, a card tray, trade dialogs for the bank, docks and players, and a
coach panel. Reloading the page resumes the game, and so does restarting the server.

* **What you can do stands out.** On your move, everything you can press is drawn larger:
  the Roll button, the builds you can afford, a playable venture, the spots on the
  board. The board itself never moves, so you always see the whole island. A venture
  stands up out of the tray, ringed in gold, the moment it becomes playable; point at
  any venture (or focus it, or press and hold on a touch screen) for a close-up with
  what it does and whether you can play it now.
* **Information where you decide.** The left panel shows your income: pips per resource,
  the numbers you collect on, what the bandit blocks, and your trade rates. Village
  spots show their pip value. When the bandit moves, each hex shows whose income it would
  block. Every dialog that changes your hand says which builds it unlocks or gives up, and
  a **While you waited** card sums up what happened since your last turn.
* **Coach panel.** **Hint** shows the engine's top options on the board ("2 trails toward
  8 grain · 3:1 dock"), and **Play** carries one out, bank trades included. **Evaluate**
  scores every player. Each of your decisions gets a verdict, and only inaccuracies and
  worse pop up. Bots explain why they decline your offers.
* **Undo** works at any moment when you play alone: while the bots play, in the discard
  and offer dialogs, and after the game ends. (Shared games: see take-backs below.)
* **On a phone** the whole game is one screen: a slim bar that says what to do, the
  players as a strip of counters (tap it for the full cards), the board from edge to
  edge, your cards as five counters and one big button. Coach, income, bank and log are
  a scroll away, a hint is one tap from your hand, and saved games are a list of cards.
* **No bot offers** (`Shift+M`, beside the log) stops the bots asking you to trade. You
  can still make offers to them, and they keep trading with each other.
* **Accessible.** Every spot you can build on is a real button that a screen reader can
  announce ("Build a village: 8 grain, 5 wool, 9 pips"). **Marks** (`Shift+C`)
  switches to a colour-blind-safe palette and puts each player's seat number on their
  pieces. Animations follow the system's reduced-motion setting.

Everything can be done from the keyboard: `R` roll, `1/2/3` trail/village/town,
`D` venture, `T` trade, `E` end turn, `H` hint, `U` undo (on every screen and in
every dialog), `?` help. When you place something, every clickable spot shows a letter,
ranked rather than positional: the best spot is always **A** (in green), then S, F, G…
along the home row. **Space** always makes the obvious move, the one ringed in green: the A spot, the
roll, the recommended answer in a dialog, or End turn once nothing else is left to do.

```
hexfield-web --port 9000 --no-browser
```

## In the terminal

The board and side panel need about **132 columns × 40 rows**. In a narrower window the
panel moves below the board. The game uses 256-colour ANSI. `--no-color` (or
`NO_COLOR=1`) turns colour off.

Before each move a status line says what you can build now, or how many trades away the
nearest build is. Placing a village lists the open spots with the most pips. Moving the
bandit shows whom each hex blocks and whom you can rob. A bot's offer shows your hand
before and after. Hints show the command to type, e.g. `→ t wool clay`.

| command | |
|---|---|
| `r` | roll (rolled for you unless you can play a ranger first; `--no-autoroll` to always ask) |
| `v 23` / `v` | village at vertex 23 (bare `v` shows the legal vertex ids on the map) |
| `tn 23` | town |
| `tr 41` / `tr` | trail on edge 41 (bare `tr` shows the legal edge ids) |
| `vn` | buy a venture |
| `rg`, `pf`, `hoard stone`, `windfall grain stone` | play ventures |
| `t timber stone` / `t timber stone 3` | bank/dock trade at your best rate; the number is how many trades (all or none) |
| `offer 2 timber for stone` | offer a trade to the bots; one that says no may name its price |
| `c timber for stone` | at a bot's offer: your counter-offer (you give timber, you get stone) |
| `e` | end turn (or pass, in the special building phase) |
| `h` / `hint` | the engine's top 5 options for the current decision, with reasons |
| `eval` | engine evaluation of every player, plus production (pips per resource) |
| `review on/off`, `reason on/off` | post-move review / bot reasoning (bare `review` shows the setting) |
| `undo` | take back your last decision, at any prompt. Dice and the deck stay the same, so you can try another line |
| `b`, `ids`, `log 30`, `stats`, `cost`, `help`, `q` | view and help commands |

Hexes have fixed letters for the bandit, e.g. `F` or `F vera`. The letters never clash
with a command (no B, D, E, H, Q, R or T). `v` is a command at the main
prompt and a hex letter at the bandit prompt. At the discard prompt, card names come first:
`c t t` discards clay, timber, timber.

## Saved games, replays and progress

Every game is saved as you play, in `~/.hexfield/games` (set `HEXFIELD_HOME` to move it).
A save stores the seed, the table and the list of decisions, so it stays valid when the
bots change. Quit at any point and resume later, in the terminal or the browser, with your
undo history intact.

In the browser, **Games** (`Shift+G`) lists your saved games. **Replay** steps through a
game move by move (`←` `→`; `[` `]` jump between your own decisions; `Space` plays). At
each of your decisions it shows what you played, the coach's verdict and the engine's line.
**All cards** turns every hand face up. **Play on from here** starts a new game from that
position with the same board and dice, so you can find out where the other line would
have gone. Games can be exported to a file and imported.

**Your progress** (or `--stats`) adds up your finished solo games: accuracy game by game,
win rate, and where the lost points go (opening, building and trading, bandit, discards,
trade offers, rangers).

## Playing with other people

A new game can seat several humans, with bots in the other chairs (3 to 6 players in all).

* **Pass and play**: one screen. It goes dark between players, and the next player
  confirms before their cards show.
* **Online**: one device each. Start the server with `hexfield-web --lan`, create the
  game and send the others the invite link from the lobby. Each seat has its own secret
  key, kept in that browser. A player only ever receives their own hand. Only the machine
  running the server can start, resume, list or delete games.

Alone, undo steps back as far as you like. In a shared game you can take back your own
last decision, until someone else has decided: around one screen it asks the table first,
and online every other player has to allow it (one refusal and the move stands, and you can ask once per decision). Offers
between humans work like offers to bots: the others accept, decline or answer with a
counter-offer, and the proposer picks.

## Hosting

`--lan` is for people on your own network. To run the server on a machine you can't sit
at (a small VM, a container, a home server behind a reverse proxy), give it a host key:

```
export HEXFIELD_HOST_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
hexfield-web --lan --no-browser --allow-host play.example.com
```

Open `https://play.example.com/#host=<the key>` once. That browser is now the host (a
cookie remembers it for 30 days) and can start, resume and delete games. The key goes after
`#`, which browsers never send to a server, so no proxy can log it: the page posts it and
then removes it from the address bar. Everyone else
only gets into games they hold an invite link for. With a host key set, requests from
the server's own machine are no longer treated as the host.

### Accounts

For a server open to anyone, add `--accounts` (it needs a host key and HTTPS):

```
hexfield-web --lan --no-browser --allow-host play.example.com --accounts
```

Anyone can then make an account with a passkey: a name, and the fingerprint, face or
PIN their device already uses. There's no password or email. An account can join every
game it has an invite link for, playing under its own name; joining needs an account.
Hosting games, saved games and replays is for the accounts the admin allows: the admin is
the browser holding the host key, whose **Admin** button lists the accounts with a
*May host* switch and a *Delete* button for each (deleting removes the account's saved
games too). Names are unique. Each account sees only its own saved games (the 50 newest are
kept), in `games/users/<account>` under `$HEXFIELD_HOME`; the accounts themselves are in
`accounts.db` beside them. One address (one IPv6 /64) can make 5 accounts an hour, and each
host keeps at most 4 games live in memory at once, so nobody can push the others' games out.

Passkeys belong to a domain name, so they work over HTTPS or on `localhost`, never on a
bare IP address.

### Docker

The image builds for the machine you build it on (x86-64 or arm64). Games are kept in the
`/data` volume.

```
docker build -t hexfield .
docker run -p 8765:8765 -v hexfield-data:/data -e HEXFIELD_HOST_KEY="$HEXFIELD_HOST_KEY" hexfield
```

To run the production stack ([`compose.yaml`](compose.yaml)) on your own machine, with the
pre-deploy backup and the same health check, build it and fill in a `.env`:

```
docker build -t hexfield:dev .
cp .env.example .env              # then set HEXFIELD_HOST_KEY, and HEXFIELD_DOMAIN=localhost
docker network create edge        # once; on the server the edge stack creates it
docker compose up -d --wait       # http://127.0.0.1:8765
```

`docker compose` merges in [`compose.override.yaml`](compose.override.yaml) on its own: it
publishes the port on 127.0.0.1 and does nothing else. It is never copied to the server.

The image CI publishes to GHCR is linux/arm64 only, to match the server it deploys to.

### Behind a proxy

The server speaks plain HTTP. On the open internet, put a reverse proxy with TLS in front
of it, because every request carries the player's seat key (in an `Authorization` header,
never the URL, so access logs that drop that header never hold one). With Caddy that's `reverse_proxy
127.0.0.1:8765`; with nginx, `proxy_pass` plus `proxy_set_header Host $host` and
`X-Forwarded-For`. Allow a read timeout of 60 s or more for the long poll.

The request allowances below go by the client's address. The server reads it from the
proxy's `X-Forwarded-For` when the proxy runs on the same machine. When the proxy is
elsewhere (another container) and nothing else can reach the server, say so with
`--behind-proxy`; otherwise every player would share the proxy's allowance. That flag
believes the header from any peer, so use it only where every peer is your proxy (on
hex.vaari.dev, the edge network holds only the shared Caddy and the owner's own apps).

The server also protects itself and its players:

* It only answers to an IP address, `localhost`, a `.local` name or a name given with
  `--allow-host`. It refuses cross-site posts, so a web page you visit can't drive it.
* A request passed on by a proxy never counts as "this machine".
* Responses carry strict browser headers: a content security policy, no framing and no
  `Referer`.
* Each remote address gets a request allowance (HTTP 429 beyond it). At most 200 requests
  run at once, bodies are capped, and a stalled connection is dropped after 30 s.
* Errors are logged on the server. `--verbose` adds an access log; it can't hold a key,
  because no key is ever in a URL. Remote callers only see "internal error".
* `GET /healthz` answers health checks, and SIGTERM stops the server cleanly. Games are
  written after every decision, so a restart loses nothing.

It is built for a table of friends, or a small public server with accounts, not for
strangers at scale: one process holds every live game, and the bots think on the server's
CPU.


### Deploying hex.vaari.dev

The public server runs on one Oracle Cloud ARM VM next to other apps. A single Caddy
([vaari-dev/edge](https://github.com/vaari-dev/edge)) terminates HTTPS for all of them. Its
`sites/hexfield.caddy` sends `hex.vaari.dev` to `hexfield:8765` on the shared Docker
network. Hexfield is its own Compose project ([`compose.yaml`](compose.yaml)), publishes
no ports, and only its one long-running service (also called `hexfield`) joins that network.

On every push to `main`, CI runs every check, builds and smoke-tests one linux/arm64 image,
pushes it to GHCR as `ghcr.io/vaari-dev/hexfield:<commit>` (with a build provenance
attestation), and deploys it. The deploy is two files and one command:

1. CI writes `.env` from the `oracle-cloud` environment: one single-quoted line for each key
   in [`.env.example`](.env.example), the first being `APP_IMAGE`, the image of this commit.
   A missing value stops the deploy before the server is touched.
2. It keeps the serving release's `compose.yaml` and `.env` as `*.previous`, copies the new
   two over, pulls the image, and runs `docker compose up --wait`. The `backup` service runs
   first and the server starts only once it has finished; the app's health check decides
   whether the deploy passed.
3. If the new release doesn't come up healthy within 5 minutes, CI puts the kept files
   back, brings the previous release up the same way, and fails the run.
4. Only after a good deploy does it remove older `ghcr.io/vaari-dev/hexfield` images.

`GET https://hex.vaari.dev/healthz` names the commit that is serving (`build`). To go back
to an older release, re-run the `deploy` job of a good commit's run on `main`: GHCR still
holds its image. To deploy again without a new commit, re-run the latest one.

The server is shared with other apps, so a deploy touches only `/opt/hexfield`, the
`hexfield` Compose project (its containers, the `hexfield_data` volume where games live and
`hexfield_backups`) and older Hexfield images. It joins the edge's network without creating
or changing it. No script, registry login or other logic lives on the server: the
package `ghcr.io/vaari-dev/hexfield` is public, so the server pulls it without a login.

**Backups.** Before every start, `backup` (`python -m hexfield.backup /data /backups`)
writes `hexfield-<UTC time>.tar.gz` to the `hexfield_backups` volume: every saved game and
a consistent copy of `accounts.db`, taken while the old server keeps running. The newest 7
are kept. To restore one, on the server:

```sh
cd /opt/hexfield
docker compose stop hexfield
docker compose run --rm --no-deps -v hexfield_backups:/backups --entrypoint sh hexfield \
  -c 'cd /data && tar -xzf /backups/hexfield-<time>.tar.gz && rm -f accounts.db-wal accounts.db-shm'
docker compose up -d --wait hexfield
```

Settings live in the `oracle-cloud` GitHub environment, which only `main` may deploy to. A
new setting is one line in each of `.env.example`, `compose.yaml` and the deploy job's
`Write .env` step; a blank value stops the deploy.

| Name                 | Where                  | Value                                                   |
| -------------------- | ---------------------- | ------------------------------------------------------- |
| `DOCKER_NETWORK`     | repository variable    | the edge's shared network, exactly as in edge: `edge`   |
| `DEPLOY_HOST`        | environment variable   | the server's public IP                                  |
| `DEPLOY_USER`        | environment variable   | SSH user in the `docker` group (`ubuntu`)               |
| `DEPLOY_KNOWN_HOSTS` | environment variable   | `ssh-keyscan -t ed25519 <host>` output                  |
| `HEXFIELD_DOMAIN`    | environment variable   | `hex.vaari.dev`                                         |
| `DEPLOY_SSH_KEY`     | environment secret     | private half of a deploy key whose public half is on the server |
| `HEXFIELD_HOST_KEY`  | environment secret     | the admin key, which chooses who may host: `openssl rand -hex 32` |

```sh
gh api -X PUT repos/vaari-dev/hexfield/environments/oracle-cloud \
  -F 'deployment_branch_policy[protected_branches]=false' -F 'deployment_branch_policy[custom_branch_policies]=true'
gh api -X POST repos/vaari-dev/hexfield/environments/oracle-cloud/deployment-branch-policies -f name=main
gh variable set DOCKER_NETWORK --body edge
gh variable set HEXFIELD_DOMAIN --env oracle-cloud --body hex.vaari.dev
gh variable set DEPLOY_HOST --env oracle-cloud --body <ip>
gh variable set DEPLOY_USER --env oracle-cloud --body ubuntu
ssh-keyscan -t ed25519 <ip> | gh variable set DEPLOY_KNOWN_HOSTS --env oracle-cloud
gh secret set DEPLOY_SSH_KEY --env oracle-cloud < deploy_key
openssl rand -hex 32 | gh secret set HEXFIELD_HOST_KEY --env oracle-cloud
```

Before the first deploy, once: the edge must be running (it creates the network),
`hex.vaari.dev` needs a DNS-only `A` record pointing at the VM (edge's `scripts/dns.sh`),
the package must be public (its *Package settings* on GitHub), and the server needs the
deploy directory with a first pair of files to keep:

```sh
sudo install -d -o ubuntu -g ubuntu -m 700 /opt/hexfield && touch /opt/hexfield/compose.yaml /opt/hexfield/.env
```

The server runs with [accounts](#accounts) and `--behind-proxy`: open
`https://hex.vaari.dev/#host=<HEXFIELD_HOST_KEY>` once to be the admin.

## Rules

* A random board with balanced tile, number and dock distributions. The two busiest numbers
  (6 and 8, the heavier ones) never touch, and neither do two of the same number. There are 9 docks
  (four 3:1, five 2:1).
* Snake-draft setup. Your second village pays its starting resources.
* Distance rule: no two villages on neighbouring corners. Trails must connect to your
  network, and an opponent's building cuts it.
* Production follows the bank-shortage rule: if the bank can't pay everyone, nobody gets
  that resource (unless only one player is owed it).
* On a 7, everyone holding more than 7 cards discards half. Then the player who rolled
  moves the bandit and steals a card from someone next to it.
* Ventures: 14 rangers, 5 landmarks, 2 pathfinders, 2 hoards and 2
  windfalls. You can't play a card on the turn you buy it, and you can play at most one per
  turn. Rangers may be played before you roll.
* Hidden hands: you see only how many cards each opponent holds.
* Great Patrol (3+ rangers) and Great Trail (5+, recomputed when a village cuts a trail).
* Bank trades at 4:1, or 3:1 and 2:1 at docks. Players trade with each other: you can
  make offers to the bots, and bots make offers to you and to each other.
* The first player to reach 10 fame **on their own turn** wins. Hidden landmarks count.

Options (all off unless you ask, so an old seed still deals the same game):

* **5 or 6 players.** A large board of 30 hexes in rows of 3-4-5-6-5-4-3, with two
  badlands, 28 number tokens, 11 docks, 24 cards of each resource and a 34-card
  venture deck (20 rangers). After every turn comes the **special building phase**:
  each other player in turn may build or buy a venture with the cards in hand,
  without trading or playing cards. Players who can't afford anything are skipped.
* **Beginner board.** A fixed, balanced starting map for up to 4 players.
* **Balanced dice.** The 36 combinations are dealt from a shuffled deck, which is
  reshuffled when fewer than 13 are left. A long drought of one number can't happen, and
  the tail of a deck can't be counted.
* **Counter-offers.** A player who turns an offer down may name a price: one more card, or
  one fewer in return. Bots do this to you and to each other, and you can answer any offer
  with terms of your own (two tries against a bot's offer).

## The coach

* **Post-move review.** After each decision with real alternatives, the engine compares
  your move with its best line. The difference is measured in *fame-equivalents*:
  `✓ best` < 0.05 ≤ `✓ good` < 0.2 ≤ `?! inaccuracy` < 0.5 ≤ `? mistake` < 1.0 ≤ `?? blunder`.
  It covers setup placements, trails, the bandit and its victim, discards, rangers before
  rolling, builds, trades, and answers to offers. Opening placements are scored in pips
  and rescaled to fame-equivalents, so a reasonable alternative trail reads as `good`, not as
  a blunder. Rolling with a ranger in hand is judged against playing the ranger *after*
  the roll. If you can win this turn, the hint and the review show the winning line, and
  any move that gives the win away is a blunder.
* **End-of-game report.** You get an accuracy percentage, the total value lost, and your
  five biggest misses with the engine's preferred line.
* **Across games.** `--stats` (or **Your progress** in the browser) shows accuracy over
  time and which kinds of decision cost you the most.
* **Seeds.** The board, dice, venture deck and steals each use their own random
  stream, so the same `--seed` replays the same board and dice even if you play
  differently. Undo uses the same mechanism inside a game.

## How the bots think

All of this lives in `hexfield/ai.py`, and every evaluation weight is in one table,
`PARAMS`.

1. **Evaluation.** Each player's position is scored in fame-equivalents from:
   * real fame;
   * expected production per resource, aware of the bandit and with diminishing returns.
     Surplus counts for more when you own the matching dock, and missing resources
     are penalised;
   * room to expand: open spots within 2 trails, discounted when an opponent is closer;
   * hand quality: cards that complete a village, town or venture are worth
     more, and large hands are penalised for the risk of a 7;
   * ventures and the Great Patrol and Great Trail races.

   The weights shift from timber and clay early to grain and stone late, and production
   matters less as someone nears 10 fame.
2. **Decision score.** A bot's own value minus a weighted share of each opponent's. The
   weights rise for the leader and for anyone at 8+ fame, so the bots gang up on whoever is
   winning, including you.
3. **Turn search.** A beam search over the actions in a turn: builds, bank and dock
   trades, and ventures (ranger and bandit, hoard, windfall,
   pathfinder). It includes *macro moves* ("trade 3 wool→stone, trade 4 timber→grain, build
   town"), so builds that need several trades aren't pruned.
4. **Win-now search.** From 7 fame up, every bot first runs an exhaustive search for a line
   that wins *this turn*, capped at 4000 nodes. An optimistic bound cuts dead branches, so
   it usually takes well under a millisecond. It uses only what the player knows, and a
   ranger's steal is never counted on. The coach runs the same search for hints and
   reviews.
5. **Opening.** A snake-draft lookahead. For each candidate first village, the bot
   simulates every opponent's greedy pick up to its own second pick, then takes the best
   pair. It scores resource coverage, scarcity, number diversity, docks and room to
   expand.
6. **Bandit, discards, rangers and trades** all use the same evaluation. A bot accepts a
   trade only if it gains more than the proposer does, measured by simulating the
   proposer's best turn with and without the trade. It never feeds a player at 8+ fame.
7. **Styles.** Every bot has a style: *balanced*, *builder* (trails, villages, Great
   Trail), *citizen* (grain and stone, early towns), *general* (ventures, Great
   Patrol) or *trader* (docks and deals). A style is a handful of multipliers on the
   evaluation weights. By default each bot gets a random style (`--styles balanced` turns
   that off). In 96-game matches between hard bots, no style was distinguishable from
   *balanced*: the styles add variety, not a handicap.

**Hidden information.** Each player has a card tracker fed only by public events
(production, builds, trades, discards). When two other players steal from each other, the
tracker estimates the stolen card from the victim's likely hand. Every bot decision runs
on that tracked view, never on the real hands. The coach uses *your* tracker, so hints,
evaluations and reviews can't leak hidden cards. Nobody sees other players' ventures
or the deck order.

### Measuring and tuning

```
python3 tools/selfplay.py --levels hard,medium,easy --games 180
python3 tools/selfplay.py --levels hard,hard,hard,hard --styles balanced,builder,citizen,general --games 96
python3 tools/tune.py --iterations 50 --games 48 --out tuned.json
```

`selfplay.py` runs on every core, rotates the seats and prints each entry's win rate with
a 95% interval. Over 180 games, `hard` won 74% (67-80%) against one `medium` and one
`easy` bot, where a fair share is 33%.

`tune.py` searches the weights by self-play with SPSA, then plays the result against the
current weights. Adopt a tuned set only when the whole interval is above 50%.

Two attempts to make `hard` stronger were measured this way and **not** kept:

* Tuned weights (50 iterations of 48 games) scored 45.5% against the hand-set ones over 200
  games (95% interval 39-52%).
* Looking past the bot's own turn (re-ranking its best lines over sampled dice for the
  next round) scored 48.8% over 480 games, at twice the thinking time.

At these budgets, rollouts and chance-averaging tie a good evaluation, and weight tuning
stays inside the noise. What does pay is already here: a planner inside the turn, an exact
win-this-turn search and a lookahead opening. Telling a few percent apart takes on the
order of a thousand games per comparison.

## Development

```
hexfield/constants.py   rules data
hexfield/board.py       geometry (screen-coordinate vertices), board and dock generation, both sizes
hexfield/game.py        state, legal moves, transitions, great trail, rule options
hexfield/ai.py          evaluation weights and styles, beam search, setup lookahead, trading
hexfield/match.py       seating, trade resolution with counter-offers, headless games
hexfield/record.py      a game as data: seed + decisions, replayed exactly
hexfield/store.py       saved games on disk, statistics across games
hexfield/backup.py      a Hexfield home as one .tar.gz, run before every deploy
hexfield/render.py      ANSI board and panel
hexfield/cli.py         terminal game, coach, undo, resume, replay, statistics
hexfield/web/session.py live games (any mix of humans and bots), offers, reviews, replays
hexfield/web/server.py  HTTP API: several games at once, seat keys, long polling
hexfield/web/static/js  the browser client as ES modules, no build step:
                        app (game loop) · state · board · art (all the drawings) · panels ·
                        keys · coach · dialogs · effects · games (saves, replay, lobby,
                        pass and play) · text · util · logic (DOM-free, unit-tested)
tests/                  rules, options, records, hidden information, bots, scripted terminal
                        and HTTP games, multiplayer, and tests/js for the client's logic
tools/selfplay.py       parallel bot-vs-bot benchmark with seat rotation and intervals
tools/tune.py           SPSA weight tuning with validation
tools/browser.mjs       a small headless-Chrome driver (DevTools protocol, no packages)
tools/e2e.mjs           a real browser plays through the main screens
```

```
python3 -m unittest                                         # everything
node --test tests/js                                        # just the browser client's logic
HEXFIELD_BROWSER_TESTS=1 python3 -m unittest tests.test_js  # plus a real browser
```

The suite runs on Python 3.11 or newer (CI runs it on 3.11 and 3.15). With Node 26 on the path it also runs the client's unit tests
and checks that every module parses and every import resolves. Without Node those tests
are skipped. The real-browser run also needs Chrome or Chromium (set `CHROME_BIN` if it
isn't in a usual place) and is opt-in.

The game needs nothing beyond Python; the tools for working on it are pinned in
`pyproject.toml`'s `dev` group. Install them and turn on the pre-commit hook (lint and
format check of the staged files, then the tests) once:

```
python3 -m pip install --group dev
git config core.hooksPath .githooks
ruff check . && ruff format .
```

GitHub Actions runs all of it on arm64 runners, then builds the package and the Docker
image and starts the production compose stack from it. On `main` it then publishes the
image and deploys it (see [Deploying hex.vaari.dev](#deploying-hexvaaridev)). Every action
is pinned to a commit and the base image to a digest; [Renovate](renovate.json) moves them,
with everything short of a major in one weekly pull request that merges itself once CI
passes.

## Contributing

Issues and pull requests are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). To report a
security problem, see [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
