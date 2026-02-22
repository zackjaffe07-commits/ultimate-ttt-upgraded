"""AI for Ultimate Tic Tac Toe — easy / medium / hard difficulties."""
import random
from .logic import WIN_LINES


# ── Taunts ──────────────────────────────────────────────────────────────────
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
    """Return a taunt string or None."""
    if random.random() < TAUNT_CHANCE.get(difficulty, 0.3):
        return random.choice(AI_TAUNTS.get(difficulty, AI_TAUNTS['medium']))
    return None


# ── Helpers ──────────────────────────────────────────────────────────────────
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


def _count_two_in_row(board_state, player):
    """Count lines where player has 2 and the third is empty."""
    count = 0
    for a, b, c in WIN_LINES:
        vals = [board_state[a], board_state[b], board_state[c]]
        if vals.count(player) == 2 and vals.count(None) == 1:
            count += 1
    return count


def _board_score(game, b, player):
    """Heuristic score of mini-board b for player (for hard difficulty)."""
    if game.board_winners[b] == player:
        return 10
    if game.board_winners[b] and game.board_winners[b] != player:
        return -10
    score = 0
    opp = 'O' if player == 'X' else 'X'
    score += _count_two_in_row(game.boards[b], player) * 2
    score -= _count_two_in_row(game.boards[b], opp) * 2
    if game.boards[b][4] == player:
        score += 1
    return score


def _destination_board_score(game, dest_board, ai):
    """How good is it for opponent to land on dest_board?  Lower = better for ai."""
    opp = 'O' if ai == 'X' else 'X'
    if game.board_winners[dest_board] is not None:
        return 0          # board already won / drawn — opponent gets free choice → neutral
    # If opponent would have a winning move from that board, bad for us
    opp_wins = sum(1 for c in range(9)
                   if game.boards[dest_board][c] is None and _mini_wins_after(game, dest_board, c, opp))
    return -opp_wins


# ── Public API ────────────────────────────────────────────────────────────────
def get_ai_move(game, difficulty='medium'):
    valid = game.get_valid_moves()
    if not valid:
        return None
    if difficulty == 'easy':
        return _easy(game, valid)
    if difficulty == 'hard':
        return _hard(game, valid)
    return _medium(game, valid)


def _easy(game, valid):
    """Easy: mostly random, occasionally makes an obvious win/block."""
    ai  = game.current_player
    opp = 'O' if ai == 'X' else 'X'
    if random.random() < 0.35:
        # Try to win meta
        for b, c in valid:
            if _mini_wins_after(game, b, c, ai) and _meta_wins_after(game, b, ai):
                return b, c
    if random.random() < 0.25:
        # Block obvious meta loss
        for b, c in valid:
            if _mini_wins_after(game, b, c, opp) and _meta_wins_after(game, b, opp):
                return b, c
    return random.choice(valid)


def _medium(game, valid):
    """Medium: structured heuristics (original logic)."""
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


def _hard(game, valid):
    """Hard: enhanced heuristics with board scoring and destination awareness."""
    ai  = game.current_player
    opp = 'O' if ai == 'X' else 'X'

    # 1. Win meta immediately
    for b, c in valid:
        if _mini_wins_after(game, b, c, ai) and _meta_wins_after(game, b, ai):
            return b, c

    # 2. Block human meta win
    for b, c in valid:
        if _mini_wins_after(game, b, c, opp) and _meta_wins_after(game, b, opp):
            return b, c

    # 3. Win a mini-board that's strategically valuable
    winning_moves = [(b, c) for b, c in valid if _mini_wins_after(game, b, c, ai)]
    if winning_moves:
        # Prefer the win that sends opponent to a worse board
        scored = []
        for b, c in winning_moves:
            dest_score = _destination_board_score(game, c, ai)
            scored.append((dest_score, b, c))
        scored.sort(reverse=True)
        return scored[0][1], scored[0][2]

    # 4. Block human mini-board win (prioritise boards that would help them win meta)
    block_moves = [(b, c) for b, c in valid if _mini_wins_after(game, b, c, opp)]
    if block_moves:
        scored = []
        for b, c in block_moves:
            dest_score = _destination_board_score(game, c, ai)
            scored.append((dest_score, b, c))
        scored.sort(reverse=True)
        return scored[0][1], scored[0][2]

    # 5. Score all moves by: mini-board score + destination penalty
    def move_score(b, c):
        board_val = _board_score(game, b, ai)
        dest_val  = _destination_board_score(game, c, ai)
        center_bonus = 1 if c == 4 else 0
        meta_center  = 2 if b == 4 else 0
        return board_val + dest_val + center_bonus + meta_center

    best = max(valid, key=lambda mv: move_score(mv[0], mv[1]))
    # Add a tiny random tie-break so hard isn't deterministic
    top_score = move_score(best[0], best[1])
    top_moves = [(b, c) for b, c in valid if move_score(b, c) >= top_score - 1]
    return random.choice(top_moves)
