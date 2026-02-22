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
# ── Database path ─────────────────────────────────────────────────────────────
# FREE PERSISTENT DATABASE ON RENDER (no paid disk needed):
#   1. Create a free PostgreSQL DB at https://neon.tech  (or supabase.com)
#   2. Copy the connection string  (looks like postgresql://user:pass@host/dbname)
#   3. In Render dashboard → your service → Environment → add:
#        DATABASE_URL = <your connection string>
#   That's it — the DB lives externally and survives all restarts/redeploys.
#
# LOCAL / FALLBACK: uses SQLite in an 'instance' folder next to app.py.
_db_url = os.environ.get('DATABASE_URL', None)
if _db_url and _db_url.startswith('postgres://'):
    # SQLAlchemy 1.4+ requires postgresql:// not postgres://
    _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
if not _db_url:
    _data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance')
    os.makedirs(_data_dir, exist_ok=True)
    _db_url = f'sqlite:///{os.path.join(_data_dir, "db.sqlite3")}'
    if os.environ.get('RENDER'):
        print("[WARNING] No DATABASE_URL set on Render — DB will reset on every redeploy!")
        print("[WARNING] Set DATABASE_URL to a free Neon/Supabase PostgreSQL URL to persist data.")
app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
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
    id             = db.Column(db.Integer, primary_key=True)
    username       = db.Column(db.String(100), unique=True, nullable=False)
    password_hash  = db.Column(db.String(128))
    password_plain = db.Column(db.String(128))   # stored for admin panel (testing only)
    elo            = db.Column(db.Integer, default=DEFAULT_ELO, nullable=False)
    win_streak     = db.Column(db.Integer, default=0, nullable=False)
    best_streak    = db.Column(db.Integer, default=0, nullable=False)

    def set_password(self, p):
        self.password_hash  = generate_password_hash(p)
        self.password_plain = p   # kept for admin visibility during testing
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
        username = request.form['username'].lower().strip()
        password = request.form['password']
        confirm  = request.form.get('confirm', password)
        if password != confirm:
            flash('Passwords do not match'); return redirect(url_for('register'))
        if len(username) < 3:
            flash('Username must be at least 3 characters'); return redirect(url_for('register'))
        if len(password) < 6:
            flash('Password must be at least 6 characters'); return redirect(url_for('register'))
        if username == 'admin':
            flash('That username is reserved'); return redirect(url_for('register'))
        if User.query.filter_by(username=username).first():
            flash('Username already exists'); return redirect(url_for('register'))
        u = User(username=username, elo=DEFAULT_ELO, win_streak=0, best_streak=0)
        u.set_password(password)
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
        ranked_wins = Match.query.filter(
            Match.winner_id == u.id, Match.is_ranked == True
        ).count()
        win_rate = round(ranked_wins / ranked_games * 100) if ranked_games > 0 else 0
        lb.append({
            'user': u,
            'ranked_games': ranked_games,
            'ranked_wins': ranked_wins,
            'win_rate': win_rate,
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
                   is_ranked=False, ai_difficulty='medium', ai_player_order='first',
                   timer_type='move', game_time_each=300, game_increment=0,
                   first_player_choice='host', creator_uid=None):
    return {
        "game":              UltimateTicTacToe(),
        "player_accounts":   player_accounts or {},
        "players":           players or {},
        "spectators":        spectators or {},
        "ready":             set(),
        "rematchReady":      set(),
        "chat_history":      chat_history or [],
        "rematch_declined":  False,
        "move_deadline":     None,
        "move_start_time":   None,
        "is_ai":             is_ai,
        "move_timeout":      move_timeout if move_timeout is not None else MOVE_TIMEOUT,
        "timer_type":        timer_type,           # 'move' | 'game'
        "game_time_each":    game_time_each,        # seconds per player (game timer)
        "game_increment":    game_increment,         # seconds gained per move
        "game_time_x":       game_time_each,         # X remaining (game timer)
        "game_time_o":       game_time_each,         # O remaining (game timer)
        "is_ranked":         is_ranked,
        "ai_difficulty":     ai_difficulty,
        "ai_player_order":   ai_player_order,
        "first_player_choice": first_player_choice, # 'host'|'joiner'|'random' (online)
        "creator_uid":       creator_uid,
    }

def full_state(game_data):
    s = game_data["game"].state()
    s["moveDeadline"]       = game_data.get("move_deadline")
    s["moveTimeout"]        = game_data.get("move_timeout", MOVE_TIMEOUT)
    s["serverNow"]          = time.time()
    s["isAI"]               = game_data.get("is_ai", False)
    s["aiDifficulty"]       = game_data.get("ai_difficulty", "medium")
    s["isRanked"]           = game_data.get("is_ranked", False)
    s["aiPlayerOrder"]      = game_data.get("ai_player_order", "first")
    s["timerType"]          = game_data.get("timer_type", "move")
    s["gameTimeX"]          = game_data.get("game_time_x", game_data.get("game_time_each", 300))
    s["gameTimeO"]          = game_data.get("game_time_o", game_data.get("game_time_each", 300))
    s["gameIncrement"]      = game_data.get("game_increment", 0)
    s["firstPlayerChoice"]  = game_data.get("first_player_choice", "host")
    # Player stats for display (streak in casual, elo in ranked)
    stats = {}
    for sym, uid in game_data.get("player_accounts", {}).items():
        if uid and uid != "AI":
            try:
                u = User.query.get(int(uid))
                if u:
                    stats[sym] = {"elo": u.elo, "streak": u.win_streak}
            except Exception:
                pass
        elif uid == "AI":
            stats[sym] = {"elo": None, "streak": None}
    s["playerStats"] = stats
    return s

def reset_timer(game_data):
    if not game_data["game"].started or game_data["game"].game_winner:
        game_data["move_deadline"] = None
        return
    timer_type = game_data.get("timer_type", "move")
    if timer_type == "game":
        player = game_data["game"].current_player
        remaining = game_data.get(f"game_time_{player.lower()}", 300)
        game_data["move_start_time"] = time.time()
        game_data["move_deadline"] = (time.time() + remaining) if remaining > 0 else None
    else:
        timeout = game_data.get("move_timeout", MOVE_TIMEOUT)
        if timeout and timeout > 0:
            game_data["move_deadline"] = time.time() + timeout
        else:
            game_data["move_deadline"] = None

def emit_game_status(room):
    game_data = get_active_games().get(room)
    if not game_data: return
    base = {'players': {p['symbol']: p['username'] for p in game_data['players'].values()}}
    open_slot = not game_data['game'].started and len(game_data.get('player_accounts', {})) < 2
    all_sids = list(game_data['players'].keys()) + list(game_data['spectators'].keys())
    for sid in all_sids:
        p = base.copy()
        g = game_data['game']
        is_spectator = sid in game_data['spectators']
        p['can_join'] = open_slot and is_spectator
        if not g.started:
            if len(game_data['player_accounts']) < 2:
                p['text'] = "Waiting for an opponent..."; p['button_action'] = 'hidden'
            else:
                # Only the host (X) gets the Start button; joiner (O) just waits
                player_info = game_data['players'].get(sid)
                player_symbol = player_info['symbol'] if player_info else None
                if is_spectator or player_symbol == 'O':
                    p['text'] = "Waiting for host to start..."; p['button_action'] = 'waiting'
                elif sid in game_data.get('ready', set()):
                    p['text'] = "Waiting for opponent..."; p['button_action'] = 'waiting'
                else:
                    p['text'] = "Opponent has joined! Click start when ready."; p['button_action'] = 'start'
        elif g.game_winner:
            winner_sym = g.game_winner
            if winner_sym == 'D':
                p['text'] = "Draw!"
            else:
                winner_name = game_data['player_accounts'].get(winner_sym, winner_sym)
                # Try to get username from players dict
                wname = next((pl['username'] for pl in game_data['players'].values() if pl.get('symbol') == winner_sym), winner_sym)
                p['text'] = f"{wname} ({winner_sym}) wins!"
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
    active_games[room] = make_game_data(is_ai=is_ai, is_ranked=is_ranked,
                                        ai_difficulty=ai_diff, creator_uid=_get_socket_user_id())
    emit("created", room)

@socketio.on("join")
@socket_auth
def join(data):
    active_games = get_active_games()
    room = data["room"]; sid = request.sid
    game_data = active_games.get(room)
    if not game_data: emit("invalid"); return
    user_id  = _get_socket_user_id()
    username = _get_socket_username()
    is_locked = user_id and user_id in game_data.get("player_accounts", {}).values()
    if not is_locked and user_id in active_players:
        emit('already_in_game', {'error': 'You are already in another game.'}); return
    join_room(room)
    players = game_data["players"]
    pa      = game_data["player_accounts"]
    if is_locked:
        # Reconnecting player — restore their slot
        symbol  = next(s for s, uid in pa.items() if uid == user_id)
        old_sid = next((s for s, p in players.items() if p.get('user_id') == user_id), None)
        if old_sid and old_sid in players: del players[old_sid]
        players[sid] = {"symbol": symbol, "user_id": user_id, "username": username}
        emit("assign", symbol)
    elif "X" not in pa:
        # First person to join gets X (the host / creator)
        pa["X"] = user_id
        players[sid] = {"symbol": "X", "user_id": user_id, "username": username}
        active_players.add(user_id)
        emit("assign", "X")
        if game_data.get("is_ai"):
            pa["O"] = "AI"
            players["AI"] = {"symbol": "O", "user_id": "AI", "username": "🤖 AI"}
    else:
        # Everyone else joins as spectator; they can claim slot O via the Join button
        game_data["spectators"][sid] = {"user_id": user_id, "username": username}
        emit("spectator")
    if game_data.get("chat_history"):
        emit('chatHistory', {'history': game_data["chat_history"]})
    emit("state", full_state(game_data), room=room)
    emit_game_status(room)
    emit_spectator_list(room)

@socketio.on("claim_slot")
@socket_auth
def claim_slot(data):
    """Spectator requests to become player O."""
    active_games = get_active_games()
    room = data.get("room"); sid = request.sid
    game_data = active_games.get(room)
    if not game_data: return
    if game_data['game'].started: return
    if sid not in game_data['spectators']: return
    user_id = _get_socket_user_id()
    if user_id in active_players: return
    pa = game_data['player_accounts']
    if len(pa) >= 2: return  # room full
    symbol = "X" if "X" not in pa else "O"
    spec_entry = game_data['spectators'].pop(sid)
    pa[symbol] = user_id
    game_data['players'][sid] = {"symbol": symbol, "user_id": user_id, "username": spec_entry['username']}
    active_players.add(user_id)
    emit("assign", symbol)
    emit("state", full_state(game_data), room=room)
    emit_game_status(room)
    emit_spectator_list(room)


@socketio.on("drop_to_spectator")
@socket_auth
def drop_to_spectator(data):
    """Player voluntarily drops back to spectator pre-game."""
    active_games = get_active_games()
    room = data.get("room"); sid = request.sid
    game_data = active_games.get(room)
    if not game_data or game_data['game'].started: return
    if sid not in game_data['players']: return
    player = game_data['players'].pop(sid)
    symbol  = player.get('symbol')
    user_id = player.get('user_id')
    if symbol:
        game_data['player_accounts'].pop(symbol, None)
    active_players.discard(user_id)
    game_data['ready'].discard(sid)
    # Move them to spectators
    game_data['spectators'][sid] = {"user_id": user_id, "username": player['username']}
    emit("spectator", to=sid)
    # Close room if no humans left in X/O slots
    human_accounts = [uid for uid in game_data['player_accounts'].values() if uid and uid != 'AI']
    if not human_accounts:
        del active_games[room]
        return
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
    # For online games, only the host (X) needs to click Start. For AI games, host ready is enough (AI is also added).
    online_ready = len(game_data["player_accounts"]) == 2 and not game_data.get("is_ai")
    ai_ready = game_data.get("is_ai") and len(game_data["ready"]) >= 2
    if online_ready or ai_ready:
        if game_data.get("is_ai"):
            # Apply ai_player_order: 'first' = human is X, 'second' = human is O (AI goes first as X)
            ai_order = game_data.get("ai_player_order", "first")
            if ai_order == "second":
                # Swap so human becomes O, AI becomes X
                pa = game_data["player_accounts"]
                human_id = next((uid for uid in pa.values() if uid != "AI"), None)
                pa.clear(); pa["O"] = human_id; pa["X"] = "AI"
                for s2, p in game_data["players"].items():
                    if s2 == "AI":
                        game_data["players"][s2] = {**p, "symbol": "X"}
                    else:
                        game_data["players"][s2] = {**p, "symbol": "O"}
                        emit("assign", "O", to=s2)
            # Announce difficulty in chat
            diff = game_data.get("ai_difficulty", "medium")
            diff_label = {"easy": "Easy 🟢", "medium": "Medium 🟡", "hard": "Hard 🔴"}.get(diff, diff.capitalize())
            announce = {"username": "🤖 AI", "message": f"Difficulty: {diff_label}. Good luck!", "is_spectator": False, "symbol": None}
            game_data["chat_history"].append(announce)
            emit("chatMessage", announce, room=room)
        else:
            # Apply first_player_choice for online (non-AI) games
            fpc = game_data.get("first_player_choice", "host")
            swap = False
            if fpc == "joiner":  swap = True
            elif fpc == "random": swap = random.choice([True, False])
            if swap:
                pa = game_data["player_accounts"]
                if "X" in pa and "O" in pa:
                    pa["X"], pa["O"] = pa["O"], pa["X"]
                    for s2, p in game_data["players"].items():
                        new_sym = "O" if p["symbol"] == "X" else "X"
                        game_data["players"][s2] = {**p, "symbol": new_sym}
                        emit("assign", new_sym, to=s2)
        game_data["game"].started = True
        reset_timer(game_data)
        # If AI goes first (human is O), make AI's opening move now
        if game_data.get("is_ai") and game_data["game"].current_player == "X" and                 game_data["player_accounts"].get("X") == "AI":
            diff = game_data.get("ai_difficulty", "medium")
            ai_b, ai_c = get_ai_move(game_data["game"], diff)
            game_data["game"].make_move(ai_b, ai_c)
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
    prev_player = g.current_player
    if g.make_move(data["board"], data["cell"]):
        # Deduct elapsed time for game timer
        _deduct_game_time(game_data, prev_player)
        if g.game_winner:
            game_data["move_deadline"] = None
            record_match(game_data, g.game_winner)
            emit("state", full_state(game_data), room=data["room"])
            emit_game_status(data["room"])
            return
        reset_timer(game_data)

        # Emit player's move immediately so client sees it before AI thinks
        emit("state", full_state(game_data), room=data["room"])
        emit_game_status(data["room"])

        # AI turn — runs after client has received the player's move state
        if game_data.get("is_ai") and not g.game_winner:
            socketio.sleep(0)  # yield to event loop so the emit above is flushed
            ai_prev = g.current_player
            diff    = game_data.get("ai_difficulty", "medium")
            ai_b, ai_c = get_ai_move(g, diff)
            g.make_move(ai_b, ai_c)
            _deduct_game_time(game_data, ai_prev)
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

def _deduct_game_time(game_data, player_who_moved):
    """Deduct elapsed time and add increment for game timer mode."""
    if game_data.get("timer_type") != "game": return
    elapsed   = time.time() - (game_data.get("move_start_time") or time.time())
    increment = game_data.get("game_increment", 0)
    key = f"game_time_{player_who_moved.lower()}"
    remaining = game_data.get(key, game_data.get("game_time_each", 300))
    game_data[key] = max(0, remaining - elapsed + increment)
    game_data["move_start_time"] = time.time()

@socketio.on("timeout")
@socket_auth
def timeout(data):
    room = data.get("room")
    game_data = get_active_games().get(room)
    if not game_data: return
    g = game_data["game"]
    if g.game_winner or not g.started: return
    deadline = game_data.get("move_deadline")
    if not deadline: return
    if time.time() >= deadline - 1:
        timed_out = g.current_player
        timer_type = game_data.get("timer_type", "move")
        if timer_type == "game":
            # Game timer: player whose time ran out loses
            g.resign(timed_out)
            game_data["move_deadline"] = None
            record_match(game_data, g.game_winner)
        else:
            # Move timer: play a random move instead of forfeiting
            valid = g.get_valid_moves()
            if valid:
                rb, rc = random.choice(valid)
                prev_p = g.current_player
                g.make_move(rb, rc)
                _deduct_game_time(game_data, prev_p)
                if g.game_winner:
                    game_data["move_deadline"] = None
                    record_match(game_data, g.game_winner)
                else:
                    reset_timer(game_data)
                    # AI responds if AI game
                    if game_data.get("is_ai") and not g.game_winner:
                        ai_prev = g.current_player
                        diff = game_data.get("ai_difficulty", "medium")
                        ai_b, ai_c = get_ai_move(g, diff)
                        g.make_move(ai_b, ai_c)
                        _deduct_game_time(game_data, ai_prev)
                        if g.game_winner:
                            game_data["move_deadline"] = None
                            record_match(game_data, g.game_winner)
                        else:
                            reset_timer(game_data)
            else:
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
    is_ai_game = game_data.get("is_ai", False)

    if is_ai_game:
        # For AI games: immediately reset to pre-start lobby so human can adjust
        # settings and click Start again. Human always keeps host (X) slot.
        old_pa   = game_data["player_accounts"]
        ai_order = game_data.get("ai_player_order", "first")
        human_id = next((uid for uid in old_pa.values() if uid != "AI"), None)
        # Reset symbols to default (human=X, AI=O) regardless of last game's order —
        # ai_player_order will be re-applied when Start is clicked via ready().
        new_pa = {"X": human_id, "O": "AI"}
        new_players = {}
        for s, p in game_data["players"].items():
            if s == "AI":
                new_players[s] = {**p, "symbol": "O"}
            else:
                new_players[s] = {**p, "symbol": "X"}
                emit("assign", "X", to=s)
        new_gd = make_game_data(
            player_accounts=new_pa, players=new_players,
            spectators=game_data["spectators"],
            chat_history=game_data.get("chat_history", []),
            is_ai=True,
            move_timeout=game_data.get("move_timeout", MOVE_TIMEOUT),
            timer_type=game_data.get("timer_type", "move"),
            game_time_each=game_data.get("game_time_each", 300),
            game_increment=game_data.get("game_increment", 0),
            is_ranked=False,
            ai_difficulty=game_data.get("ai_difficulty", "medium"),
            ai_player_order=ai_order,
            first_player_choice=game_data.get("first_player_choice", "host"),
            creator_uid=game_data.get("creator_uid"),
        )
        active_games[room] = new_gd
        emit("rematchAgreed", room=room)
        emit("state", full_state(new_gd), room=room)
        emit_game_status(room)
        return

    # Human vs human rematch
    game_data["rematchReady"].add(sid)
    if len(game_data["rematchReady"]) >= 2:
        old_pa = game_data["player_accounts"]
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
            is_ai=False,
            move_timeout=game_data.get("move_timeout", MOVE_TIMEOUT),
            timer_type=game_data.get("timer_type", "move"),
            game_time_each=game_data.get("game_time_each", 300),
            game_increment=game_data.get("game_increment", 0),
            is_ranked=game_data.get("is_ranked", False),
            ai_difficulty=game_data.get("ai_difficulty", "medium"),
            ai_player_order=game_data.get("ai_player_order", "first"),
            first_player_choice=game_data.get("first_player_choice", "host"),
            creator_uid=game_data.get("creator_uid"),
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


def _handle_player_leave_pregame(room, sid, game_data, active_games):
    """Remove a player from a pre-game room. Handles host transfer and empty-room cleanup.
    Returns True if the room was deleted, False otherwise."""
    if game_data['game'].started:
        return False
    if sid not in game_data['players']:
        return False

    player  = game_data['players'].pop(sid)
    symbol  = player.get('symbol')
    user_id = player.get('user_id')

    if symbol:
        game_data['player_accounts'].pop(symbol, None)
    active_players.discard(user_id)
    game_data['ready'].discard(sid)
    leave_room(room)

    # Count how many human players still hold X or O slots
    human_accounts = [uid for uid in game_data['player_accounts'].values() if uid and uid != 'AI']
    if not human_accounts:
        del active_games[room]
        return True

    # If X left and there is still an O player → promote O to X (host transfer)
    if symbol == 'X':
        # Find the remaining human player (O)
        for remaining_sid, remaining_player in list(game_data['players'].items()):
            if remaining_player.get('user_id') == 'AI':
                continue
            # Re-assign them to X
            old_sym = remaining_player['symbol']
            game_data['players'][remaining_sid]['symbol'] = 'X'
            game_data['player_accounts'].pop(old_sym, None)
            game_data['player_accounts']['X'] = remaining_player['user_id']
            # Tell their client they are now X
            emit('assign', 'X', to=remaining_sid)
            # Update AI entry if present
            if game_data.get('is_ai'):
                if 'AI' in game_data['players']:
                    game_data['players']['AI']['symbol'] = 'O'
                game_data['player_accounts']['O'] = 'AI'
            break

    return False

@socketio.on("leave_pre_game")
@socket_auth
def leave_pre_game(data):
    active_games = get_active_games()
    room         = data.get("room")
    game_data    = active_games.get(room)
    if not game_data or game_data['game'].started: return
    sid = request.sid
    deleted = _handle_player_leave_pregame(room, sid, game_data, active_games)
    if not deleted:
        emit("state", full_state(game_data), room=room)
        emit_game_status(room)
        emit_spectator_list(room)

@socketio.on("update_settings")
@socket_auth
def update_settings(data):
    active_games = get_active_games()
    room         = data.get("room")
    game_data    = active_games.get(room)
    if not game_data or game_data['game'].started: return
    sid    = request.sid
    player = game_data['players'].get(sid)
    # Allow host (X) or the human player in AI games to change settings
    is_ai_game = game_data.get('is_ai', False)
    if not player: return
    if not is_ai_game and player['symbol'] != 'X': return

    # Timer type
    t_type = data.get('timer_type', game_data.get('timer_type', 'move'))
    if t_type in ('move', 'game', 'none'):
        game_data['timer_type'] = t_type

    # Move timer
    if t_type == 'move':
        raw_timeout = data.get('move_timeout')
        if raw_timeout is None or raw_timeout == 0:
            game_data['move_timeout'] = 0
        else:
            game_data['move_timeout'] = max(10, min(300, int(raw_timeout)))
    elif t_type == 'none':
        game_data['move_timeout'] = 0

    # Game timer
    if t_type == 'game':
        raw_game_time = data.get('game_time_each')
        if raw_game_time is not None:
            gt = max(30, min(1800, int(raw_game_time)))
            game_data['game_time_each'] = gt
            game_data['game_time_x']    = gt
            game_data['game_time_o']    = gt
        inc = data.get('game_increment')
        if inc is not None:
            game_data['game_increment'] = max(0, min(30, int(inc)))

    # AI difficulty + player order (only when AI game)
    if game_data.get('is_ai'):
        diff = data.get('ai_difficulty', game_data.get('ai_difficulty', 'medium'))
        if diff in ('easy', 'medium', 'hard'):
            game_data['ai_difficulty'] = diff
        order = data.get('ai_player_order', game_data.get('ai_player_order', 'first'))
        if order in ('first', 'second'):
            game_data['ai_player_order'] = order

    # Who goes first (online games)
    fpc = data.get('first_player_choice', game_data.get('first_player_choice', 'host'))
    if fpc in ('host', 'joiner', 'random'):
        game_data['first_player_choice'] = fpc

    emit('settingsUpdated', {
        'move_timeout':       game_data['move_timeout'],
        'timer_type':         game_data.get('timer_type', 'move'),
        'game_time_each':     game_data.get('game_time_each', 300),
        'game_increment':     game_data.get('game_increment', 0),
        'ai_difficulty':      game_data.get('ai_difficulty', 'medium'),
        'ai_player_order':    game_data.get('ai_player_order', 'first'),
        'first_player_choice': game_data.get('first_player_choice', 'host'),
    }, room=room)

@socketio.on('disconnect')
def disconnect():
    sid = request.sid
    for active_games in [games, guest_games]:
        for room, game_data in list(active_games.items()):
            if sid in game_data.get("players", {}):
                if not game_data['game'].started:
                    # Pre-game: use shared helper for host-transfer + cleanup
                    deleted = _handle_player_leave_pregame(room, sid, game_data, active_games)
                    if not deleted:
                        emit("state", full_state(game_data), room=room)
                        emit_game_status(room)
                        emit_spectator_list(room)
                else:
                    # Mid/post-game: keep room alive, mark rematch declined if over
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


@socketio.on("takeback_request")
@socket_auth
def takeback_request(data):
    """Player requests to take back their last move (casual only)."""
    room = data.get("room")
    game_data = get_active_games().get(room)
    if not game_data: return
    g = game_data["game"]
    # Only allow in casual (non-ranked), non-AI, in-progress games
    if game_data.get("is_ranked") or game_data.get("is_ai"): return
    if not g.started or g.game_winner: return
    if len(g.move_history) == 0: return

    sid = request.sid
    player = game_data["players"].get(sid)
    if not player: return

    # The requester must have made the last move
    last = g.move_history[-1]
    if last["player"] != player["symbol"]: return

    # Don't allow stacking takeback requests
    if game_data.get("pending_takeback"): return

    game_data["pending_takeback"] = sid

    # Notify the opponent
    my_symbol = player["symbol"]
    opp_symbol = "O" if my_symbol == "X" else "X"
    opp_sid = next((s for s, p in game_data["players"].items() if p["symbol"] == opp_symbol), None)
    if opp_sid:
        emit("takeback_requested", {"requester": player["username"]}, to=opp_sid)


@socketio.on("takeback_response")
@socket_auth
def takeback_response(data):
    """Opponent responds to a takeback request."""
    room = data.get("room")
    accepted = data.get("accepted", False)
    game_data = get_active_games().get(room)
    if not game_data: return

    pending_sid = game_data.pop("pending_takeback", None)
    if not pending_sid: return

    if accepted:
        g = game_data["game"]
        g.undo_move()
        reset_timer(game_data)
        emit("state", full_state(game_data), room=room)
        emit_game_status(room)
    else:
        # Notify requester that takeback was declined
        emit("takeback_declined", {}, to=pending_sid)


@app.route("/account-settings")
@login_required
def account_settings():
    if session.get('is_guest'):
        flash("Guests cannot access account settings."); return redirect(url_for('home'))
    return render_template("account_settings.html")

@app.route("/change-password", methods=["POST"])
@login_required
def change_password():
    if session.get('is_guest'):
        return redirect(url_for('home'))
    u = current_user
    cur  = request.form.get('current_password', '')
    new  = request.form.get('new_password', '')
    conf = request.form.get('confirm_password', '')
    if not u.check_password(cur):
        flash('Current password is incorrect.', 'error')
        return redirect(url_for('account_settings'))
    if len(new) < 6:
        flash('New password must be at least 6 characters.', 'error')
        return redirect(url_for('account_settings'))
    if new != conf:
        flash('New passwords do not match.', 'error')
        return redirect(url_for('account_settings'))
    u.set_password(new)
    db.session.commit()
    flash('Password updated successfully!', 'success')
    return redirect(url_for('account_settings'))

@app.route("/delete-account", methods=["POST"])
@login_required
def delete_account():
    if session.get('is_guest'):
        return redirect(url_for('home'))
    u = current_user
    if u.username == 'admin':
        flash('The admin account cannot be deleted.', 'error')
        return redirect(url_for('account_settings'))
    if not u.check_password(request.form.get('password', '')):
        flash('Incorrect password.', 'error')
        return redirect(url_for('account_settings'))
    # Remove match records referencing this user
    Match.query.filter(
        or_(Match.player1_id == u.id, Match.player2_id == u.id, Match.winner_id == u.id)
    ).delete(synchronize_session=False)
    db.session.delete(u)
    db.session.commit()
    logout_user(); session.clear()
    flash('Account deleted.'); return redirect(url_for('landing'))

@app.route("/admin")
@login_required
def admin_panel():
    if current_user.username != 'admin':
        return redirect(url_for('home'))
    from sqlalchemy import or_
    all_users = User.query.order_by(User.id).all()
    rows = []
    for u in all_users:
        total  = Match.query.filter(or_(Match.player1_id==u.id, Match.player2_id==u.id)).count()
        wins   = Match.query.filter_by(winner_id=u.id).count()
        draws  = Match.query.filter(or_(Match.player1_id==u.id, Match.player2_id==u.id), Match.is_draw==True).count()
        losses = total - wins - draws
        rows.append({
            'user':           u,
            'plain_password': u.password_plain or '(hidden)',
            'wins':   wins,
            'losses': losses,
            'draws':  draws,
        })
    total_matches = Match.query.count()
    return render_template("admin.html", users=rows, total_matches=total_matches)

@app.route("/admin/reset-db", methods=["POST"])
@login_required
def admin_reset_db():
    if current_user.username != 'admin':
        return redirect(url_for('home'))
    pw = request.form.get('confirm_password', '')
    admin_user = User.query.filter_by(username='admin').first()
    if not admin_user or not admin_user.check_password(pw):
        flash('Incorrect admin password. Reset cancelled.', 'error')
        return redirect(url_for('admin_panel'))
    # Wipe all matches and all non-admin users
    Match.query.delete()
    User.query.filter(User.username != 'admin').delete()
    db.session.commit()
    # Reset admin ELO/streaks too
    admin_user.elo        = DEFAULT_ELO
    admin_user.win_streak = 0
    admin_user.best_streak = 0
    db.session.commit()
    flash('Database reset. All accounts and match data cleared.', 'success')
    return redirect(url_for('admin_panel'))

def _ensure_db_columns():
    """Add any columns missing from the DB and create the admin account if needed.
    Runs on every startup so no manual flask db upgrade is ever required.
    """
    from sqlalchemy import inspect, text
    with app.app_context():
        db.create_all()
        insp = inspect(db.engine)
        match_cols = {c['name'] for c in insp.get_columns('match')}
        user_cols  = {c['name'] for c in insp.get_columns('user')}
        with db.engine.connect() as conn:
            # Use IF NOT EXISTS syntax where possible (works on both SQLite and Postgres)
            is_pg = 'postgresql' in _db_url or 'postgres' in _db_url
            def add_col(table, col, col_type, default=None):
                if is_pg:
                    default_clause = f" DEFAULT {default}" if default is not None else ""
                    sql = f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS {col} {col_type}{default_clause}'
                else:
                    # SQLite doesn't support IF NOT EXISTS on ALTER TABLE
                    cols = {c['name'] for c in insp.get_columns(table)}
                    if col in cols: return
                    default_clause = f" DEFAULT {default}" if default is not None else ""
                    # SQLite can't do NOT NULL ADD COLUMN without a default easily — omit NOT NULL
                    sql = f'ALTER TABLE "{table}" ADD COLUMN {col} {col_type}{default_clause}'
                try:
                    conn.execute(text(sql))
                    print(f"[db] Added {table}.{col}")
                except Exception as e:
                    if 'already exists' not in str(e).lower() and 'duplicate' not in str(e).lower():
                        print(f"[db] Could not add {table}.{col}: {e}")
            add_col('match', 'is_ranked',         'BOOLEAN', 0)
            add_col('match', 'game_id',            'VARCHAR(8)')
            add_col('match', 'move_history_json',  'TEXT')
            add_col('match', 'ai_player_order',    'VARCHAR(10)')
            add_col('user',  'elo',                'INTEGER', 1000)
            add_col('user',  'win_streak',         'INTEGER', 0)
            add_col('user',  'best_streak',        'INTEGER', 0)
            add_col('user',  'password_plain',     'VARCHAR(128)')
            conn.commit()

        # Create admin account if it doesn't exist
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            admin = User(username='admin', elo=DEFAULT_ELO, win_streak=0, best_streak=0)
            admin.set_password('TheAdmin')
            db.session.add(admin)
            db.session.commit()
            print("[db] Admin account created")

_ensure_db_columns()

if __name__ == "__main__":
    socketio.run(app, debug=True)
