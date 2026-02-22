"""AI for Ultimate Tic Tac Toe — easy / medium / hard difficulties.

Hard uses Monte Carlo Tree Search (MCTS) with UCB1 selection.
A lightweight internal GameState is used for simulations so deepcopy
of the real game object (which carries large move history snapshots) is avoided.
"""
import random
import math
import time
from .logic import WIN_LINES


# ── Taunts ───────────────────────────────────────────────────────────────────
AI_TAUNTS = {
    'easy': [
        "beep boop 🤖", "i think i did good?", "i'm still learning...",
        "oops, was that right?", "my circuits are confused 😵",
        "i just picked randomly lol", "is this how you play?",
    ],
    'medium': [
        "calculated.", "nice try 😏", "i see your plan.",
        "that won't work.", "interesting move... i'm not worried.",
        "getting closer. not close enough.", "chess? never heard of it.",
    ],
    'hard': [
        "your defeat was inevitable.", "i decided 4 moves ago.",
        "resistance is futile.", "is that the best you've got?",
        "i've already simulated every outcome. you lose.",
        "you played well. just not well enough. 😤",
        "the AI always wins. eventually.",
    ],
}

TAUNT_CHANCE = {'easy': 0.5, 'medium': 0.30, 'hard': 0.35}


def maybe_taunt(difficulty):
    if random.random() < TAUNT_CHANCE.get(difficulty, 0.3):
        return random.choice(AI_TAUNTS.get(difficulty, AI_TAUNTS['medium']))
    return None


# ── Lightweight game state for MCTS simulations ──────────────────────────────
# We avoid deepcopy-ing the real UltimateTicTacToe object (which stores large
# move-history snapshots) by using this stripped-down clone instead.

def _check_line_winner(board):
    """Return winner symbol or None for a 9-cell list."""
    for a, b, c in WIN_LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    if all(board):
        return 'D'
    return None


def _check_meta_winner(board_winners):
    """Return meta-game winner from the 9 mini-board results."""
    for a, b, c in WIN_LINES:
        if board_winners[a] and board_winners[a] != 'D' and board_winners[a] == board_winners[b] == board_winners[c]:
            return board_winners[a]
    if all(board_winners):
        x = board_winners.count('X')
        o = board_winners.count('O')
        return 'X' if x > o else ('O' if o > x else 'D')
    return None


class _SimState:
    """Fast-clone game state used exclusively inside MCTS rollouts."""
    __slots__ = ('boards', 'winners', 'player', 'forced', 'winner')

    def __init__(self, game):
        self.boards  = [list(row) for row in game.boards]
        self.winners = list(game.board_winners)
        self.player  = game.current_player
        self.forced  = game.forced_board
        self.winner  = game.game_winner

    def clone(self):
        s         = _SimState.__new__(_SimState)
        s.boards  = [list(row) for row in self.boards]
        s.winners = list(self.winners)
        s.player  = self.player
        s.forced  = self.forced
        s.winner  = self.winner
        return s

    def valid_moves(self):
        boards = range(9) if self.forced is None else [self.forced]
        return [(b, c) for b in boards if not self.winners[b]
                for c in range(9) if self.boards[b][c] is None]

    def push(self, b, c):
        p              = self.player
        self.boards[b][c] = p
        if not self.winners[b]:
            w = _check_line_winner(self.boards[b])
            if w:
                self.winners[b] = w
        self.winner  = _check_meta_winner(self.winners)
        self.forced  = c if not self.winners[c] else None
        self.player  = 'O' if p == 'X' else 'X'


# ── MCTS ──────────────────────────────────────────────────────────────────────
class _Node:
    __slots__ = ('move', 'parent', 'children', 'wins', 'visits', 'untried')

    def __init__(self, move=None, parent=None, untried=None):
        self.move     = move
        self.parent   = parent
        self.children = []
        self.wins     = 0.0
        self.visits   = 0
        self.untried  = untried or []

    def ucb1(self, c=1.41):
        if self.visits == 0:
            return float('inf')
        return self.wins / self.visits + c * math.sqrt(math.log(self.parent.visits) / self.visits)

    def best_child(self, c=1.41):
        return max(self.children, key=lambda n: n.ucb1(c))


def _rollout(state: _SimState) -> str:
    """Play out randomly from state, biased toward winning/blocking moves."""
    s = state.clone()
    while not s.winner:
        moves = s.valid_moves()
        if not moves:
            break
        # Light heuristic bias during rollout: prefer immediate mini-wins
        ai  = s.player
        opp = 'O' if ai == 'X' else 'X'
        win_moves   = [(b, c) for b, c in moves if _would_win_mini(s, b, c, ai)]
        block_moves = [(b, c) for b, c in moves if _would_win_mini(s, b, c, opp)]
        if win_moves:
            b, c = random.choice(win_moves)
        elif block_moves:
            b, c = random.choice(block_moves)
        else:
            b, c = random.choice(moves)
        s.push(b, c)
    return s.winner or 'D'


def _would_win_mini(s: _SimState, b: int, c: int, player: str) -> bool:
    bd = s.boards[b]
    for a, bb, cc in WIN_LINES:
        line = [bd[a], bd[bb], bd[cc]]
        if line.count(player) == 2 and line.count(None) == 1:
            empties = [a, bb, cc]
            if empties[[bd[x] for x in [a, bb, cc]].index(None)] == c:
                return True
    return False


def _mcts(game, valid, time_limit: float = 1.5, max_sims: int = 800) -> tuple:
    """Run MCTS and return the best (board, cell) move."""
    ai   = game.current_player
    root = _Node(untried=list(valid))
    base = _SimState(game)
    deadline = time.time() + time_limit

    for _ in range(max_sims):
        if time.time() >= deadline:
            break

        node  = root
        state = base.clone()

        # Selection — walk tree while fully expanded
        while not node.untried and node.children:
            node = node.best_child()
            state.push(*node.move)
            if state.winner:
                break

        # Expansion
        if node.untried and not state.winner:
            move = node.untried.pop(random.randrange(len(node.untried)))
            state.push(*move)
            child = _Node(move=move, parent=node,
                          untried=state.valid_moves() if not state.winner else [])
            node.children.append(child)
            node = child

        # Simulation
        result = _rollout(state) if not state.winner else state.winner

        # Backpropagation
        n = node
        while n is not None:
            n.visits += 1
            if result == ai:
                n.wins += 1.0
            elif result == 'D':
                n.wins += 0.4   # draws are slightly bad for us — we want to win
            n = n.parent

    if not root.children:
        return random.choice(valid)
    best = max(root.children, key=lambda n: n.visits)
    return best.move


# ── Simple helpers for easy/medium ───────────────────────────────────────────
def _find_winning_cell(board_state, player):
    for a, b, c in WIN_LINES:
        vals = [board_state[a], board_state[b], board_state[c]]
        if vals.count(player) == 2 and vals.count(None) == 1:
            return [a, b, c][vals.index(None)]
    return None


def _mini_wins_after(game, b, c, player):
    bc = game.boards[b][:]
    bc[c] = player
    for a, bb, cc in WIN_LINES:
        if bc[a] == bc[bb] == bc[cc] == player:
            return True
    return False


def _meta_wins_after(game, b, player):
    bw = game.board_winners[:]
    bw[b] = player
    for a, bb, cc in WIN_LINES:
        if bw[a] == bw[bb] == bw[cc] == player:
            return True
    return False


# ── Public API ────────────────────────────────────────────────────────────────
def get_ai_move(game, difficulty='medium'):
    valid = game.get_valid_moves()
    if not valid:
        return None
    if difficulty == 'easy':
        return _easy(game, valid)
    if difficulty == 'hard':
        return _mcts(game, valid, time_limit=1.5, max_sims=800)
    return _medium(game, valid)


def _easy(game, valid):
    ai  = game.current_player
    opp = 'O' if ai == 'X' else 'X'
    if random.random() < 0.35:
        for b, c in valid:
            if _mini_wins_after(game, b, c, ai) and _meta_wins_after(game, b, ai):
                return b, c
    if random.random() < 0.25:
        for b, c in valid:
            if _mini_wins_after(game, b, c, opp) and _meta_wins_after(game, b, opp):
                return b, c
    return random.choice(valid)


def _medium(game, valid):
    ai  = game.current_player
    opp = 'O' if ai == 'X' else 'X'
    for b, c in valid:
        if _mini_wins_after(game, b, c, ai) and _meta_wins_after(game, b, ai):
            return b, c
    for b, c in valid:
        if _mini_wins_after(game, b, c, opp) and _meta_wins_after(game, b, opp):
            return b, c
    for b, c in valid:
        if _mini_wins_after(game, b, c, ai):
            return b, c
    for b, c in valid:
        if _mini_wins_after(game, b, c, opp):
            return b, c
    centers = [(b, c) for b, c in valid if c == 4]
    if centers:
        return random.choice(centers)
    corners = [(b, c) for b, c in valid if c in (0, 2, 6, 8)]
    if corners:
        return random.choice(corners)
    return random.choice(valid)
