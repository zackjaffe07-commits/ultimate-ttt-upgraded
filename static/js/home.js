const socket = io();

// ── Debug: confirm socket connects ───────────────────────────────────────────
socket.on('connect', () => {
    console.log('[socket] Connected. SID:', socket.id);
});
socket.on('connect_error', (err) => {
    console.error('[socket] Connection error:', err.message);
});
socket.on('disconnect', (reason) => {
    console.warn('[socket] Disconnected:', reason);
});

// ── Safe helper: attach click handler only if element exists ──────────────────
function onClick(id, fn) {
    const el = document.getElementById(id);
    if (el) {
        el.addEventListener('click', fn);
    } else {
        console.warn('[home.js] Element not found:', id);
    }
}

// ── Button wiring ─────────────────────────────────────────────────────────────
onClick('create-ranked', () => {
    console.log('[socket] Emitting: create { ranked: true }');
    socket.emit('create', { ranked: true });
});

onClick('create-casual', () => {
    console.log('[socket] Emitting: create { ranked: false }');
    socket.emit('create', { ranked: false });
});

// Single AI button — difficulty is set via ⚙️ Settings inside the game room
onClick('create-ai', () => {
    console.log('[socket] Emitting: create { ai: true, difficulty: medium }');
    socket.emit('create', { ai: true, difficulty: 'medium' });
});

onClick('join', joinGame);

const roomInput = document.getElementById('room');
if (roomInput) {
    roomInput.addEventListener('keydown', e => {
        if (e.key === 'Enter') joinGame();
    });
}

// ── Join game ─────────────────────────────────────────────────────────────────
function joinGame() {
    const code = roomInput ? roomInput.value.trim() : '';
    if (code) {
        console.log('[socket] Joining room:', code);
        window.location.href = `/game/${code}`;
    }
}

// ── Server responses ──────────────────────────────────────────────────────────
socket.on('created', room => {
    console.log('[socket] Room created:', room, '— redirecting to /game/' + room);
    window.location.href = `/game/${room}`;
});

socket.on('already_in_game', data => {
    console.warn('[socket] Already in game:', data.error);
    let err = document.querySelector('.error');
    if (!err) {
        err = document.createElement('div');
        err.className = 'error';
        const sub = document.querySelector('.subtitle');
        sub
            ? sub.insertAdjacentElement('afterend', err)
            : document.querySelector('.home-card').prepend(err);
    }
    err.textContent = data.error;
    err.style.display = 'block';
});
