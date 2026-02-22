"""AI for Ultimate Tic Tac Toe — easy / medium / hard difficulties.

Difficulty summary
──────────────────
Easy   : 100% random. No strategy whatsoever.
Medium : 50% chance to play randomly, 50% chance to play the best
         greedy move (win/block, prefer center/corners). Mistakes happen often.
Hard   : Iterative-deepening Minimax + Alpha-Beta pruning with a hand-tuned
         heuristic encoding UTTT-specific strategy (see below).

Hard strategic principles encoded in the heuristic
──────────────────────────────────────────────────
• Sending the opponent to a WON/FULL board (free choice) is the worst move.
• Sending the opponent to the CENTER board (4) is very bad.
• Sending the opponent to a CORNER board (0,2,6,8) is mildly bad.
• Sending the opponent to an EDGE board (1,3,5,7 = the "diamond") is fine.
• Priority: win CENTER mini-board first, then CORNERS, then build 3-in-a-row.
• A meta 2-in-a-row threat outweighs having lots of individual grid wins.
• Within each mini-board: centre cell (4) > corners > edges.
"""
import random
import math
import time
from .logic import WIN_LINES


# ── Taunts ────────────────────────────────────────────────────────────────────
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


# ── Board geometry constants ──────────────────────────────────────────────────
_CENTER_BOARD  = 4
_CORNER_BOARDS = frozenset({0, 2, 6, 8})
_EDGE_BOARDS   = frozenset({1, 3, 5, 7})   # the "diamond" boards

# How STRATEGICALLY VALUABLE each meta-board is for the AI to control.
# Center >> corners >> edges.
_META_VALUE = [6, 3, 6, 3, 10, 3, 6, 3, 6]

# Cell weight within a mini-board: centre > corners > edges.
_CELL_VALUE = [3, 2, 3, 2, 4, 2, 3, 2, 3]

# PENALTY applied to AI score for sending the opponent to board index i.
# Large positive = bad for AI (opponent gets good position).
# Negative = actually good for AI (forces opponent somewhere weak).
#   Free choice (won board)  → -300 in score  ← worst case
#   Center board (4)         → -200
#   Corner boards (0,2,6,8)  → -60
#   Edge boards (1,3,5,7)    → +20  ← forcing diamond = fine
_SEND_PENALTY = [60, -20, 60, -20, 200, -20, 60, -20, 60]
_FREE_MOVE_PENALTY = 300   # sending to a won/full board (free choice) is worst


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
        if board_winners[a] and board_winners[a] != 'D' \
                and board_winners[a] == board_winners[b] == board_winners[c]:
            return board_winners[a]
    if all(board_winners):
        x = board_winners.count('X')
        o = board_winners.count('O')
        return 'X' if x > o else ('O' if o > x else 'D')
    return None

class _SimState:
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
        self.winner  = _check_meta_winner(self.winners)
        self.forced  = c if not self.winners[c] else None
        self.player  = 'O' if p == 'X' else 'X'


# ── Heuristic evaluation ──────────────────────────────────────────────────────
def _mini_score(board, ai, opp):
    """Score one mini-board: 2-in-a-row threats + positional cells."""
    score = 0
    for a, b, c in WIN_LINES:
        line = [board[a], board[b], board[c]]
        ai_n  = line.count(ai)
        op_n  = line.count(opp)
        if ai_n > 0 and op_n == 0:
            score += 10 * (10 ** (ai_n - 1))    # 10, 100
        elif op_n > 0 and ai_n == 0:
            score -= 12 * (10 ** (op_n - 1))    # -12, -120  (defend slightly more)
    for i in range(9):
        if board[i] == ai:
            score += _CELL_VALUE[i]
        elif board[i] == opp:
            score -= _CELL_VALUE[i]
    return score

def _evaluate(state, ai):
    """Full strategic heuristic. Positive = good for AI."""
    opp = 'O' if ai == 'X' else 'X'

    # Terminal states
    if state.winner == ai:  return  200000
    if state.winner == opp: return -200000
    if state.winner == 'D': return 0

    score = 0

    # ── Meta-board 3-in-a-row threats ─────────────────────────────────────────
    # This is the top priority: 2-in-a-row on the meta board beats everything.
    for a, b, c in WIN_LINES:
        wl = [state.winners[a], state.winners[b], state.winners[c]]
        ai_n  = wl.count(ai)
        op_n  = wl.count(opp)
        # Value lines through center higher (center appears in 4 lines)
        line_importance = 1 + (1 if _CENTER_BOARD in (a, b, c) else 0)
        if ai_n > 0 and op_n == 0:
            score += line_importance * (500 if ai_n == 1 else 4000)
        elif op_n > 0 and ai_n == 0:
            score -= line_importance * (600 if op_n == 1 else 5000)  # block more urgently

    # ── Mini-board control (weighted by meta-board importance) ────────────────
    for i in range(9):
        mv = _META_VALUE[i]
        if state.winners[i] == ai:
            score += mv * 80         # won board, weight by position
        elif state.winners[i] == opp:
            score -= mv * 95         # opponent's won board hurts more
        elif not state.winners[i]:
            # Unresolved board: score internal position, scaled by meta importance
            score += _mini_score(state.boards[i], ai, opp) * (mv / 6.0)

    # ── Destination penalty: where does THIS move send the opponent? ──────────
    # (Evaluated from the perspective of the LAST move made, i.e. current forced board)
    if state.forced is None:
        # Opponent gets free choice — big penalty
        score -= _FREE_MOVE_PENALTY
    else:
        dest = state.forced
        if state.winners[dest]:
            # Sent to a won board → free choice anyway → worst
            score -= _FREE_MOVE_PENALTY
        else:
            # _SEND_PENALTY[dest]: positive = bad for AI (opponent gets good spot)
            score -= _SEND_PENALTY[dest]

    return score


# ── Alpha-Beta Minimax ────────────────────────────────────────────────────────
_KILLER_MOVES = {}

def _move_score_for_ordering(state, b, c, ai):
    """Quick priority score for move ordering (higher = try first)."""
    opp     = 'O' if ai == 'X' else 'X'
    current = state.player
    score   = 0

    # 1. Immediate meta win — try first, skip rest
    s2 = state.clone(); s2.push(b, c)
    if s2.winner == current:
        return 1_000_000

    # 2. Block immediate opponent meta win
    s3 = state.clone(); s3.player = opp; s3.push(b, c)
    if s3.winner == opp:
        score += 80_000

    # 3. Mini-board win in a high-value board
    bc = state.boards[b][:]
    bc[c] = current
    if _check_line_winner(bc) not in (None, 'D'):
        score += 3000 * _META_VALUE[b]    # winning center board >> corner >> edge

    # 4. Block opponent mini-board win in a high-value board
    bc2 = state.boards[b][:]
    bc2[c] = opp
    if _check_line_winner(bc2) not in (None, 'D'):
        score += 2000 * _META_VALUE[b]

    # 5. Destination quality: sending opponent to edge (diamond) = good
    if s2.winner is None:   # game not over
        dest = c  # where opponent will be sent
        if state.winners[dest]:
            score -= 5000   # free move for opponent = terrible
        else:
            score -= _SEND_PENALTY[dest] * 20  # positive penalty = subtract = bad

    # 6. Playing in the center mini-board or corner mini-board
    score += _META_VALUE[b] * 30

    # 7. Cell position within mini-board (centre > corners > edges)
    score += _CELL_VALUE[c] * 10

    # 8. Killer move bonus
    if (b, c) in _KILLER_MOVES.get(0, set()):
        score += 500

    return score

def _order_moves(state, moves, ai, depth):
    return sorted(moves,
                  key=lambda m: _move_score_for_ordering(state, m[0], m[1], ai),
                  reverse=True)

def _alphabeta(state, depth, alpha, beta, ai, deadline):
    if state.winner or depth == 0 or time.time() >= deadline:
        return _evaluate(state, ai), None

    moves = state.valid_moves()
    if not moves:
        return _evaluate(state, ai), None

    ordered   = _order_moves(state, moves, ai, depth)
    best_move = ordered[0]
    maximizing = (state.player == ai)

    if maximizing:
        best_val = -math.inf
        for b, c in ordered:
            child = state.clone()
            child.push(b, c)
            val, _ = _alphabeta(child, depth - 1, alpha, beta, ai, deadline)
            if val > best_val:
                best_val, best_move = val, (b, c)
            alpha = max(alpha, best_val)
            if beta <= alpha:
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
                best_val, best_move = val, (b, c)
            beta = min(beta, best_val)
            if beta <= alpha:
                _KILLER_MOVES.setdefault(depth, set()).add((b, c))
                break
        return best_val, best_move


def _hard_ai(game, valid, time_limit=2.5):
    """Iterative-deepening alpha-beta. Returns (board, cell)."""
    ai       = game.current_player
    state    = _SimState(game)
    deadline = time.time() + time_limit
    best_move = valid[0]

    # Instant win
    for b, c in valid:
        s2 = state.clone(); s2.push(b, c)
        if s2.winner == ai:
            return b, c

    # Instant block (opponent would win next move)
    opp = 'O' if ai == 'X' else 'X'
    for b, c in valid:
        s2 = state.clone(); s2.player = opp; s2.push(b, c)
        if s2.winner == opp:
            best_move = (b, c)   # we must block this
            break

    # Iterative deepening
    for depth in range(1, 15):
        if time.time() >= deadline:
            break
        try:
            val, move = _alphabeta(state, depth, -math.inf, math.inf, ai, deadline)
            if move:
                best_move = move
            if val >= 200000:
                break   # forced win found
        except Exception:
            break

    return best_move


# ── Greedy helper (used by medium) ───────────────────────────────────────────
def _greedy_move(game, valid):
    """Best single-ply greedy move: win > block > centre > corner > edge."""
    ai  = game.current_player
    opp = 'O' if ai == 'X' else 'X'

    def mini_wins(b, c, player):
        bc = game.boards[b][:]
        bc[c] = player
        return any(bc[a] == bc[bb] == bc[cc] == player for a, bb, cc in WIN_LINES)

    def meta_wins(b, player):
        bw = game.board_winners[:]
        bw[b] = player
        return any(bw[a] == bw[bb] == bw[cc] == player for a, bb, cc in WIN_LINES)

    # Win the game
    for b, c in valid:
        if mini_wins(b, c, ai) and meta_wins(b, ai):
            return b, c
    # Block opponent game win
    for b, c in valid:
        if mini_wins(b, c, opp) and meta_wins(b, opp):
            return b, c
    # Win a mini-board (prefer centre > corners > edges)
    for brd in [_CENTER_BOARD] + list(_CORNER_BOARDS) + list(_EDGE_BOARDS):
        for b, c in valid:
            if b == brd and mini_wins(b, c, ai):
                return b, c
    # Block opponent mini-board win (prefer high-value boards)
    for brd in [_CENTER_BOARD] + list(_CORNER_BOARDS) + list(_EDGE_BOARDS):
        for b, c in valid:
            if b == brd and mini_wins(b, c, opp):
                return b, c
    # Positional: prefer centre cell in high-value boards
    for brd in [_CENTER_BOARD] + list(_CORNER_BOARDS):
        centre = [(b, c) for b, c in valid if b == brd and c == 4]
        if centre:
            return random.choice(centre)
    corners = [(b, c) for b, c in valid if c in _CORNER_BOARDS]
    if corners:
        return random.choice(corners)
    return random.choice(valid)


# ── Public API ────────────────────────────────────────────────────────────────
def get_ai_move(game, difficulty='medium'):
    valid = game.get_valid_moves()
    if not valid:
        return None
    if difficulty == 'easy':
        return _easy(valid)
    if difficulty == 'hard':
        return _hard_ai(game, valid, time_limit=2.5)
    return _medium(game, valid)


def _easy(valid):
    """100% random — no strategy at all."""
    return random.choice(valid)


def _medium(game, valid):
    """50% random, 50% greedy best move."""
    if random.random() < 0.5:
        return random.choice(valid)
    return _greedy_move(game, valid)
