"""Simple heuristic AI for Ultimate Tic Tac Toe."""
import random
from .logic import WIN_LINES


def _find_winning_cell(board_state, player):
    """Return the cell index that wins board_state for player, or None."""
    for a, b, c in WIN_LINES:
        vals = [board_state[a], board_state[b], board_state[c]]
        if vals.count(player) == 2 and vals.count(None) == 1:
            return [a, b, c][vals.index(None)]
    return None


def _mini_board_wins_after(game, b, c, player):
    """Would playing (b,c) win mini-board b for player?"""
    board_copy = game.boards[b][:]
    board_copy[c] = player
    for a, bb, cc in WIN_LINES:
        if board_copy[a] == board_copy[bb] == board_copy[cc] == player:
            return True
    return False


def _meta_wins_after(game, b, player):
    """Would winning mini-board b give player the meta-game?"""
    bw = game.board_winners[:]
    bw[b] = player
    for a, bb, cc in WIN_LINES:
        if bw[a] == bw[bb] == bw[cc] == player:
            return True
    return False


def get_ai_move(game):
    """Return (board, cell) for the best AI move."""
    valid_moves = game.get_valid_moves()
    if not valid_moves:
        return None

    ai = game.current_player
    human = "O" if ai == "X" else "X"

    # 1. Win the meta-game immediately
    for b, c in valid_moves:
        if _mini_board_wins_after(game, b, c, ai) and _meta_wins_after(game, b, ai):
            return b, c

    # 2. Block human from winning the meta-game
    for b, c in valid_moves:
        if _mini_board_wins_after(game, b, c, human) and _meta_wins_after(game, b, human):
            return b, c

    # 3. Win any mini-board
    for b, c in valid_moves:
        if _mini_board_wins_after(game, b, c, ai):
            return b, c

    # 4. Block human from winning any mini-board
    for b, c in valid_moves:
        if _mini_board_wins_after(game, b, c, human):
            return b, c

    # 5. Play center of any playable board
    center_moves = [(b, c) for b, c in valid_moves if c == 4]
    if center_moves:
        return random.choice(center_moves)

    # 6. Play corners
    corner_moves = [(b, c) for b, c in valid_moves if c in (0, 2, 6, 8)]
    if corner_moves:
        return random.choice(corner_moves)

    return random.choice(valid_moves)
