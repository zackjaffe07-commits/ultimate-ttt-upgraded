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
from game.ai import get_ai_move
import random, string, os, time, math

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'a_secret_key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///db.sqlite3')
db = SQLAlchemy(app)
migrate = Migrate(app, db)
socketio = SocketIO(app, async_mode='gevent')
login_manager = LoginManager(app)
login_manager.login_view = 'landing'

MOVE_TIMEOUT = 30   # seconds per move
ELO_K = 32
DEFAULT_ELO = 1200

games = {}
guest_games = {}
active_players = set()

# --- Models ---
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
    id          = db.Column(db.Integer, primary_key=True)
    player1_id  = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    player2_id  = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    winner_id   = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    is_draw     = db.Column(db.Boolean, default=False, nullable=False)
    timestamp   = db.Column(db.DateTime, server_default=db.func.now())
    player1     = db.relationship('User', foreign_keys=[player1_id])
    player2     = db.relationship('User', foreign_keys=[player2_id])
    winner      = db.relationship('User', foreign_keys=[winner_id])

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

# --- ELO helper ---
def update_elo(winner: User, loser: User):
    exp_w = 1 / (1 + 10 ** ((loser.elo - winner.elo) / 400))
    exp_l = 1 - exp_w
    winner.elo = max(0, round(winner.elo + ELO_K * (1 - exp_w)))
    loser.elo  = max(0, round(loser.elo  + ELO_K * (0 - exp_l)))

# --- Routes ---
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

@app.route("/profile")
@login_required
def profile():
    if session.get('is_guest'):
        flash("Guests do not have profiles."); return redirect(url_for('home'))
    u = current_user
    wins    = Match.query.filter_by(winner_id=u.id).count()
    draws   = Match.query.filter(or_(Match.player1_id==u.id, Match.player2_id==u.id), Match.is_draw==True).count()
    total   = Match.query.filter(or_(Match.player1_id==u.id, Match.player2_id==u.id)).count()
    losses  = total - wins - draws
    matches = Match.query.filter(or_(Match.player1_id==u.id, Match.player2_id==u.id)).order_by(Match.timestamp.desc()).all()
    return render_template("profile.html", user=u, matches=matches,
                           wins=wins, losses=losses, draws=draws)

# --- Helpers ---
def new_room(): return ''.join(random.choices(string.digits, k=5))
def get_active_games(): return guest_games if session.get('is_guest') else games

def make_game_data(player_accounts=None, players=None, spectators=None, chat_history=None, is_ai=False):
    return {
        "game": UltimateTicTacToe(),
        "player_accounts": player_accounts or {},
        "players": players or {},
        "spectators": spectators or {},
        "ready": set(),
        "rematchReady": set(),
        "chat_history": chat_history or [],
        "rematch_declined": False,
        "move_deadline": None,
        "is_ai": is_ai,
    }

def full_state(game_data):
    """Return game state augmented with server-side timer info."""
    s = game_data["game"].state()
    s["moveDeadline"] = game_data.get("move_deadline")
    s["moveTimeout"] = MOVE_TIMEOUT
    return s

def reset_timer(game_data):
    if game_data["game"].started and not game_data["game"].game_winner:
        game_data["move_deadline"] = time.time() + MOVE_TIMEOUT
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
        emit('gameStatus', p, room=sid)

def emit_spectator_list(room):
    gd = get_active_games().get(room)
    if gd:
        emit('spectatorList', {'spectators': [s['username'] for s in gd['spectators'].values()]}, room=room)

def record_match(game_data, winner_symbol):
    for uid in game_data["player_accounts"].values(): active_players.discard(uid)
    if session.get('is_guest') or len(game_data["player_accounts"]) < 2 or game_data.get("is_ai"): return
    p1_id = game_data["player_accounts"]["X"]
    p2_id = game_data["player_accounts"]["O"]
    if winner_symbol == "D":
        match = Match(player1_id=p1_id, player2_id=p2_id, winner_id=None, is_draw=True)
        # Reset streaks on draw? No — keep them.
    else:
        winner_id = game_data["player_accounts"][winner_symbol]
        loser_id  = p1_id if winner_id == p2_id else p2_id
        match = Match(player1_id=p1_id, player2_id=p2_id, winner_id=winner_id, is_draw=False)
        w = User.query.get(winner_id)
        l = User.query.get(loser_id)
        if w and l:
            update_elo(w, l)
            w.win_streak  = (w.win_streak or 0) + 1
            w.best_streak = max(w.best_streak or 0, w.win_streak)
            l.win_streak  = 0
    db.session.add(match); db.session.commit()

# --- SocketIO Events ---
@socketio.on("create")
@login_required
def create(data=None):
    if current_user.get_id() in active_players:
        emit('already_in_game', {'error': 'You are already in a game.'}); return
    active_games = get_active_games()
    room = new_room()
    is_ai = bool(data and data.get('ai'))
    active_games[room] = make_game_data(is_ai=is_ai)
    emit("created", room)

@socketio.on("join")
@login_required
def join(data):
    active_games = get_active_games()
    room = data["room"]; sid = request.sid
    game_data = active_games.get(room)
    if not game_data: emit("invalid"); return
    user_id = current_user.get_id()
    is_locked = user_id in game_data.get("player_accounts", {}).values()
    if not is_locked and user_id in active_players:
        emit('already_in_game', {'error': 'You are already in another game.'}); return
    join_room(room)
    players = game_data["players"]
    pa = game_data["player_accounts"]
    if is_locked:
        symbol = next(s for s, uid in pa.items() if uid == user_id)
        old_sid = next((s for s, p in players.items() if p.get('user_id') == user_id), None)
        if old_sid: del players[old_sid]
        players[sid] = {"symbol": symbol, "user_id": user_id, "username": current_user.username}
        emit("assign", symbol)
    elif len(pa) < 2:
        symbol = "X" if "X" not in pa else "O"
        pa[symbol] = user_id
        players[sid] = {"symbol": symbol, "user_id": user_id, "username": current_user.username}
        active_players.add(user_id)
        emit("assign", symbol)
        # If AI mode and this is the first human (X), add the AI as O
        if game_data.get("is_ai") and symbol == "X":
            pa["O"] = "AI"
            players["AI"] = {"symbol": "O", "user_id": "AI", "username": "🤖 AI"}
    else:
        game_data["spectators"][sid] = {"user_id": user_id, "username": current_user.username}
        emit("spectator")
    if game_data.get("chat_history"):
        emit('chatHistory', {'history': game_data["chat_history"]})
    emit("state", full_state(game_data), room=room)
    emit_game_status(room)
    emit_spectator_list(room)

@socketio.on("ready")
@login_required
def ready(data):
    active_games = get_active_games(); room = data["room"]; sid = request.sid
    game_data = active_games.get(room)
    if not game_data or sid not in game_data["players"]: return
    game_data["ready"].add(sid)
    # For AI games, auto-ready when the human is ready
    if game_data.get("is_ai"): game_data["ready"].add("AI")
    if len(game_data["player_accounts"]) == 2 and len(game_data["ready"]) >= 2:
        game_data["game"].started = True
        reset_timer(game_data)
        emit("state", full_state(game_data), room=room)
    emit_game_status(room)

@socketio.on("move")
@login_required
def move(data):
    game_data = get_active_games().get(data["room"])
    if not game_data: return
    # Validate timer
    deadline = game_data.get("move_deadline")
    if deadline and time.time() > deadline + 2:  # 2s grace
        return
    g = game_data["game"]
    if g.make_move(data["board"], data["cell"]):
        if g.game_winner:
            game_data["move_deadline"] = None
            record_match(game_data, g.game_winner)
        else:
            reset_timer(game_data)
            # AI move
            if game_data.get("is_ai") and not g.game_winner:
                ai_b, ai_c = get_ai_move(g)
                g.make_move(ai_b, ai_c)
                if g.game_winner:
                    game_data["move_deadline"] = None
                    record_match(game_data, g.game_winner)
                else:
                    reset_timer(game_data)
        emit("state", full_state(game_data), room=data["room"])
        emit_game_status(data["room"])

@socketio.on("timeout")
@login_required
def timeout(data):
    """Client reports that the move timer has expired."""
    room = data.get("room")
    game_data = get_active_games().get(room)
    if not game_data: return
    g = game_data["game"]
    if g.game_winner or not g.started: return
    deadline = game_data.get("move_deadline")
    if deadline and time.time() >= deadline - 1:  # 1s tolerance
        # The player whose turn it is forfeits
        timed_out_player = g.current_player
        g.resign(timed_out_player)
        game_data["move_deadline"] = None
        record_match(game_data, g.game_winner)
        emit("state", full_state(game_data), room=room)
        emit_game_status(room)

@socketio.on("rematch")
@login_required
def rematch(data):
    active_games = get_active_games(); room = data["room"]; sid = request.sid
    game_data = active_games.get(room)
    if not game_data or sid not in game_data["players"] or game_data.get('rematch_declined'): return
    game_data["rematchReady"].add(sid)
    if game_data.get("is_ai"): game_data["rematchReady"].add("AI")
    if len(game_data["rematchReady"]) >= 2:
        pa = game_data["player_accounts"]
        active_games[room] = make_game_data(
            player_accounts=pa, players=game_data["players"],
            spectators=game_data["spectators"], chat_history=game_data.get("chat_history", []),
            is_ai=game_data.get("is_ai", False)
        )
        emit("rematchAgreed", room=room)
        emit("state", full_state(active_games[room]), room=room)
    emit_game_status(room)

@socketio.on("leave_post_game")
@login_required
def leave_post_game(data):
    active_games = get_active_games(); room = data["room"]
    game_data = active_games.get(room)
    if not game_data: return
    game_data['rematch_declined'] = True
    emit_game_status(room)

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
@login_required
def chat(data):
    room = data['room']; message = data['message']
    game_data = get_active_games().get(room)
    if not game_data: return
    is_spectator = request.sid in game_data['spectators']
    player_symbol = None
    if not is_spectator:
        pd = game_data['players'].get(request.sid)
        if pd: player_symbol = pd['symbol']
    entry = {'username': current_user.username, 'message': message,
             'is_spectator': is_spectator, 'symbol': player_symbol}
    game_data["chat_history"].append(entry)
    emit('chatMessage', entry, room=room)

@socketio.on("resign")
@login_required
def resign(data):
    game_data = get_active_games().get(data["room"])
    if not game_data: return
    g = game_data["game"]
    loser = data["symbol"]
    winner = "X" if loser == "O" else "O"
    g.resign(loser)
    game_data["move_deadline"] = None
    record_match(game_data, winner)
    emit("state", full_state(game_data), room=data["room"])
    emit_game_status(data["room"])

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    socketio.run(app, debug=True)
