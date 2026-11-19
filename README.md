# Hexfield

A complete base-game engine in pure Python (3.10+, no dependencies). You play against
2 to 5 machine opponents in a coloured terminal UI or in the browser, alone or with other people. A coach can suggest moves, review
every decision you make, and replay the same board and dice from a seed.

```
python3 -m hexfield                    # you + 3 hard bots, random seat
python3 -m hexfield -o 2               # you + 2 bots
python3 -m hexfield --seed 1234 --seat 1   # replay exact board + dice, you go first
python3 -m hexfield -l medium          # easier bots (easy | medium | hard)
python3 -m hexfield --fast             # no pause between bot actions
python3 -m hexfield --no-review --no-reasoning   # play "blind"
python3 -m hexfield -o 5               # six players: the large board and the special building phase
python3 -m hexfield --dice balanced --layout beginner   # dealt dice, the rulebook's starting map
python3 -m hexfield --resume           # pick up your last unfinished game
python3 -m hexfield --games            # your saved games; --replay ID steps through one
python3 -m hexfield --stats            # accuracy across your finished games, and where the points go
```

`pip install .` adds the commands `hexfield` and `hexfield-web`; running from a checkout
with `python3 -m` works just as well.

### Saved games, replays and your progress

Every game is saved as you play, in `~/.hexfield/games` (set `HEXFIELD_HOME` to move it).
A save is the seed, the table and the list of decisions, so it stays valid when the bots
change. Quit at any point and resume later, in the terminal or the browser, with your undo
history intact.

In the browser, **Games** (`Shift+G`) lists them. **Replay** steps through a game move by
move (`←` `→`, `[` `]` for your own decisions, `Space` to play). At each of your decisions
it shows what you played, the coach's verdict and the engine's line on the board, and can
list what else there was. **All cards** turns every hand face up. **Play on from here**
starts a new game from that position, on the same board with the same dice: the way to find
out what the other line would have done. Games can be exported to a file and imported.

**Your progress** adds up your finished solo games: accuracy game by game, win rate, and a
table of where the lost points go (opening, building and trading, robber, discards, trade
offers, knights), so you can see what to practise.

### Playing with other people

A new game can seat several humans, with bots in the other chairs (3 to 6 players in all).

* **Pass and play**: one screen. It goes dark between players and the next one confirms
  before their cards show.
* **Online**: one device each. Start the server with `python3 -m hexfield.web --lan`, create
  the game and send the others the invite link from the lobby. Each seat has its own secret
  key, kept in that browser; a player only ever receives their own hand. Only the machine
  running the server can start, resume, list or delete games.

Undo is for single-player games. Offers between humans work like offers to bots: the others
accept, decline or answer with a counter-offer, and the proposer picks.

### Hosting it for others

`--lan` is for people on your own network. To run the server somewhere you can't sit at (a
small VM, a container, a home server behind a reverse proxy), give it a host key:

```
export HEXFIELD_HOST_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
python3 -m hexfield.web --lan --no-browser --allow-host play.example.com
```

Open `https://play.example.com/?host=<the key>` once. That browser is now the host (a
cookie remembers it for 30 days) and can start, resume and delete games; everyone else only
gets into the game they hold an invite link for. With a host key set, requests from the
server's own machine are no longer treated as the host.

Or with Docker (games are kept in the `/data` volume):

```
docker build -t hexfield .
docker run -p 8765:8765 -v hexfield-data:/data -e HEXFIELD_HOST_KEY="$HEXFIELD_HOST_KEY" hexfield
```

The server speaks plain HTTP. On the open internet put a reverse proxy with TLS in front of
it (Caddy: `reverse_proxy 127.0.0.1:8765`; nginx: `proxy_pass` plus `proxy_set_header Host
$host` and `X-Forwarded-For`), because invite links carry seat keys. Let the long poll
live: a proxy read timeout of 60 s or more.

What the server does to protect itself and you:

* It only answers to an address, `localhost`, a `.local` name or a name given with
  `--allow-host`, and refuses cross-site posts, so a web page you visit can't drive it.
* A request passed on by a proxy never counts as "this machine".
* Strict browser headers (content security policy, no framing, no `Referer`).
* Each remote address gets a request allowance (HTTP 429 beyond it), at most 200 requests
  run at once, bodies are capped, and a stalled connection is dropped after 30 s.
* Errors are logged on the server (`--verbose` adds an access log without seat keys);
  remote callers only see "internal error".
* `GET /healthz` for health checks; SIGTERM stops it cleanly. Games are written after every
  decision, so a restart loses nothing.

It is built for a table of friends, not for strangers at scale: there are no accounts, one
process holds every live game, and the bots think on the server's CPU.

### Web interface

```
python3 -m hexfield.web                # opens http://127.0.0.1:8765 in your browser
python3 -m hexfield.web --port 9000 --no-browser
```

The browser UI uses the same engine, bots and coach (still no dependencies: a stdlib HTTP
server plus plain HTML/CSS/JS). It has an SVG board with clickable spots, animated dice,
production and robber, a card dock, trade dialogs for the bank, ports and players, and a
coach panel. In the coach panel, **Hint** shows the engine's top options on the board,
**Evaluate** scores every player, and each of your decisions gets a verdict. Hover a review
to compare your move with the engine's. Undo works as in the terminal, at any moment: while
the bots play, in the discard and bot-offer dialogs (an **Undo** button, or `U`), and after
the game ends. Bots play at a
selectable speed. Keys: `R` roll, `1/2/3` road/settlement/city, `D` dev card,
`T` trade, `E` end turn, `H` hint, `U` undo (the same key on every screen and in every dialog), `?` help. Reloading the
page resumes the game, and so does restarting the server.

On your move, whatever you can press is drawn larger: the Roll button, the builds you can
afford, a playable development card, the spots on the board. What you can't use steps
back. When the choices sit close together (the road beside a new settlement, a free road,
a city to upgrade) the board zooms in on them and zooms out again once you have chosen.
The board, pieces and cards are drawn in the browser as vector art (`js/art.js`): every
hex is its own small landscape, and there are no image files to load.

Every spot you can build on is also a real button: `Tab` to it and press `Enter`, and a
screen reader hears what it is ("Build a settlement: 8 wheat, 5 sheep, 9 pips"). **Marks**
(`Shift+C`) switches to a colour-blind-safe palette and puts each player's seat number on
their pieces. Animations are dropped when the system asks for reduced motion.

Each hint line names where it builds ("2 roads toward 8 wheat · 3:1 port"); selecting a line
lights its spots on the board, and **Play** carries out bank trades together with the build
they pay for (a longer plan says **Play step 1**). Only inaccuracies and worse pop up as
toasts; every verdict is listed in the coach panel. Offers you make come back with each
bot's reason for saying no, and a bot's offer to you opens with the coach's verdict. The
Monopoly dialog shows your card tracker's estimate of what each resource would bring in.
On a phone or tablet the key badges and board letters are hidden.

Everything can be done from the keyboard. In setup, in a build mode and when moving the
robber, every clickable spot shows a letter, and typing it picks the spot. Letters go by
rank, not position: the best spot is always **A** (shown in green), then S, F, G… along the
home row, and only the weakest spots get two letters. **Space** always does the obvious
move: the A spot, the roll, and in every dialog and popup the green-ringed recommended
answer (the coach's verdict on an offer, the suggested discard, the trade that unlocks your
best build, the richest player to rob); keep pressing to follow a multi-step one.
Settlements rank by pips, new resources and ports; cities by pips; roads by the best open
spot they lead to; the robber by the opponents' income it blocks (the leaders' counts
more), never your own.

In the terminal, hexes are named with fixed letters that never clash with a command that
works at every prompt (no B, D, E, H, Q, R or T). `c`, `k` and `s` are main-turn commands as
well as hexes C, K and S; at the robber prompt only hex letters and victim names mean
anything, so there is no clash.

The board fills a full-bleed sea panel and scales with the window. When a roll pays out, each
card travels from the hex that produced it to whoever collected it. After a game ends you can
dismiss the result to look at the board; **Results** in the status pill brings it back.

The left panel shows **your income**: pips per resource, the numbers you collect on, what
the robber blocks, and your trade rate. Settlement spots are labelled with their pip value
(the best ones stand out), and when your turn starts a **While you waited** card summarises
your card changes and what each bot did. The status line says what you can build right now,
or how many bank trades away the nearest build is. Every dialog that changes your hand
(discard, offers, trades, Year of Plenty) says which builds it unlocks or gives up. When you
move the robber, each hex shows whose income it would block and by how many pips, and hexes
that would block you are outlined in red. A player within two points of winning is flagged.

### Terminal

The board plus side panel needs a terminal about **132 columns × 40 rows**. In a narrower
window the panel moves below the board. Before each move a status line says what you can
build now, or how many bank/port trades away the nearest build is. Placing a settlement
lists the open spots with the most pips. Moving the robber first shows a table of hexes:
whom each blocks (pips), whom you can rob, and which ones would block you. A bot's offer
shows your hand before and after and what the trade unlocks or gives up (`y/N`; Enter
declines). Hints show the command to type next to each option, e.g. `→ t sheep brick`. The game uses 256-colour ANSI and works in
iTerm2, Terminal.app, kitty, Alacritty and VS Code. `--no-color` (or `NO_COLOR=1`) turns
colour off.

## Rules implemented

* A random board with standard tile, number and port distributions. Red numbers (6/8)
  never touch, and neither do identical numbers. There are 9 ports (four 3:1, five 2:1).
* Snake-draft setup. Your second settlement pays its starting resources.
* Distance rule. Roads must connect to your network, and an opponent's building cuts it.
* Production follows the bank-shortage rule: if the bank can't pay everyone, nobody gets
  that resource (unless only one player is owed it).
* On a 7, everyone over 7 cards discards half. Then the robber moves and you steal a card.
* Development cards: 14 knights, 5 VP, 2 road building, 2 monopoly, 2 year of plenty.
  You can't play a card on the turn you buy it, and you can play at most one per turn
  (knights are allowed before you roll).
* Hidden hands: you see only how many cards each opponent holds (details below).
* Largest Army (3+ knights) and Longest Road (5+, recomputed when a settlement cuts a road).
* Bank trades at 4:1, 3:1 or 2:1 by port. Players trade with each other: you can offer
  to the bots, and bots make offers to you and to each other.
* First to 10 VP **on their own turn** wins. Hidden VP cards count.

Options (all off unless you ask, so an old seed still deals the same game):

* **5 or 6 players.** The large board: 30 hexes in rows of 3-4-5-6-5-4-3, two deserts,
  28 number tokens, 11 harbours, 24 cards of each resource and a 34-card development deck
  (20 knights). After every turn comes the **special building phase**: each other player
  in turn may build or buy a development card with the cards in hand, without trading or
  playing cards. Players who can't afford anything are skipped. (Newer printings replace
  this phase with "paired players"; the classic rule is the one implemented.)
* **Beginner board.** The fixed starting map from the rulebook, for up to 4 players.
* **Balanced dice.** The 36 combinations are dealt from a shuffled deck, reshuffled when
  fewer than 13 are left, so a long drought of one number can't happen and the tail of a
  deck can't be counted. It is a plain deck: the extra anti-streak weighting some online
  versions add is not included.
* **Counter-offers.** A player who turns an offer down may name a price: one more card, or
  one fewer in return. Bots do this to you and to each other, and you can answer any offer
  with terms of your own (two tries against a bot's offer).

## Commands

| command | |
|---|---|
| `r` | roll (rolled for you unless you can play a knight first; `--no-autoroll` to always ask) |
| `s 23` / `s` | settlement at vertex 23 (bare `s` shows the legal vertex ids on the map) |
| `c 23` | city |
| `rd 41` / `rd` | road on edge 41 (bare `rd` shows the legal edge ids) |
| `d` | buy a development card |
| `k`, `rb`, `mono ore`, `yop wheat ore` | play development cards |
| `t wood ore` / `t wood ore 3` | bank/port trade at your best ratio; the number is how many trades (all or none) |
| `offer 2 wood for ore` | offer a trade to the bots; one that says no may name its price |
| `c wood for ore` | at a bot's offer: your counter-offer (you give wood, you get ore) |
| `e` | end turn (or pass, in the special building phase) |
| `h` / `hint` | the engine's top 5 options for the current decision, with reasons |
| `eval` | engine evaluation of every player plus production (pips per resource) |
| `review on/off`, `reason on/off` | post-move review / bot reasoning (bare `review` shows the setting) |
| `undo` | take back your last decision, at any prompt (also while discarding or answering a bot's offer). Dice and the deck stay the same, so you can try another line |
| `b`, `ids`, `log 30`, `stats`, `cost`, `help`, `q` | view and help commands |

Hexes are lettered for the robber, e.g. `F` or `F vera`. At the robber prompt a letter is
always a hex (C, K and S mean city, knight and settlement only at the main prompt). At the
discard prompt card names come first: `b w w` discards brick, wood, wood (`board` redraws).

## Coaching

* **Post-move review.** After each decision with real alternatives, the engine compares
  your move with its best line. The difference is in *VP-equivalents*:
  `✓ best` < 0.05 ≤ `✓ good` < 0.2 ≤ `?! inaccuracy` < 0.5 ≤ `? mistake` < 1.0 ≤ `?? blunder`.
  It covers setup placements, roads, robber placement and victim, discards, knights
  before rolling, builds, trades, and accepting or declining offers. Opening placements
  are scored in pip units internally and rescaled to VP-equivalents (fitted to how much
  the evaluation changes after the placement), so a reasonable alternative road reads as
  `good`, not as a blunder. Rolling with a knight in hand is judged against playing the
  knight *after* the roll: it only costs the production the robber blocks on this roll
  plus a small edge. If you can win this turn, the hint and the review show the winning
  line, and any move that gives the win away is a blunder (a forced win counts as
  3 VP-eq over the position).
* **End-of-game report.** You get an accuracy %, the total value lost, and your five
  biggest misses with the engine's preferred line.
* **Across games.** `--stats` (or **Your progress** in the browser) shows accuracy over
  time and which kinds of decision cost you the most.
* **Seeds.** Board, dice, dev deck and steals each use their own random stream. The
  same `--seed` replays the same board and dice sequence even if you play differently.
  `undo` uses the same mechanism inside a game.

## How the bots think (`hexfield/ai.py`)

1. **Evaluation.** Each player's position is scored in VP-equivalents from:
   * real VP;
   * expected production per resource, robber-aware, with diminishing returns. Surplus
     counts for more when you own the matching port, and missing resources are penalised;
   * expansion potential: open spots reachable within 2 roads, discounted when an
     opponent is closer;
   * hand quality: cards that complete a settlement, city or dev card are worth more,
     and large hands are penalised for 7-roll exposure;
   * development cards and the Largest Army / Longest Road races.

   The weights shift from wood/brick early to wheat/ore late. Horizon decay makes
   production matter less as someone nears 10 VP.
2. **Decision score.** My value minus a weighted share of each opponent's value. Weights
   rise for the leader and for anyone at 8+ VP, so the bots gang up on whoever is
   winning — including you.
3. **Turn search.** A beam search over action sequences within the turn: builds, bank/port
   trades, dev-card plays (knight + robber, monopoly, year of plenty, road building). It
   also includes *macro moves* ("trade 3 sheep→ore, trade 4 wood→wheat, build city") so
   multi-trade builds aren't pruned.
   **Win-now search.** From 7 VP up (counting its own hidden VP cards), every bot level
   first runs an exhaustive breadth-first search for a line that wins *this turn*: builds,
   bank/port trades and dev plays (no dev purchases), capped at 4000 nodes. An optimistic
   bound (cards and wildcards needed for the cheapest mix of settlements, cities, Longest
   Road and Largest Army) cuts dead branches, so it usually costs well under a
   millisecond. It uses only what the player knows: Monopoly counts whole cards of the
   tracked opponent hands, and a knight's steal is never counted on. The beam search
   alone used to miss lines like "Year of Plenty, two roads, take Longest Road". The
   coach runs the same search for hints and reviews.
4. **Opening.** Snake-draft lookahead. For each candidate first settlement, it simulates
   every opponent's greedy pick up to its own second pick, then takes the best pair. It
   scores resource coverage, scarcity, number diversity, port synergy and room to expand.
   Setup roads point at the best contested spot.
5. **Robber, discards, knights, trades** all use the same evaluation. The robber
   averages over the victim's possible cards. When a bot plays a knight before rolling it
   picks the robber move at the same time, so the reason it gives is the move it makes. A bot accepts a trade only if it gains
   more than the trade helps the proposer, measured by simulating the proposer's best
   turn with and without the trade. It never feeds a player at 8+ VP.

**Hidden information, as in the real game.** You see only your own cards. For opponents
you see how many cards and dev cards they hold, plus the public log (production, builds,
trades, discards). The bots play under the same rules. Each player has a card tracker
fed only by public events. When two other players steal from each other, the tracker
estimates the stolen card from the victim's likely hand. Every bot decision runs on
that tracked view, never the real hands. The coach also uses *your* tracker, so `hint`,
`eval` and reviews can't leak hidden cards. Nobody sees other players' development cards
or the deck order. A dev card bought inside a simulation is valued at the deck's
expected value.

6. **Styles.** Every bot has a style: *balanced*, *builder* (roads, settlements, Longest
   Road), *citizen* (wheat and ore, early cities), *general* (development cards, Largest
   Army) or *trader* (harbours and deals). A style is a handful of multipliers on the
   evaluation weights, so all of them play the same sound game with different tastes. By
   default each bot gets one at random (`--styles balanced` turns that off). In 96-game
   matches between hard bots no style was distinguishable from *balanced*: variety, not a
   handicap.

All the evaluation weights live in one table, `PARAMS` in `hexfield/ai.py`.

### Measuring and tuning

```
python3 tools/selfplay.py --levels hard,medium,easy --games 180
python3 tools/selfplay.py --levels hard,hard,hard,hard --styles balanced,builder,citizen,general --games 96
python3 tools/tune.py --iterations 50 --games 48 --out tuned.json
```

`selfplay.py` runs on every core (about 3 hard games a second on a 12-core laptop), rotates
the seats and prints each entry's win rate with a 95% interval. Over 180 games `hard` won
74% (67-80%) against one `medium` and one `easy` bot, where a fair share is 33%.

`tune.py` searches the weights by self-play with SPSA, the method chess engines use for
this, and then plays the result against the current weights. Adopt a tuned set only when
the whole interval is above 50%.

Two attempts to make `hard` stronger were measured this way and **not** kept:

* Tuned weights (50 iterations of 48 games) scored 45.5% against the hand-set ones over 200
  games (95% interval 39-52%): no better.
* A look past the bot's own turn (re-ranking its best lines over sampled dice for the next
  round) scored 48.8% over 480 games: no better, at twice the thinking time.

Both agree with what others have found for this game: at small budgets, rollouts and
chance-averaging lose to or tie a good evaluation, and weight tuning stays inside the
noise. What does pay is in place here already: a planner inside the turn, an exact
win-this-turn search and a look-ahead opening. Telling a few percent apart needs on the
order of a thousand games per comparison.

## Code layout

```
hexfield/constants.py   rules data
hexfield/board.py       geometry (screen-coordinate vertices), board/port generation, both sizes
hexfield/game.py        state, legal moves, transitions, longest road, rule options
hexfield/ai.py          evaluation weights and styles, beam search, setup lookahead, trading
hexfield/match.py       seating, trade resolution with counter-offers, headless games
hexfield/record.py      a game as data: seed + decisions; exact replay
hexfield/store.py       saved games on disk, statistics across games
hexfield/render.py      ANSI board + panel
hexfield/cli.py         terminal game, coach, undo, resume, replay, statistics
hexfield/web/session.py live games (any mix of humans and bots), offers, reviews, replays
hexfield/web/server.py  HTTP API: several games at once, seat keys, long polling
hexfield/web/static/js  the browser client as ES modules, no build step:
                        app (game loop) · state · board · art (all the drawings) · panels ·
                        keys · coach · dialogs ·
                        effects · games (saves, replay, lobby, pass and play) · text · util ·
                        logic (DOM-free, unit-tested)
tests/                  rules, options, records, hidden information, bots, scripted terminal
                        and HTTP games, multiplayer, and tests/js for the client's logic
tools/selfplay.py       parallel bot-vs-bot benchmark with seat rotation and intervals
tools/tune.py           SPSA weight tuning with validation
tools/browser.mjs       a small headless-Chrome driver (DevTools protocol, no packages)
tools/e2e.mjs           a real browser plays through the main screens
```

## Tests

```
python3 -m unittest                                   # everything (about 45 s)
node --test tests/js                                  # just the browser client's logic
HEXFIELD_BROWSER_TESTS=1 python3 -m unittest tests.test_js   # plus the real-browser run
```

The suite needs only Python. With Node 20+ on the path it also runs the client's unit
tests and checks that every module parses and every import resolves; without Node those
are skipped. The real-browser run needs Node 22+ and Chrome and is opt-in. GitHub Actions
(`.github/workflows/tests.yml`) runs all of it on Python 3.10, 3.12, 3.13 and 3.14, then
builds the package and the Docker image and checks the container starts and stops cleanly.
