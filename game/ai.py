"""AI for Ultimate Tic Tac Toe — easy / medium / hard difficulties.

HARD AI STRATEGY (key principles encoded)
──────────────────────────────────────────
1. NEVER send opponent to a free-choice board unless it wins the game.
   Free choice (won/full board) = opponent plays anywhere = catastrophic.

2. Meta-board priority:  CENTER (4) >> CORNERS (0,2,6,8) >> EDGES (1,3,5,7)
   Win center first. Then corners. Diamond edges are least important.

3. The "destination" of a move (cell index = next forced board) matters as
   much as the move itself. Cell 4 → opponent goes to center (terrible).
   Cells 1,3,5,7 → opponent goes to edge (acceptable). Cells 0,2,6,8 →
   corners (bad). Always weigh destination cost.

4. Two-in-a-row on the META board is the winning pattern. Building 2-of-3
   on high-value meta lines is the primary mid-game goal.

5. Hybrid engine: Alpha-Beta (70% budget) + MCTS refinement (30% budget).
   Time adapts to timer mode so AI never blunders on clock.
"""
import random, math, time
from .logic import WIN_LINES


# ── Taunts ────────────────────────────────────────────────────────────────────
AI_TAUNTS = {
    'easy':   ["beep boop 🤖","i think i did good?","i'm still learning...",
                "oops, was that right?","my circuits are confused 😵",
                "i just picked randomly lol","is this how you play?"],
    'medium': ["calculated.","nice try 😏","i see your plan.",
                "that won't work.","interesting move... i'm not worried.",
                "getting closer. not close enough.","chess? never heard of it."],
    'hard':   ["your defeat was inevitable.","i decided 4 moves ago.",
                "resistance is futile.","is that the best you've got?",
                "i've already evaluated every branch. you lose.",
                "you played well. just not well enough. 😤",
                "the AI always wins. eventually."],
}
TAUNT_CHANCE = {'easy': 0.5, 'medium': 0.30, 'hard': 0.35}

def maybe_taunt(difficulty):
    if random.random() < TAUNT_CHANCE.get(difficulty, 0.3):
        return random.choice(AI_TAUNTS.get(difficulty, AI_TAUNTS['medium']))
    return None


# ── Board geometry ────────────────────────────────────────────────────────────
_CENTER_BOARD  = 4
_CORNER_BOARDS = frozenset({0, 2, 6, 8})
_EDGE_BOARDS   = frozenset({1, 3, 5, 7})

# How valuable is it to OWN each meta-board position?
# Center >> corners >> edges
_META_VALUE = [8, 3, 8, 3, 15, 3, 8, 3, 8]

# Cell weight within a mini-board: centre > corners > edges
_CELL_VALUE = [3, 2, 3, 2, 4, 2, 3, 2, 3]

# DESTINATION COST: how bad is it to send the opponent to board i?
# Higher = worse for us (opponent gets a great position or free choice).
# Free choice is handled separately with _FREE_CHOICE_COST.
#   Center board (4)         → 500  (opponent picks their ideal square there)
#   Corner boards (0,2,6,8)  → 120
#   Edge boards   (1,3,5,7)  → 20   (diamond = fine)
_DEST_COST     = [120, 20, 120, 20, 500, 20, 120, 20, 120]
_FREE_CHOICE_COST = 800   # sending to a won/full board is almost always losing


# ── Sim state ─────────────────────────────────────────────────────────────────
def _check_line_winner(board):
    for a, b, c in WIN_LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return 'D' if all(board) else None

def _check_meta_winner(winners):
    for a, b, c in WIN_LINES:
        if winners[a] and winners[a] != 'D' and winners[a] == winners[b] == winners[c]:
            return winners[a]
    if all(winners):
        x, o = winners.count('X'), winners.count('O')
        return 'X' if x > o else ('O' if o > x else 'D')
    return None

class _SimState:
    __slots__ = ('boards', 'winners', 'player', 'forced', 'winner')

    def __init__(self, game):
        self.boards  = [list(r) for r in game.boards]
        self.winners = list(game.board_winners)
        self.player  = game.current_player
        self.forced  = game.forced_board
        self.winner  = game.game_winner

    def clone(self):
        s = _SimState.__new__(_SimState)
        s.boards  = [list(r) for r in self.boards]
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
            if w: self.winners[b] = w
        self.winner = _check_meta_winner(self.winners)
        self.forced = c if not self.winners[c] else None
        self.player = 'O' if p == 'X' else 'X'


# ── Heuristic evaluation ──────────────────────────────────────────────────────
def _mini_threats(board, ai, opp):
    """Score one mini-board for threats and positional strength."""
    score = 0
    for a, b, c in WIN_LINES:
        line = [board[a], board[b], board[c]]
        an, op = line.count(ai), line.count(opp)
        if an > 0 and op == 0:
            score += 10 * (10 ** (an - 1))      # 10, 100
        elif op > 0 and an == 0:
            score -= 12 * (10 ** (op - 1))       # -12, -120
    for i in range(9):
        if board[i] == ai:   score += _CELL_VALUE[i]
        elif board[i] == opp: score -= _CELL_VALUE[i]
    return score

def _evaluate(state, ai):
    """Full strategic heuristic. Positive = good for AI."""
    opp = 'O' if ai == 'X' else 'X'

    if state.winner == ai:  return  500_000
    if state.winner == opp: return -500_000
    if state.winner == 'D': return 0

    score = 0

    # ── Meta-board 2-in-a-row / 3-in-a-row threats ───────────────────────────
    for a, b, c in WIN_LINES:
        wl = [state.winners[a], state.winners[b], state.winners[c]]
        an, op = wl.count(ai), wl.count(opp)
        # Weight lines that pass through center higher (4 lines through center)
        center_bonus = 1.5 if _CENTER_BOARD in (a, b, c) else 1.0
        if an == 2 and op == 0:
            score += int(8000 * center_bonus)   # one move from meta-win
        elif an == 1 and op == 0:
            score += int(600  * center_bonus)   # open 1-of-3
        elif op == 2 and an == 0:
            score -= int(10000 * center_bonus)  # must block — slightly higher priority
        elif op == 1 and an == 0:
            score -= int(700  * center_bonus)

    # ── Won board value by position ───────────────────────────────────────────
    for i in range(9):
        mv = _META_VALUE[i]
        if state.winners[i] == ai:
            score += mv * 100
        elif state.winners[i] == opp:
            score -= mv * 120
        elif not state.winners[i]:
            score += int(_mini_threats(state.boards[i], ai, opp) * (mv / 8.0))

    # ── Destination penalty ───────────────────────────────────────────────────
    # This is a huge factor: where do we send the opponent after this state?
    if state.forced is None:
        # Opponent gets free choice (we sent them to a won board)
        score -= _FREE_CHOICE_COST
    else:
        dest = state.forced
        if state.winners[dest]:
            # The destination is already won → free choice anyway
            score -= _FREE_CHOICE_COST
        else:
            score -= _DEST_COST[dest]

    return score


# ── Move ordering ─────────────────────────────────────────────────────────────
def _move_priority(state, b, c, ai):
    """Fast priority for move ordering — higher = try first."""
    opp = 'O' if ai == 'X' else 'X'
    cur = state.player
    score = 0

    # 1. Immediate meta win — always play it
    s2 = state.clone(); s2.push(b, c)
    if s2.winner == cur: return 2_000_000

    # 2. Must block opponent meta win
    s3 = state.clone(); s3.player = opp; s3.push(b, c)
    if s3.winner == opp: score += 200_000

    # 3. Wins a mini-board (weight by board value)
    bc = state.boards[b][:]; bc[c] = cur
    won_mini = _check_line_winner(bc) not in (None, 'D')
    if won_mini:
        score += 4000 * _META_VALUE[b]

    # 4. Blocks opponent mini-board win
    bc2 = state.boards[b][:]; bc2[c] = opp
    if _check_line_winner(bc2) not in (None, 'D'):
        score += 2500 * _META_VALUE[b]

    # 5. Destination quality after the move — THIS IS CRITICAL
    if s2.winner is None:
        dest = c  # cell index = next forced board
        if s2.winners[dest]:
            score -= 60_000   # gives opponent free choice → terrible
        else:
            score -= _DEST_COST[dest] * 40  # scale up for ordering

    # 6. Positional value of the board we're playing in
    score += _META_VALUE[b] * 40

    # 7. Cell position in mini-board
    score += _CELL_VALUE[c] * 15

    return score


# ── Alpha-Beta ────────────────────────────────────────────────────────────────
_KILLER = {}

def _alphabeta(state, depth, alpha, beta, ai, deadline):
    if state.winner or depth == 0 or time.time() >= deadline:
        return _evaluate(state, ai), None
    moves = state.valid_moves()
    if not moves: return _evaluate(state, ai), None

    ordered   = sorted(moves, key=lambda m: _move_priority(state, m[0], m[1], ai), reverse=True)
    best_move = ordered[0]
    maximizing = (state.player == ai)

    if maximizing:
        best_val = -math.inf
        for b, c in ordered:
            child = state.clone(); child.push(b, c)
            val, _ = _alphabeta(child, depth-1, alpha, beta, ai, deadline)
            if val > best_val: best_val, best_move = val, (b, c)
            alpha = max(alpha, best_val)
            if beta <= alpha:
                _KILLER.setdefault(depth, set()).add((b, c)); break
        return best_val, best_move
    else:
        best_val = math.inf
        for b, c in ordered:
            child = state.clone(); child.push(b, c)
            val, _ = _alphabeta(child, depth-1, alpha, beta, ai, deadline)
            if val < best_val: best_val, best_move = val, (b, c)
            beta = min(beta, best_val)
            if beta <= alpha:
                _KILLER.setdefault(depth, set()).add((b, c)); break
        return best_val, best_move


# ── MCTS ─────────────────────────────────────────────────────────────────────
class _MCTSNode:
    __slots__ = ('state','move','parent','children','wins','visits','untried')
    def __init__(self, state, move=None, parent=None):
        self.state=state; self.move=move; self.parent=parent
        self.children=[]; self.wins=0.0; self.visits=0
        self.untried=state.valid_moves()

    def ucb1(self, c=1.41):
        if self.visits==0: return math.inf
        return self.wins/self.visits + c*math.sqrt(math.log(self.parent.visits)/self.visits)

    def expand(self):
        move=self.untried.pop(random.randrange(len(self.untried)))
        child=_MCTSNode(self.state.clone(), move, self)
        child.state.push(*move); self.children.append(child); return child

    def rollout(self, ai):
        s=self.state.clone()
        opp='O' if ai=='X' else 'X'
        for _ in range(80):
            if s.winner: break
            moves=s.valid_moves()
            if not moves: break
            # Biased rollout: prefer immediate wins/blocks and avoid free-choice moves
            picked=None
            sample=random.sample(moves, min(8, len(moves)))
            # Instant win
            for b,c in sample:
                tmp=s.clone(); tmp.push(b,c)
                if tmp.winner==s.player: picked=(b,c); break
            # Block
            if not picked:
                for b,c in sample:
                    tmp=s.clone(); tmp.player=opp; tmp.push(b,c)
                    if tmp.winner==opp: picked=(b,c); break
            # Avoid free-choice moves strongly
            if not picked:
                non_free=[(b,c) for b,c in moves if not s.winners[c]]
                if non_free: picked=random.choice(non_free)
            if not picked: picked=random.choice(moves)
            s.push(*picked)
        w=s.winner
        if w==ai:   return 1.0
        if w=='D':  return 0.4
        if w is None: return 0.3+0.1*((_evaluate(s,ai)+500000)/1000000)
        return 0.0

    def backprop(self, r):
        self.visits+=1; self.wins+=r
        if self.parent: self.parent.backprop(1.0-r)


def _mcts(state, ai, time_limit):
    if time_limit < 0.12: return None
    root=_MCTSNode(state); deadline=time.time()+time_limit
    while time.time()<deadline:
        node=root
        while not node.untried and node.children:
            node=max(node.children, key=lambda n: n.ucb1())
        if node.untried and not node.state.winner:
            node=node.expand()
        node.backprop(node.rollout(ai))
    if not root.children: return None
    return max(root.children, key=lambda n: n.visits).move


# ── Hard AI ───────────────────────────────────────────────────────────────────
def _hard_ai(game, valid, time_limit=2.5):
    ai=game.current_player; state=_SimState(game)
    t0=time.time(); deadline=t0+time_limit
    opp='O' if ai=='X' else 'X'

    # Instant win
    for b,c in valid:
        s2=state.clone(); s2.push(b,c)
        if s2.winner==ai: return b,c

    # Forced block
    block=None
    for b,c in valid:
        s2=state.clone(); s2.player=opp; s2.push(b,c)
        if s2.winner==opp: block=(b,c); break
    best_move=block if block else valid[0]

    # Phase 1: Alpha-Beta — 70% of budget
    ab_dl=t0+time_limit*0.70
    for depth in range(1,18):
        if time.time()>=ab_dl: break
        try:
            val,move=_alphabeta(state,depth,-math.inf,math.inf,ai,ab_dl)
            if move: best_move=move
            if val>=500000: return best_move  # forced win
        except Exception: break

    # Phase 2: MCTS — remaining budget
    rem=deadline-time.time()
    if rem>=0.12:
        mcts_move=_mcts(state, ai, rem)
        if mcts_move:
            ab_pri   = _move_priority(state, *best_move,  ai)
            mcts_pri = _move_priority(state, *mcts_move, ai)
            if mcts_pri > ab_pri * 1.05:
                best_move=mcts_move

    return best_move


# ── Greedy (medium) ───────────────────────────────────────────────────────────
def _greedy_move(game, valid):
    ai=game.current_player; opp='O' if ai=='X' else 'X'
    def mini_wins(b,c,p):
        bc=game.boards[b][:]; bc[c]=p
        return any(bc[a]==bc[bb]==bc[cc]==p for a,bb,cc in WIN_LINES)
    def meta_wins(b,p):
        bw=game.board_winners[:]; bw[b]=p
        return any(bw[a]==bw[bb]==bw[cc]==p for a,bb,cc in WIN_LINES)
    for b,c in valid:
        if mini_wins(b,c,ai) and meta_wins(b,ai): return b,c
    for b,c in valid:
        if mini_wins(b,c,opp) and meta_wins(b,opp): return b,c
    for brd in [_CENTER_BOARD]+list(_CORNER_BOARDS)+list(_EDGE_BOARDS):
        for b,c in valid:
            if b==brd and mini_wins(b,c,ai): return b,c
    for brd in [_CENTER_BOARD]+list(_CORNER_BOARDS)+list(_EDGE_BOARDS):
        for b,c in valid:
            if b==brd and mini_wins(b,c,opp): return b,c
    for brd in [_CENTER_BOARD]+list(_CORNER_BOARDS):
        centre=[(b,c) for b,c in valid if b==brd and c==4]
        if centre: return random.choice(centre)
    corners=[(b,c) for b,c in valid if c in _CORNER_BOARDS]
    return random.choice(corners) if corners else random.choice(valid)


# ── Time budget ───────────────────────────────────────────────────────────────
def calc_ai_time_budget(game_data):
    diff=game_data.get('ai_difficulty','medium')
    if diff!='hard': return None
    timer_type=game_data.get('timer_type','move')
    if timer_type=='none': return 3.0
    if timer_type=='move':
        mt=game_data.get('move_timeout',30) or 0
        if mt<=0: return 3.0
        return max(0.5, min(mt*0.40, 12.0))
    if timer_type=='game':
        ai_sym=game_data['game'].current_player
        remaining=game_data.get(f'game_time_{ai_sym.lower()}',
                                game_data.get('game_time_each',300))
        if remaining<=0:   return 0.0
        if remaining<=3:   return 0.05
        if remaining<=10:  return 0.15
        if remaining<=20:  return 0.4
        if remaining<=60:  return min(remaining*0.06, 3.0)
        if remaining<=300: return min(remaining*0.04, 8.0)
        return min(remaining*0.025, 12.0)
    return 3.0


# ── Public API ────────────────────────────────────────────────────────────────
def get_ai_move(game, difficulty='medium', time_limit=None):
    valid=game.get_valid_moves()
    if not valid: return None
    if difficulty=='easy':  return random.choice(valid)
    if difficulty=='medium':
        return random.choice(valid) if random.random()<0.5 else _greedy_move(game,valid)
    tl=time_limit if time_limit is not None else 2.5
    return _hard_ai(game, valid, time_limit=max(0.05,tl))
