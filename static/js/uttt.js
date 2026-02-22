/**
 * uttt.js — Self-contained Ultimate Tic-Tac-Toe engine (client-side).
 * Used by the local 2-player mode and the match replay viewer.
 */
class UTTT {
    static WIN_LINES = [[0,1,2],[3,4,5],[6,7,8],[0,3,6],[1,4,7],[2,5,8],[0,4,8],[2,4,6]];

    constructor() {
        this.boards        = Array(9).fill(null).map(() => Array(9).fill(null));
        this.boardWinners  = Array(9).fill(null);
        this.boardWinLines = Array(9).fill(null);
        this.currentPlayer = 'X';
        this.forcedBoard   = null;
        this.gameWinner    = null;
        this.gameWinLine   = null;
        this.lastMove      = null;
        this.moveHistory   = [];
    }

    checkWin(board) {
        for (const [a, b, c] of UTTT.WIN_LINES) {
            if (board[a] && board[a] === board[b] && board[a] === board[c])
                return { winner: board[a], line: [a, b, c] };
        }
        if (board.every(Boolean)) return { winner: 'D', line: null };
        return { winner: null, line: null };
    }

    checkGameWinner() {
        const res = this.checkWin(this.boardWinners);
        if (res.winner && res.winner !== 'D') {
            this.gameWinLine = res.line;
            return res.winner;
        }
        if (this.boardWinners.every(Boolean)) {
            const x = this.boardWinners.filter(w => w === 'X').length;
            const o = this.boardWinners.filter(w => w === 'O').length;
            return x > o ? 'X' : o > x ? 'O' : 'D';
        }
        return null;
    }

    makeMove(b, c) {
        if (this.gameWinner) return false;
        if (this.boardWinners[b]) return false;
        if (this.forcedBoard !== null && b !== this.forcedBoard) return false;
        if (this.boards[b][c] !== null) return false;

        const player     = this.currentPlayer;
        this.boards[b][c] = player;
        this.lastMove    = [b, c];

        const res = this.checkWin(this.boards[b]);
        if (res.winner) {
            this.boardWinners[b]  = res.winner;
            if (res.winner !== 'D') this.boardWinLines[b] = res.line;
        }

        this.gameWinner    = this.checkGameWinner();
        this.forcedBoard   = this.boardWinners[c] === null ? c : null;
        this.currentPlayer = this.currentPlayer === 'X' ? 'O' : 'X';

        // Store snapshot
        this.moveHistory.push({
            board: b, cell: c, player,
            snapshot: this.getState(),
        });
        return true;
    }

    getState() {
        return {
            boards:        this.boards.map(r => [...r]),
            winners:       [...this.boardWinners],
            boardWinLines: [...this.boardWinLines],
            forced:        this.forcedBoard,
            lastMove:      this.lastMove ? [...this.lastMove] : null,
            player:        this.currentPlayer,
            gameWinner:    this.gameWinner,
            gameWinLine:   this.gameWinLine ? [...this.gameWinLine] : null,
            started:       true,
        };
    }

    /** Replay a list of {board, cell, player} moves and return snapshots. */
    static buildSnapshots(history) {
        const game      = new UTTT();
        const snapshots = [];
        for (const m of history) {
            game.makeMove(m.board, m.cell);
            snapshots.push({ ...m, snapshot: game.getState() });
        }
        return snapshots;
    }
}
