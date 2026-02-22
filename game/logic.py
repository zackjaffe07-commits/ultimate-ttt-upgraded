WIN_LINES = [
    (0,1,2),(3,4,5),(6,7,8),
    (0,3,6),(1,4,7),(2,5,8),
    (0,4,8),(2,4,6)
]

class UltimateTicTacToe:
    def __init__(self):
        self.boards = [[None]*9 for _ in range(9)]
        self.board_winners = [None]*9
        self.board_win_lines = [None]*9   # which 3 cells formed each mini-board win
        self.current_player = "X"
        self.forced_board = None
        self.game_winner = None
        self.game_win_line = None          # which 3 mini-boards formed the meta-win
        self.started = False
        self.last_move = None              # [board, cell]
        self.move_history = []             # [{board, cell, player}, ...]

    def check_win(self, board):
        for a, b, c in WIN_LINES:
            if board[a] and board[a] == board[b] == board[c]:
                return board[a], [a, b, c]
        if all(board):
            return "D", None
        return None, None

    def check_game_winner(self):
        winner, win_line = self.check_win(self.board_winners)
        if winner and winner != "D":
            self.game_win_line = win_line
            return winner
        if all(self.board_winners):
            x_wins = self.board_winners.count("X")
            o_wins = self.board_winners.count("O")
            if x_wins > o_wins: return "X"
            elif o_wins > x_wins: return "O"
            else: return "D"
        return None

    def make_move(self, b, c):
        if not self.started or self.game_winner: return False
        if self.board_winners[b]: return False
        if self.forced_board is not None and b != self.forced_board: return False
        if self.boards[b][c] is not None: return False
        player = self.current_player
        self.boards[b][c] = player
        self.last_move = [b, c]
        self.move_history.append({"board": b, "cell": c, "player": player})
        if not self.board_winners[b]:
            winner, win_line = self.check_win(self.boards[b])
            if winner:
                self.board_winners[b] = winner
                if winner != "D": self.board_win_lines[b] = win_line
        self.game_winner = self.check_game_winner()
        self.forced_board = c if self.board_winners[c] is None else None
        self.current_player = "O" if self.current_player == "X" else "X"
        return True

    def get_valid_moves(self):
        moves = []
        boards_to_check = range(9) if self.forced_board is None else [self.forced_board]
        for b in boards_to_check:
            if self.board_winners[b]: continue
            for c in range(9):
                if self.boards[b][c] is None: moves.append((b, c))
        return moves

    def resign(self, loser):
        self.game_winner = "O" if loser == "X" else "X"

    def state(self):
        return {
            "boards": self.boards,
            "winners": self.board_winners,
            "boardWinLines": self.board_win_lines,
            "player": self.current_player,
            "forced": self.forced_board,
            "gameWinner": self.game_winner,
            "gameWinLine": self.game_win_line,
            "started": self.started,
            "lastMove": self.last_move,
            "moveHistory": self.move_history,
        }
