"""AI for Ultimate Tic Tac Toe — easy / medium / hard difficulties.

Hard uses iterative-deepening Minimax with Alpha-Beta pruning + a
board-position heuristic tailored for Ultimate TTT.
A lightweight internal GameState avoids deepcopy of the real game object
(which carries large move-history snapshots).
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
        "i've already evaluated every branch. you lose.",
        "you played well. just not well enough. 😤",
        "the AI always wins. eventually.",
    ],
}

TAUNT_CHANCE = {'easy': 0.5, 'medium': 0.30, 'hard': 0.35}


def maybe_taunt(difficulty):
    if random.random() < TAUNT_CHANCE.get(difficulty, 0.3):
        return random.choice(AI_TAUNTS.get(difficulty, AI_TAUNTS['medium']))
    return None


# ── Lightweight game state ────────────────────────────────────────────────────
def _check_line_winner(board):
    for a, b, c in WIN_LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    if all(board):
        return 'D'
    return None


def _check_meta_winner(board_winners):
    for a, b, c in WIN_LINES:
        if board_winners[a] and board_winners[a] != 'D' and board_winners[a] == board_winners[b] == board_winners[c]:
            return board_winners[a]
    if all(board_winners):
        x = board_winners.count('X')
        o = board_winners.count('O')
        return 'X' if x > o else ('O' if o > x else 'D')
    return None


class _SimState:
    """Fast-clone game state used exclusively inside search."""
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
        p = self.player
        self.boards[b][c] = p
        if not self.winners[b]:
            w = _check_line_winner(self.boards[b])
            if w:
                self.winners[b] = w
        self.winner = _check_meta_winner(self.winners)
        self.forced = c if not self.winners[c] else None
        self.player = 'O' if p == 'X' else 'X'


# ── Heuristic evaluation ──────────────────────────────────────────────────────
# Position weights: center > corners > edges
_POS_WEIGHT = [3, 2, 3, 2, 4, 2, 3, 2, 3]

def _mini_board_score(board, ai, opp):
    """Score a single mini-board from AI's perspective."""
    score = 0
    for a, b, c in WIN_LINES:
        line = [board[a], board[b], board[c]]
        ai_cnt  = line.count(ai)
        opp_cnt = line.count(opp)
        if ai_cnt > 0 and opp_cnt == 0:
            score += (10 ** ai_cnt)
        elif opp_cnt > 0 and ai_cnt == 0:
            score -= (10 ** opp_cnt)
    # Positional bonus
    for i in range(9):
        if board[i] == ai:
            score += _POS_WEIGHT[i]
        elif board[i] == opp:
            score -= _POS_WEIGHT[i]
    return score


def _evaluate(state, ai):
    """Full board heuristic. Positive = good for AI."""
    opp = 'O' if ai == 'X' else 'X'
    if state.winner == ai:  return 100000
    if state.winner == opp: return -100000
    if state.winner == 'D': return 0

    score = 0
    # Meta-board: treat won mini-boards like cells
    for a, b, c in WIN_LINES:
        line = [state.winners[a], state.winners[b], state.winners[c]]
        ai_cnt  = line.count(ai)
        opp_cnt = line.count(opp)
        if ai_cnt > 0 and opp_cnt == 0:
            score += (50 ** ai_cnt)
        elif opp_cnt > 0 and ai_cnt == 0:
            score -= (50 ** opp_cnt)

    # Positional meta bonus
    for i in range(9):
        if state.winners[i] == ai:
            score += _POS_WEIGHT[i] * 20
        elif state.winners[i] == opp:
            score -= _POS_WEIGHT[i] * 20
        elif not state.winners[i]:
            score += _mini_board_score(state.boards[i], ai, opp)

    # Bonus for sending opponent to a won/full board (free move for us)
    if state.forced is None:
        score += 15  # opponent can go anywhere = slightly bad for us
    elif state.winners[state.forced]:
        score += 30  # we sent opponent somewhere that gives them free choice
    # Penalty for being sent to center/corners of meta (high value boards)
    else:
        score -= _POS_WEIGHT[state.forced] * 5

    return score


# ── Alpha-Beta Minimax ────────────────────────────────────────────────────────
_KILLER_MOVES = {}  # simple killer move heuristic storage

def _order_moves(state, moves, ai, depth):
    """Order moves to improve alpha-beta pruning."""
    opp = 'O' if ai == 'X' else 'X'
    current = state.player

    def move_priority(m):
        b, c = m
        priority = 0
        # Immediate meta win
        s2 = state.clone(); s2.push(b, c)
        if s2.winner == current:
            return 10000
        # Immediate mini-board win
        bc = state.boards[b][:]
        bc[c] = current
        if _check_line_winner(bc) not in (None, 'D'):
            priority += 500
        # Block opponent mini-board win
        bc2 = state.boards[b][:]
        bc2[c] = ('O' if current == 'X' else 'X')
        if _check_line_winner(bc2) not in (None, 'D'):
            priority += 300
        # Send to a won/full board (free choice) is slightly good
        if state.winners[c]:
            priority += 50
        # Positional bonus
        priority += _POS_WEIGHT[b] * 3 + _POS_WEIGHT[c] * 2
        # Killer move bonus
        if m in _KILLER_MOVES.get(depth, set()):
            priority += 200
        return priority

    return sorted(moves, key=move_priority, reverse=True)


def _alphabeta(state, depth, alpha, beta, ai, deadline):
    if state.winner or depth == 0 or time.time() >= deadline:
        return _evaluate(state, ai), None

    moves = state.valid_moves()
    if not moves:
        return _evaluate(state, ai), None

    ordered = _order_moves(state, moves, ai, depth)
    best_move = ordered[0]
    is_maximizing = (state.player == ai)

    if is_maximizing:
        best_val = -math.inf
        for b, c in ordered:
            child = state.clone()
            child.push(b, c)
            val, _ = _alphabeta(child, depth - 1, alpha, beta, ai, deadline)
            if val > best_val:
                best_val = val
                best_move = (b, c)
            alpha = max(alpha, best_val)
            if beta <= alpha:
                # Store killer move
                _KILLER_MOVES.setdefault(depth, set()).add((b, c))
                break
        return best_val, best_move
    else:
        best_val = math.inf
        for b, c in ordered:
            child = state.clone()
            child.push(b, c)
            val, _ = _alphabeta(child, depth - 1, alpha, beta, ai, deadline)
            if val < best_val:
                best_val = val
                best_move = (b, c)
            beta = min(beta, best_val)
            if beta <= alpha:
                _KILLER_MOVES.setdefault(depth, set()).add((b, c))
                break
        return best_val, best_move


def _hard_ai(game, valid, time_limit=2.0):
    """Iterative-deepening alpha-beta search."""
    ai = game.current_player
    state = _SimState(game)
    deadline = time.time() + time_limit
    best_move = valid[0]

    # Immediate win check
    for b, c in valid:
        s2 = state.clone(); s2.push(b, c)
        if s2.winner == ai:
            return b, c

    # Iterative deepening
    for depth in range(1, 12):
        if time.time() >= deadline:
            break
        try:
            val, move = _alphabeta(state, depth, -math.inf, math.inf, ai, deadline)
            if move:
                best_move = move
            if val >= 100000:  # found forced win
                break
        except Exception:
            break

    return best_move


# ── Simple helpers for easy/medium ───────────────────────────────────────────
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
        return _hard_ai(game, valid, time_limit=2.0)
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
