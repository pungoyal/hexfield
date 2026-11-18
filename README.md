# Hexfield

A complete base-game engine in pure Python (3.10+, no dependencies). You play against
2 or 3 machine opponents in a coloured terminal UI. A coach can suggest moves, review
every decision you make, and replay the same board and dice from a seed.

```
python3 -m hexfield                    # you + 3 hard bots, random seat
python3 -m hexfield -o 2               # you + 2 bots
python3 -m hexfield --seed 1234 --seat 1   # replay exact board + dice, you go first
python3 -m hexfield -l medium          # easier bots (easy | medium | hard)
python3 -m hexfield --fast             # no pause between bot actions
python3 -m hexfield --no-review --no-reasoning   # play "blind"
```

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
`T` trade, `E` end turn, `H` hint, `Ctrl+Z` (`⌘Z` on a Mac) undo, `?` help. Reloading the
page resumes the game.

Each hint line names where it builds ("2 roads toward 8 wheat · 3:1 port"); selecting a line
lights its spots on the board, and **Play** carries out bank trades together with the build
they pay for (a longer plan says **Play step 1**). Only inaccuracies and worse pop up as
toasts; every verdict is listed in the coach panel. Offers you make come back with each
bot's reason for saying no, and a bot's offer to you opens with the coach's verdict. The
Monopoly dialog shows your card tracker's estimate of what each resource would bring in.
On a phone or tablet the key badges and board letters are hidden.

Everything can be done from the keyboard. In setup, in a build mode and when moving the
robber, every clickable spot shows a letter (two letters when there are many), and typing it
picks the spot. Hexes are named with letters that never clash with a browser shortcut or a
terminal command that works at every prompt (no B, D, E, H, Q, R or T). In the terminal,
`c`, `k` and `s` are main-turn commands as well as hexes C, K and S; at the robber prompt
only hex letters and victim names mean anything, so there is no clash.

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
| `offer 2 wood for ore` | offer a trade to the bots |
| `e` | end turn |
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

Strength check (`python3 tools/selfplay.py --levels hard,medium,easy --games 30`):
`hard` wins about 70% against one `medium` and one `easy` bot, where a fair share is 33%.

## Code layout

```
hexfield/constants.py   rules data
hexfield/board.py       geometry (screen-coordinate vertices), board/port generation
hexfield/game.py        state, legal moves, transitions, longest road
hexfield/ai.py          evaluator, beam search, setup lookahead, trading
hexfield/match.py       trade resolution, headless games
hexfield/render.py      ANSI board + panel
hexfield/cli.py         interactive game, coach, undo, reports
hexfield/web/           browser UI: session.py (game + async reviews), server.py, static/
tests/               rules, hidden-info, AI, scripted CLI and HTTP end-to-end tests
tools/selfplay.py    bot-vs-bot benchmark with seat rotation
```

Run the tests with `python3 -m unittest discover -s tests -t .` (about 10 s).
