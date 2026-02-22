from gevent import monkey
monkey.patch_all()

from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_socketio import SocketIO, join_room, leave_room, emit
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import or_
from game.logic import UltimateTicTacToe
from game.ai import get_ai_move, maybe_taunt
import random, string, os, time, math, json
from functools import wraps

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'a_secret_key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///db.sqlite3')
db = SQLAlchemy(app)
migrate = Migrate(app, db)
socketio = SocketIO(app, async_mode='gevent')
login_manager = LoginManager(app)
login_manager.login_view = 'landing'

# NOTE: Flask-Login's @login_required silently kills SocketIO events because
# it tries to issue an HTTP redirect inside a WebSocket context.
# Authentication is already enforced at the HTTP route level (@login_required on /home),
# so logged-out users can never reach the page that creates the socket in the first place.
# We use a no-op decorator here to keep the decorator syntax without breaking anything.
def socket_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        return f(*args, **kwargs)
    return decorated


MOVE_TIMEOUT = 30   # default seconds per move
ELO_K        = 32
DEFAULT_ELO  = 1000

games        = {}
guest_games  = {}
active_players = set()

# ── Models ───────────────────────────────────────────────────────────────────
class User(UserMixin, db.Model):
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    elo           = db.Column(db.Integer, default=DEFAULT_ELO, nullable=False)
    win_streak    = db.Column(db.Integer, default=0, nullable=False)
    best_streak   = db.Column(db.Integer, default=0, nullable=False)

    def set_password(self, p): self.password_hash = generate_password_hash(p)
    def check_password(self, p): return check_password_hash(self.password_hash, p)

class Match(db.Model):
    id                = db.Column(db.Integer, primary_key=True)
    player1_id        = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    player2_id        = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    winner_id         = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    is_draw           = db.Column(db.Boolean, default=False, nullable=False)
    is_ranked         = db.Column(db.Boolean, default=False, nullable=False)
    game_id           = db.Column(db.String(8), nullable=True)
    move_history_json = db.Column(db.Text, nullable=True)
    timestamp         = db.Column(db.DateTime, server_default=db.func.now())
    player1           = db.relationship('User', foreign_keys=[player1_id])
    player2           = db.relationship('User', foreign_keys=[player2_id])
    winner            = db.relationship('User', foreign_keys=[winner_id])

class GuestUser(UserMixin):
    def __init__(self, user_id):
        self.id = user_id
        self.username = f"Guest_{user_id[:5]}"
    @property
    def is_active(self): return True
    @property
    def is_authenticated(self): return True
    def get_id(self): return self.id

@login_manager.user_loader
def load_user(user_id):
    if session.get('is_guest'): return GuestUser(session.get('guest_id'))
    return User.query.get(int(user_id))

# ── ELO ──────────────────────────────────────────────────────────────────────
def update_elo(winner: User, loser: User):
    exp_w = 1 / (1 + 10 ** ((loser.elo - winner.elo) / 400))
    exp_l = 1 - exp_w
    winner.elo = max(0, round(winner.elo + ELO_K * (1 - exp_w)))
    loser.elo  = max(0, round(loser.elo  + ELO_K * (0 - exp_l)))

# ── Routes ───────────────────────────────────────────────────────────────────
@app.route('/')
def landing(): return render_template('landing.html')

@app.route('/rules')
def rules(): return render_template('rules.html')

@app.route('/guest')
def guest_login():
    if 'guest_id' not in session:
        session['guest_id'] = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
    session['is_guest'] = True
    login_user(GuestUser(session['guest_id']))
    return redirect(url_for('home'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated and not session.get('is_guest'):
        return redirect(url_for('home'))
    if request.method == 'POST':
        username = request.form['username'].lower()
        user = User.query.filter_by(username=username).first()
        if not user or not user.check_password(request.form['password']):
            flash('Invalid username or password'); return redirect(url_for('login'))
        login_user(user)
        session.pop('is_guest', None); session.pop('guest_id', None)
        return redirect(url_for('home'))
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated and not session.get('is_guest'):
        return redirect(url_for('home'))
    if request.method == 'POST':
        username = request.form['username'].lower()
        if User.query.filter_by(username=username).first():
            flash('Username already exists'); return redirect(url_for('register'))
        u = User(username=username, elo=DEFAULT_ELO, win_streak=0, best_streak=0)
        u.set_password(request.form['password'])
        db.session.add(u); db.session.commit()
        login_user(u)
        session.pop('is_guest', None); session.pop('guest_id', None)
        return redirect(url_for('home'))
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user(); session.clear()
    return redirect(url_for('landing'))

@app.route("/home")
@login_required
def home(): return render_template("home.html", is_guest=session.get('is_guest', False))

@app.route("/game/<room>")
@login_required
def game(room):
    active_games = get_active_games()
    if room not in active_games:
        return render_template("home.html", error="Invalid room code", is_guest=session.get('is_guest', False))
    return render_template("game.html", room=room)

@app.route("/local")
@login_required
def local():
    return render_template("local.html")

@app.route("/profile")
@login_required
def profile():
    if session.get('is_guest'):
        flash("Guests do not have profiles."); return redirect(url_for('home'))
    u = current_user
    wins   = Match.query.filter_by(winner_id=u.id).count()
    draws  = Match.query.filter(or_(Match.player1_id==u.id, Match.player2_id==u.id), Match.is_draw==True).count()
    total  = Match.query.filter(or_(Match.player1_id==u.id, Match.player2_id==u.id)).count()
    losses = total - wins - draws
    matches = Match.query.filter(or_(Match.player1_id==u.id, Match.player2_id==u.id)).order_by(Match.timestamp.desc()).all()
    return render_template("profile.html", user=u, matches=matches,
                           wins=wins, losses=losses, draws=draws)

@app.route("/leaderboard")
@login_required
def leaderboard():
    users = User.query.all()
    lb = []
    for u in users:
        ranked_games = Match.query.filter(
            or_(Match.player1_id == u.id, Match.player2_id == u.id),
            Match.is_ranked == True
        ).count()
        if ranked_games >= 10:
            ranked_wins = Match.query.filter(
                Match.winner_id == u.id, Match.is_ranked == True
            ).count()
            lb.append({
                'user': u,
                'ranked_games': ranked_games,
                'ranked_wins': ranked_wins,
                'win_rate': round(ranked_wins / ranked_games * 100),
            })
    lb.sort(key=lambda x: x['user'].elo, reverse=True)
    return render_template('leaderboard.html', leaderboard=lb)

@app.route("/match/<game_id>")
@login_required
def match_replay(game_id):
    match = Match.query.filter_by(game_id=game_id).first_or_404()
    history = json.loads(match.move_history_json) if match.move_history_json else []
    players = {'X': match.player1.username, 'O': match.player2.username}
    return render_template('match_replay.html', match=match, history=history,
                           players=players, game_id=game_id)

# ── Helpers ───────────────────────────────────────────────────────────────────
def new_room(): return ''.join(random.choices(string.digits, k=5))
def get_active_games(): return guest_games if session.get('is_guest') else games

def make_game_data(player_accounts=None, players=None, spectators=None,
                   chat_history=None, is_ai=False, move_timeout=None,
                   is_ranked=False, ai_difficulty='medium', ai_player_order='first'):
    return {
        "game":            UltimateTicTacToe(),
        "player_accounts": player_accounts or {},
        "players":         players or {},
        "spectators":      spectators or {},
        "ready":           set(),
        "rematchReady":    set(),
        "chat_history":    chat_history or [],
        "rematch_declined": False,
        "move_deadline":   None,
        "is_ai":           is_ai,
        "move_timeout":    move_timeout if move_timeout is not None else MOVE_TIMEOUT,
        "is_ranked":       is_ranked,
        "ai_difficulty":   ai_difficulty,
        "ai_player_order": ai_player_order,
    }

def full_state(game_data):
    s = game_data["game"].state()
    s["moveDeadline"]  = game_data.get("move_deadline")
    s["moveTimeout"]   = game_data.get("move_timeout", MOVE_TIMEOUT)
    s["serverNow"]     = time.time()
    s["isAI"]          = game_data.get("is_ai", False)
    s["aiDifficulty"]  = game_data.get("ai_difficulty", "medium")
    s["isRanked"]      = game_data.get("is_ranked", False)
    s["aiPlayerOrder"] = game_data.get("ai_player_order", "first")
    return s

def reset_timer(game_data):
    timeout = game_data.get("move_timeout", MOVE_TIMEOUT)
    if game_data["game"].started and not game_data["game"].game_winner:
        if timeout and timeout > 0:
            game_data["move_deadline"] = time.time() + timeout
        else:
            game_data["move_deadline"] = None  # infinity — no deadline
    else:
        game_data["move_deadline"] = None

def emit_game_status(room):
    game_data = get_active_games().get(room)
    if not game_data: return
    base = {'players': {p['symbol']: p['username'] for p in game_data['players'].values()}}
    all_sids = list(game_data['players'].keys()) + list(game_data['spectators'].keys())
    for sid in all_sids:
        p = base.copy()
        g = game_data['game']
        if not g.started:
            if len(game_data['player_accounts']) < 2:
                p['text'] = "Waiting for an opponent..."; p['button_action'] = 'hidden'
            elif sid in game_data.get('ready', set()):
                p['text'] = "Waiting for opponent to start..."; p['button_action'] = 'waiting'
            else:
                p['text'] = "Opponent has joined! Click start when ready."; p['button_action'] = 'start'
        elif g.game_winner:
            p['text'] = f"{g.game_winner} wins!" if g.game_winner != "D" else "Draw!"
            if game_data.get('rematch_declined'):
                p['button_rematch'] = 'declined'
            elif sid in game_data.get('rematchReady', set()):
                p['button_rematch'] = 'waiting'
            elif len(game_data.get('rematchReady', set())) > 0:
                p['button_rematch'] = 'prompted'
            else:
                p['button_rematch'] = 'rematch'
        else:
            p['text'] = f"Turn: {g.current_player}"; p['button_action'] = 'resign'
        emit('gameStatus', p, to=sid)

def emit_spectator_list(room):
    gd = get_active_games().get(room)
    if gd:
        emit('spectatorList', {'spectators': [s['username'] for s in gd['spectators'].values()]}, room=room)

def record_match(game_data, winner_symbol):
    for uid in game_data["player_accounts"].values():
        active_players.discard(uid)
    if session.get('is_guest') or len(game_data["player_accounts"]) < 2 or game_data.get("is_ai"):
        return

    p1_id      = game_data["player_accounts"]["X"]
    p2_id      = game_data["player_accounts"]["O"]
    is_ranked  = game_data.get("is_ranked", False)
    game_id    = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    hist_data  = [{'board': m['board'], 'cell': m['cell'], 'player': m['player']}
                  for m in game_data["game"].move_history]
    hist_json  = json.dumps(hist_data)

    if winner_symbol == "D":
        match = Match(player1_id=p1_id, player2_id=p2_id, winner_id=None,
                      is_draw=True, is_ranked=is_ranked,
                      game_id=game_id, move_history_json=hist_json)
    else:
        winner_id = game_data["player_accounts"][winner_symbol]
        loser_id  = p1_id if winner_id == p2_id else p2_id
        match = Match(player1_id=p1_id, player2_id=p2_id, winner_id=winner_id,
                      is_draw=False, is_ranked=is_ranked,
                      game_id=game_id, move_history_json=hist_json)
        w = User.query.get(winner_id)
        l = User.query.get(loser_id)
        if w and l:
            if is_ranked:
                update_elo(w, l)
            # Both ranked and casual affect streaks
            w.win_streak  = (w.win_streak or 0) + 1
            w.best_streak = max(w.best_streak or 0, w.win_streak)
            l.win_streak  = 0
    db.session.add(match)
    db.session.commit()


def _get_socket_user_id():
    """Get the current user's ID safely from session (for use in SocketIO handlers)."""
    if session.get('is_guest'):
        return session.get('guest_id')
    # Flask-Login stores user id under '_user_id' in the session
    uid = session.get('_user_id') or session.get('user_id')
    return str(uid) if uid else None

def _get_socket_username():
    """Get the current user's username safely (for use in SocketIO handlers)."""
    if session.get('is_guest'):
        gid = session.get('guest_id', '?????')
        return f"Guest_{gid[:5]}"
    uid = session.get('_user_id') or session.get('user_id')
    if uid:
        u = User.query.get(int(uid))
        if u:
            return u.username
    return 'Unknown'

# ── SocketIO Events ───────────────────────────────────────────────────────────
@socketio.on("create")
@socket_auth
def create(data=None):
    if _get_socket_user_id() in active_players:
        emit('already_in_game', {'error': 'You are already in a game.'}); return
    active_games = get_active_games()
    room         = new_room()
    is_ai        = bool(data and data.get('ai'))
    # Guests cannot play ranked — enforce server-side regardless of what the client sends
    is_ranked    = bool(data and data.get('ranked')) and not is_ai and not session.get('is_guest')
    ai_diff      = (data.get('difficulty', 'medium') if data else 'medium')
    active_games[room] = make_game_data(is_ai=is_ai, is_ranked=is_ranked, ai_difficulty=ai_diff)
    emit("created", room)

@socketio.on("join")
@socket_auth
def join(data):
    active_games = get_active_games()
    room = data["room"]; sid = request.sid
    game_data = active_games.get(room)
    if not game_data: emit("invalid"); return
    user_id   = _get_socket_user_id()
    is_locked = user_id in game_data.get("player_accounts", {}).values()
    if not is_locked and user_id in active_players:
        emit('already_in_game', {'error': 'You are already in another game.'}); return
    join_room(room)
    players = game_data["players"]
    pa      = game_data["player_accounts"]
    if is_locked:
        symbol  = next(s for s, uid in pa.items() if uid == user_id)
        old_sid = next((s for s, p in players.items() if p.get('user_id') == user_id), None)
        if old_sid: del players[old_sid]
        players[sid] = {"symbol": symbol, "user_id": user_id, "username": _get_socket_username()}
        emit("assign", symbol)
    elif len(pa) < 2:
        symbol = "X" if "X" not in pa else "O"
        pa[symbol] = user_id
        players[sid] = {"symbol": symbol, "user_id": user_id, "username": _get_socket_username()}
        active_players.add(user_id)
        emit("assign", symbol)
        if game_data.get("is_ai") and symbol == "X":
            pa["O"] = "AI"
            players["AI"] = {"symbol": "O", "user_id": "AI", "username": "🤖 AI"}
    else:
        game_data["spectators"][sid] = {"user_id": user_id, "username": _get_socket_username()}
        emit("spectator")
    if game_data.get("chat_history"):
        emit('chatHistory', {'history': game_data["chat_history"]})
    emit("state", full_state(game_data), room=room)
    emit_game_status(room)
    emit_spectator_list(room)

@socketio.on("ready")
@socket_auth
def ready(data):
    active_games = get_active_games(); room = data["room"]; sid = request.sid
    game_data = active_games.get(room)
    if not game_data or sid not in game_data["players"]: return
    game_data["ready"].add(sid)
    if game_data.get("is_ai"): game_data["ready"].add("AI")
    if len(game_data["player_accounts"]) == 2 and len(game_data["ready"]) >= 2:
        game_data["game"].started = True
        reset_timer(game_data)
        emit("state", full_state(game_data), room=room)
    emit_game_status(room)

@socketio.on("move")
@socket_auth
def move(data):
    game_data = get_active_games().get(data["room"])
    if not game_data: return
    deadline = game_data.get("move_deadline")
    if deadline and time.time() > deadline + 2:
        return
    g = game_data["game"]
    if g.make_move(data["board"], data["cell"]):
        if g.game_winner:
            game_data["move_deadline"] = None
            record_match(game_data, g.game_winner)
        else:
            reset_timer(game_data)
            # AI turn
            if game_data.get("is_ai") and not g.game_winner:
                diff    = game_data.get("ai_difficulty", "medium")
                ai_b, ai_c = get_ai_move(g, diff)
                g.make_move(ai_b, ai_c)
                # AI taunt
                taunt = maybe_taunt(diff)
                if taunt:
                    ai_sym = next((p['symbol'] for sid, p in game_data['players'].items() if sid == 'AI'), 'O')
                    entry  = {'username': '🤖 AI', 'message': taunt, 'is_spectator': False, 'symbol': ai_sym}
                    game_data['chat_history'].append(entry)
                    emit('chatMessage', entry, room=data["room"])
                if g.game_winner:
                    game_data["move_deadline"] = None
                    record_match(game_data, g.game_winner)
                else:
                    reset_timer(game_data)
        emit("state", full_state(game_data), room=data["room"])
        emit_game_status(data["room"])

@socketio.on("timeout")
@socket_auth
def timeout(data):
    room = data.get("room")
    game_data = get_active_games().get(room)
    if not game_data: return
    g = game_data["game"]
    if g.game_winner or not g.started: return
    deadline = game_data.get("move_deadline")
    if not deadline: return          # infinity mode — no timeout
    if time.time() >= deadline - 1:
        timed_out = g.current_player
        g.resign(timed_out)
        game_data["move_deadline"] = None
        record_match(game_data, g.game_winner)
        emit("state", full_state(game_data), room=room)
        emit_game_status(room)

@socketio.on("rematch")
@socket_auth
def rematch(data):
    active_games = get_active_games(); room = data["room"]; sid = request.sid
    game_data    = active_games.get(room)
    if not game_data or sid not in game_data["players"] or game_data.get('rematch_declined'): return
    game_data["rematchReady"].add(sid)
    if game_data.get("is_ai"): game_data["rematchReady"].add("AI")
    if len(game_data["rematchReady"]) >= 2:
        old_pa      = game_data["player_accounts"]
        is_ai_game  = game_data.get("is_ai", False)
        ai_order    = game_data.get("ai_player_order", "first")

        if is_ai_game:
            # For AI games: human player always stays as host.
            # Symbol assignment follows ai_player_order setting:
            #   'first'  → human is X (goes first), AI is O
            #   'second' → human is O (goes second), AI is X
            human_sym = "X" if ai_order == "first" else "O"
            ai_sym    = "O" if human_sym == "X" else "X"
            human_id  = next((uid for uid in old_pa.values() if uid != "AI"), None)
            new_pa    = {human_sym: human_id, ai_sym: "AI"}
            new_players = {}
            for s, p in game_data["players"].items():
                if s == "AI":
                    new_players[s] = {**p, "symbol": ai_sym}
                else:
                    new_players[s] = {**p, "symbol": human_sym}
                    emit("assign", human_sym, to=s)
        else:
            # Human vs human: swap sides as before
            new_pa = {}
            if "X" in old_pa and "O" in old_pa:
                new_pa["X"] = old_pa["O"]
                new_pa["O"] = old_pa["X"]
            else:
                new_pa = old_pa
            new_players = {}
            for s, p in game_data["players"].items():
                new_sym = "O" if p["symbol"] == "X" else "X"
                new_players[s] = {**p, "symbol": new_sym}
                emit("assign", new_sym, to=s)

        new_gd = make_game_data(
            player_accounts=new_pa, players=new_players,
            spectators=game_data["spectators"],
            chat_history=game_data.get("chat_history", []),
            is_ai=is_ai_game,
            move_timeout=game_data.get("move_timeout", MOVE_TIMEOUT),
            is_ranked=game_data.get("is_ranked", False),
            ai_difficulty=game_data.get("ai_difficulty", "medium"),
            ai_player_order=ai_order,
        )
        active_games[room] = new_gd
        emit("rematchAgreed", room=room)
        emit("state", full_state(new_gd), room=room)
    emit_game_status(room)

@socketio.on("leave_post_game")
@socket_auth
def leave_post_game(data):
    active_games = get_active_games(); room = data["room"]
    game_data    = active_games.get(room)
    if not game_data: return
    game_data['rematch_declined'] = True
    emit_game_status(room)

@socketio.on("leave_pre_game")
@socket_auth
def leave_pre_game(data):
    active_games = get_active_games()
    room         = data.get("room")
    game_data    = active_games.get(room)
    if not game_data or game_data['game'].started: return
    sid     = request.sid
    user_id = _get_socket_user_id()
    if sid in game_data['players']:
        symbol = game_data['players'][sid].get('symbol')
        del game_data['players'][sid]
        if symbol:
            game_data['player_accounts'].pop(symbol, None)
        active_players.discard(user_id)
        game_data['ready'].discard(sid)
        leave_room(room)
        emit_game_status(room)

@socketio.on("update_settings")
@socket_auth
def update_settings(data):
    active_games = get_active_games()
    room         = data.get("room")
    game_data    = active_games.get(room)
    if not game_data or game_data['game'].started: return
    sid    = request.sid
    player = game_data['players'].get(sid)
    if not player or player['symbol'] != 'X': return

    # Timer
    raw_timeout = data.get('move_timeout')
    if raw_timeout is None or raw_timeout == 0:
        game_data['move_timeout'] = 0  # infinity
    else:
        game_data['move_timeout'] = max(10, min(300, int(raw_timeout)))

    # AI difficulty + player order (only when AI game)
    if game_data.get('is_ai'):
        diff = data.get('ai_difficulty', game_data.get('ai_difficulty', 'medium'))
        if diff in ('easy', 'medium', 'hard'):
            game_data['ai_difficulty'] = diff
        order = data.get('ai_player_order', game_data.get('ai_player_order', 'first'))
        if order in ('first', 'second'):
            game_data['ai_player_order'] = order

    emit('settingsUpdated', {
        'move_timeout':    game_data['move_timeout'],
        'ai_difficulty':   game_data.get('ai_difficulty', 'medium'),
        'ai_player_order': game_data.get('ai_player_order', 'first'),
    }, room=room)

@socketio.on('disconnect')
def disconnect():
    sid = request.sid
    for g in [games, guest_games]:
        for room, game_data in list(g.items()):
            if sid in game_data.get("players", {}):
                del game_data["players"][sid]
                if game_data['game'].game_winner:
                    game_data['rematch_declined'] = True
                emit_game_status(room)
                return
            elif sid in game_data.get("spectators", {}):
                del game_data["spectators"][sid]
                leave_room(room)
                emit_spectator_list(room)
                return

@socketio.on('chat')
@socket_auth
def chat(data):
    room = data['room']; message = data['message']
    game_data = get_active_games().get(room)
    if not game_data: return
    is_spectator  = request.sid in game_data['spectators']
    player_symbol = None
    if not is_spectator:
        pd = game_data['players'].get(request.sid)
        if pd: player_symbol = pd['symbol']
    entry = {'username': _get_socket_username(), 'message': message,
             'is_spectator': is_spectator, 'symbol': player_symbol}
    game_data["chat_history"].append(entry)
    emit('chatMessage', entry, room=room)

@socketio.on("resign")
@socket_auth
def resign(data):
    game_data = get_active_games().get(data["room"])
    if not game_data: return
    g      = game_data["game"]
    loser  = data["symbol"]
    winner = "X" if loser == "O" else "O"
    g.resign(loser)
    game_data["move_deadline"] = None
    record_match(game_data, winner)
    emit("state", full_state(game_data), room=data["room"])
    emit_game_status(data["room"])

def _ensure_db_columns():
    """Add columns that exist in the model but are missing from the DB (avoids needing flask db upgrade)."""
    from sqlalchemy import inspect, text
    with app.app_context():
        db.create_all()
        insp = inspect(db.engine)
        match_cols = {c['name'] for c in insp.get_columns('match')}
        user_cols  = {c['name'] for c in insp.get_columns('user')}
        with db.engine.connect() as conn:
            for col, sql in [
                ('is_ranked',         'ALTER TABLE "match" ADD COLUMN is_ranked BOOLEAN NOT NULL DEFAULT 0'),
                ('game_id',           'ALTER TABLE "match" ADD COLUMN game_id VARCHAR(8)'),
                ('move_history_json', 'ALTER TABLE "match" ADD COLUMN move_history_json TEXT'),
            ]:
                if col not in match_cols:
                    conn.execute(text(sql))
                    print(f"[db] Added match.{col}")
            for col, sql in [
                ('elo',          'ALTER TABLE "user" ADD COLUMN elo INTEGER NOT NULL DEFAULT 1000'),
                ('win_streak',   'ALTER TABLE "user" ADD COLUMN win_streak INTEGER NOT NULL DEFAULT 0'),
                ('best_streak',  'ALTER TABLE "user" ADD COLUMN best_streak INTEGER NOT NULL DEFAULT 0'),
            ]:
                if col not in user_cols:
                    conn.execute(text(sql))
                    print(f"[db] Added user.{col}")
            conn.commit()

_ensure_db_columns()

if __name__ == "__main__":
    socketio.run(app, debug=True)
