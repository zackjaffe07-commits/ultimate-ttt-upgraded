const socket = io();

// ── State ────────────────────────────────────────────────────────────────────
let mySymbol        = null;
let myUsername      = null;
let isSpectator     = false;
let gameEnded       = false;
let lastWinners     = Array(9).fill(null);
let gameState       = {};
let soundEnabled    = true;
let timerInterval   = null;
let lastMoveCount   = 0;
let resignConfirming = false;
let resignTimer     = null;

// History navigation
let historyIndex    = null;
let fullMoveHistory = [];

// ── DOM ───────────────────────────────────────────────────────────────────────
const boardDiv             = document.getElementById("board");
const statusEl             = document.getElementById("status");
const playerText           = document.getElementById("player");
const actionBtn            = document.getElementById("action");
const postGameDiv          = document.getElementById("post-game-actions");
const rematchBtn           = document.getElementById('rematch-btn');
const homeBtn              = document.getElementById('home-btn');
const spectatorList        = document.getElementById("spectator-list");
const chatMessages         = document.getElementById("chat-messages");
const chatInput            = document.getElementById("chat-input");
const sendChatBtn          = document.getElementById("send-chat-btn");
const muteOpponentCheck    = document.getElementById("mute-opponent");
const muteSpectatorsCheck  = document.getElementById("mute-spectators");
const victoryModal         = document.getElementById("victory-modal");
const victoryText          = document.getElementById("victory-text");
const victorySubtext       = document.getElementById("victory-subtext");
const playerXDiv           = document.getElementById("player-X");
const playerODiv           = document.getElementById("player-O");
const timerDisplay         = document.getElementById("timer-display");
const soundToggle          = document.getElementById("sound-toggle");
const moveHistoryList      = document.getElementById("move-history-list");
const confettiCanvas       = document.getElementById("confetti-canvas");
const leaveBtn             = document.getElementById("leave-btn");
const settingsBtn          = document.getElementById("settings-btn");
const settingsModal        = document.getElementById("settings-modal");
const settingsApplyBtn     = document.getElementById("settings-apply-btn");
const settingsCancelBtn    = document.getElementById("settings-cancel-btn");
const timerSetting         = document.getElementById("timer-setting");
const timerSettingDisplay  = document.getElementById("timer-setting-display");
const timerInfinity        = document.getElementById("timer-infinity");
const aiDiffRow            = document.querySelector(".ai-difficulty-row");
const historyViewingBanner = document.getElementById("history-viewing-banner");
const historyViewingLabel  = document.getElementById("history-viewing-label");
const historyReturnBtn     = document.getElementById("history-return-btn");
const historyNavHint       = document.getElementById("history-nav-hint");
const rulesBtn             = document.getElementById("rules-btn");
const rulesModal           = document.getElementById("rules-modal");
const rulesCloseBtn        = document.getElementById("rules-close-btn");
const rankedBadge          = document.getElementById("ranked-badge");

// ── Sounds ────────────────────────────────────────────────────────────────────
const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
function beep(freq, dur, type = 'sine', vol = 0.3) {
    if (!soundEnabled) return;
    try {
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.connect(gain); gain.connect(audioCtx.destination);
        osc.type = type; osc.frequency.value = freq;
        gain.gain.setValueAtTime(vol, audioCtx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + dur);
        osc.start(); osc.stop(audioCtx.currentTime + dur);
    } catch (e) {}
}
const sounds = {
    place:    () => beep(440, 0.08, 'square', 0.2),
    win:      () => { beep(523, 0.1); setTimeout(() => beep(659, 0.1), 80); setTimeout(() => beep(784, 0.2), 160); },
    gameWin:  () => { [523, 659, 784, 1047].forEach((f, i) => setTimeout(() => beep(f, 0.15, 'sine', 0.35), i * 90)); },
    gameLose: () => { beep(330, 0.12, 'sawtooth', 0.2); setTimeout(() => beep(277, 0.25, 'sawtooth', 0.15), 130); },
    chat:     () => beep(880, 0.06, 'sine', 0.15),
    urgent:   () => beep(880, 0.07, 'square', 0.25),
};
function playSound(name) { if (sounds[name]) sounds[name](); }

soundToggle.onclick = () => {
    soundEnabled = !soundEnabled;
    soundToggle.textContent = soundEnabled ? '🔊' : '🔇';
};

// ── Timer ─────────────────────────────────────────────────────────────────────
function startTimer(deadline, serverNow) {
    clearInterval(timerInterval);
    if (!deadline) {
        // Check for infinity mode: moveTimeout === 0 means no timer
        if (gameState.moveTimeout === 0) {
            timerDisplay.className = 'infinity-timer';
            timerDisplay.textContent = '⏱ ∞';
        } else {
            timerDisplay.className = 'hidden';
        }
        return;
    }
    timerDisplay.className = '';
    const serverRemaining = deadline - (serverNow || deadline);
    const clientEndTime   = Date.now() + serverRemaining * 1000;
    function tick() {
        const rem = Math.max(0, Math.ceil((clientEndTime - Date.now()) / 1000));
        timerDisplay.textContent = `⏱ ${rem}s`;
        if (rem <= 8) {
            timerDisplay.classList.add('urgent');
            if (rem > 0 && rem <= 5) playSound('urgent');
        } else {
            timerDisplay.classList.remove('urgent');
        }
        if (rem === 0) {
            clearInterval(timerInterval);
            socket.emit('timeout', { room: ROOM });
        }
    }
    tick();
    timerInterval = setInterval(tick, 1000);
}

// ── Confetti ──────────────────────────────────────────────────────────────────
function launchConfetti() {
    const ctx = confettiCanvas.getContext('2d');
    confettiCanvas.width  = window.innerWidth;
    confettiCanvas.height = window.innerHeight;
    const particles = Array.from({ length: 120 }, () => ({
        x: Math.random() * window.innerWidth,
        y: Math.random() * -window.innerHeight * 0.5,
        vx: (Math.random() - 0.5) * 4,
        vy: Math.random() * 4 + 2,
        color: ['#e74c3c','#3498db','#f39c12','#2ecc71','#9b59b6','#ffd700'][Math.floor(Math.random() * 6)],
        w: Math.random() * 10 + 5,
        h: Math.random() * 6 + 3,
        rot: Math.random() * Math.PI * 2,
        spin: (Math.random() - 0.5) * 0.2,
    }));
    let frame = 0;
    function animate() {
        ctx.clearRect(0, 0, confettiCanvas.width, confettiCanvas.height);
        particles.forEach(p => {
            p.x += p.vx; p.y += p.vy; p.rot += p.spin; p.vy += 0.05;
            ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot);
            ctx.fillStyle = p.color;
            ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
            ctx.restore();
        });
        if (++frame < 180) requestAnimationFrame(animate);
        else ctx.clearRect(0, 0, confettiCanvas.width, confettiCanvas.height);
    }
    animate();
}

// ── Move History ─────────────────────────────────────────────────────────────
function updateMoveHistory(history) {
    fullMoveHistory = history;
    if (!moveHistoryList) return;
    moveHistoryList.innerHTML = '';
    if (history.length === 0) {
        moveHistoryList.innerHTML = '<li style="color:#aaa; font-size:0.85rem;">No moves yet.</li>';
        historyViewingBanner.style.display = 'none';
        historyNavHint.style.display = 'none';
        return;
    }
    historyNavHint.style.display = 'inline';
    history.forEach((m, i) => {
        const li  = document.createElement('li');
        const cls = m.player === 'X' ? 'hist-x' : 'hist-o';
        li.innerHTML = `<span class="hist-num">${i + 1}.</span> <span class="${cls}">${m.player}</span> → B${m.board + 1} C${m.cell + 1}`;
        li.dataset.index = i;
        li.classList.add('history-item');
        if (historyIndex === i) li.classList.add('history-selected');
        li.onclick = () => selectHistory(i);
        moveHistoryList.insertBefore(li, moveHistoryList.firstChild);
    });
    if (historyIndex !== null) {
        historyViewingBanner.style.display = 'flex';
        historyViewingLabel.textContent = `Viewing move ${historyIndex + 1} of ${history.length}`;
    } else {
        historyViewingBanner.style.display = 'none';
    }
}

function selectHistory(index) {
    if (index < 0 || index >= fullMoveHistory.length) return;
    historyIndex = index;
    const entry = fullMoveHistory[index];
    if (entry && entry.snapshot) drawSnapshot(entry.snapshot);
    updateMoveHistoryHighlight();
    historyViewingBanner.style.display = 'flex';
    historyViewingLabel.textContent = `Viewing move ${historyIndex + 1} of ${fullMoveHistory.length}`;
}

function exitHistory() {
    historyIndex = null;
    historyViewingBanner.style.display = 'none';
    updateMoveHistoryHighlight();
    if (gameState && gameState.boards) {
        drawBoardGrid(gameState, false, false);
        lastWinners = [...(gameState.winners || Array(9).fill(null))];
    }
}

function updateMoveHistoryHighlight() {
    [...(moveHistoryList?.querySelectorAll('li.history-item') || [])].forEach(li => {
        li.classList.toggle('history-selected', parseInt(li.dataset.index) === historyIndex);
    });
}

document.addEventListener('keydown', e => {
    if (e.target === chatInput) return;
    if (fullMoveHistory.length === 0) return;
    if (e.key === 'ArrowLeft') {
        e.preventDefault();
        if (historyIndex === null) selectHistory(fullMoveHistory.length - 1);
        else if (historyIndex > 0) selectHistory(historyIndex - 1);
    } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        if (historyIndex !== null) {
            if (historyIndex < fullMoveHistory.length - 1) selectHistory(historyIndex + 1);
            else exitHistory();
        }
    } else if (e.key === 'Escape') {
        if (historyIndex !== null) exitHistory();
        settingsModal.style.display = 'none';
        rulesModal.style.display    = 'none';
    }
});

historyReturnBtn.onclick = exitHistory;

// ── Rules Modal ───────────────────────────────────────────────────────────────
rulesBtn.onclick     = () => { rulesModal.style.display = 'flex'; };
rulesCloseBtn.onclick = () => { rulesModal.style.display = 'none'; };
rulesModal.onclick   = e => { if (e.target === rulesModal) rulesModal.style.display = 'none'; };

// ── Settings Modal ────────────────────────────────────────────────────────────
settingsBtn.onclick     = () => { settingsModal.style.display = 'flex'; };
settingsCancelBtn.onclick = () => { settingsModal.style.display = 'none'; };
settingsModal.onclick   = e => { if (e.target === settingsModal) settingsModal.style.display = 'none'; };

timerInfinity.onchange = () => {
    timerSetting.disabled = timerInfinity.checked;
    timerSettingDisplay.textContent = timerInfinity.checked ? '∞' : timerSetting.value + 's';
};
timerSetting.oninput = () => {
    if (!timerInfinity.checked) timerSettingDisplay.textContent = timerSetting.value + 's';
};

settingsApplyBtn.onclick = () => {
    const timeout   = timerInfinity.checked ? 0 : parseInt(timerSetting.value);
    const diffEl    = document.querySelector('input[name="ai-diff"]:checked');
    const diff      = diffEl ? diffEl.value : 'medium';
    const orderEl   = document.querySelector('input[name="ai-order"]:checked');
    const order     = orderEl ? orderEl.value : 'first';
    socket.emit('update_settings', { room: ROOM, move_timeout: timeout, ai_difficulty: diff, ai_player_order: order });
    settingsModal.style.display = 'none';
};

socket.on('settingsUpdated', data => {
    if (data.move_timeout === 0) {
        timerInfinity.checked = true;
        timerSetting.disabled = true;
        timerSettingDisplay.textContent = '∞';
    } else {
        timerInfinity.checked = false;
        timerSetting.disabled = false;
        timerSetting.value = data.move_timeout;
        timerSettingDisplay.textContent = data.move_timeout + 's';
    }
    const diffEl = document.querySelector(`input[name="ai-diff"][value="${data.ai_difficulty}"]`);
    if (diffEl) diffEl.checked = true;
    const orderEl = document.querySelector(`input[name="ai-order"][value="${data.ai_player_order || 'first'}"]`);
    if (orderEl) orderEl.checked = true;
});

// ── Socket Listeners ──────────────────────────────────────────────────────────
socket.on('connect', () => { socket.emit("join", { room: ROOM }); });

socket.on("assign", s => {
    mySymbol = s;
    playerText.textContent = `You are ${s}`;
});

socket.on("spectator", () => {
    isSpectator = true;
    playerText.textContent = "You are a spectator";
    if (actionBtn)   actionBtn.style.display   = "none";
    if (leaveBtn)    leaveBtn.style.display    = "none";
    if (settingsBtn) settingsBtn.style.display = "none";
});

socket.on("state", newState => { gameState = newState; draw(newState); });

socket.on("gameStatus", data => {
    statusEl.textContent = data.text;
    updatePlayerInfo(data.players);
    if (data.button_action) {
        actionBtn.style.display = 'inline-block';
        postGameDiv.style.display = 'none';
        // Reset resign confirm state whenever action changes
        resetResignConfirm();
        switch (data.button_action) {
            case 'start':
                actionBtn.textContent = 'Start'; actionBtn.disabled = false; actionBtn.className = 'button primary small';
                leaveBtn.style.display   = !isSpectator ? 'inline-block' : 'none';
                settingsBtn.style.display = mySymbol === 'X' && !isSpectator ? 'inline-block' : 'none';
                break;
            case 'waiting':
                actionBtn.textContent = 'Waiting...'; actionBtn.disabled = true; actionBtn.className = 'button primary small';
                leaveBtn.style.display   = !isSpectator ? 'inline-block' : 'none';
                settingsBtn.style.display = mySymbol === 'X' && !isSpectator ? 'inline-block' : 'none';
                break;
            case 'resign':
                actionBtn.textContent = 'Resign'; actionBtn.disabled = false; actionBtn.className = 'button secondary small';
                leaveBtn.style.display   = 'none';
                settingsBtn.style.display = 'none';
                break;
            case 'hidden':
                actionBtn.style.display = 'none';
                leaveBtn.style.display  = !isSpectator ? 'inline-block' : 'none';
                settingsBtn.style.display = mySymbol === 'X' && !isSpectator ? 'inline-block' : 'none';
                break;
        }
    }
    if (data.button_rematch) {
        actionBtn.style.display  = 'none';
        leaveBtn.style.display   = 'none';
        settingsBtn.style.display = 'none';
        postGameDiv.style.display = 'flex';
        switch (data.button_rematch) {
            case 'rematch':  rematchBtn.textContent = 'Rematch';                    rematchBtn.disabled = false; break;
            case 'waiting':  rematchBtn.textContent = 'Waiting...';                 rematchBtn.disabled = true;  break;
            case 'prompted': rematchBtn.textContent = 'Opponent wants a rematch!';  rematchBtn.disabled = false; break;
            case 'declined': rematchBtn.textContent = 'Opponent Left';              rematchBtn.disabled = true;  break;
        }
    }
});

socket.on("rematchAgreed", () => {
    gameEnded       = false;
    lastWinners     = Array(9).fill(null);
    lastMoveCount   = 0;
    historyIndex    = null;
    fullMoveHistory = [];
    victoryModal.style.display = "none";
    clearInterval(timerInterval);
    timerDisplay.className = 'hidden';
    resetResignConfirm();
});

socket.on("spectatorList", data => {
    spectatorList.innerHTML = "";
    if (data.spectators.length > 0) {
        data.spectators.forEach(name => {
            const li = document.createElement("li");
            li.textContent = name;
            spectatorList.appendChild(li);
        });
    } else {
        spectatorList.innerHTML = "<li>No spectators yet.</li>";
    }
});

// ── Chat ─────────────────────────────────────────────────────────────────────
function renderMessage(data) {
    myUsername = document.body.dataset.username;
    const isMyMsg   = data.username === myUsername;
    const isSpecMsg = data.is_spectator;
    const isOppMsg  = !isMyMsg && !isSpecMsg;
    if (isOppMsg && muteOpponentCheck.checked) return;
    if (isSpecMsg && muteSpectatorsCheck.checked) return;
    if (!isMyMsg) playSound('chat');
    const msgDiv = document.createElement("div");
    msgDiv.classList.add("chat-message");
    if (data.symbol) {
        const sym = document.createElement("span");
        sym.className   = `chat-symbol ${data.symbol}`;
        sym.textContent = data.symbol;
        msgDiv.appendChild(sym);
    }
    const uSpan = document.createElement("span");
    uSpan.className   = "username";
    uSpan.textContent = data.username;
    msgDiv.appendChild(uSpan);
    if (data.is_spectator) {
        const tag = document.createElement("span");
        tag.className   = "spectator-tag";
        tag.textContent = "Spectator";
        msgDiv.appendChild(tag);
    }
    msgDiv.append(`: ${data.message}`);
    chatMessages.appendChild(msgDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}
socket.on("chatMessage", renderMessage);
socket.on("chatHistory", data => { chatMessages.innerHTML = ''; data.history.forEach(renderMessage); });

function sendChatMessage() {
    const message = chatInput.value;
    if (message.trim()) {
        socket.emit('chat', { room: ROOM, message });
        chatInput.value = '';
    }
}
sendChatBtn.onclick = sendChatMessage;
chatInput.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); sendChatMessage(); } };

// ── Resign Confirmation ───────────────────────────────────────────────────────
function resetResignConfirm() {
    resignConfirming = false;
    clearTimeout(resignTimer);
    // Only reset button text if it's currently in confirm mode
    if (actionBtn.textContent === 'Sure?' || actionBtn.textContent === 'Resign') {
        actionBtn.textContent = 'Resign';
        actionBtn.className   = 'button secondary small';
        actionBtn.disabled    = false;
    }
}

actionBtn.onclick = () => {
    if (isSpectator) return;
    if (actionBtn.textContent === 'Start') {
        socket.emit("ready", { room: ROOM });
        return;
    }
    if (actionBtn.textContent === 'Resign' || actionBtn.textContent === 'Sure?') {
        if (!resignConfirming) {
            // First click — ask for confirmation
            resignConfirming = true;
            actionBtn.textContent = 'Sure?';
            actionBtn.className   = 'button danger small';
            resignTimer = setTimeout(() => {
                resignConfirming = false;
                actionBtn.textContent = 'Resign';
                actionBtn.className   = 'button secondary small';
            }, 3000);
        } else {
            // Second click — confirmed
            clearTimeout(resignTimer);
            resignConfirming = false;
            socket.emit("resign", { room: ROOM, symbol: mySymbol });
        }
    }
};

rematchBtn.onclick = () => socket.emit("rematch", { room: ROOM });
homeBtn.onclick = () => {
    if (gameEnded) socket.emit("leave_post_game", { room: ROOM });
    window.location.href = "/home";
};
leaveBtn.onclick = () => {
    socket.emit('leave_pre_game', { room: ROOM });
    window.location.href = '/home';
};

// ── Draw ──────────────────────────────────────────────────────────────────────
function updatePlayerInfo(players) {
    myUsername = document.body.dataset.username;
    const renderPlayer = (div, symbol, username) => {
        const isActive = gameState.player === symbol && gameState.started && !gameState.gameWinner;
        const youTag   = (username === myUsername && !isSpectator) ? '<div class="you-tag">You</div>' : '';
        div.className  = `player-info${isActive ? ' active-player' : ''}`;
        div.innerHTML  = `
            <div class="symbol ${symbol}">${symbol}</div>
            <div class="username">${username || 'Waiting...'}</div>
            ${youTag}
        `;
    };
    renderPlayer(playerXDiv, 'X', players.X);
    renderPlayer(playerODiv, 'O', players.O);
}

function draw(state) {
    // Ranked badge
    if (rankedBadge) rankedBadge.style.display = state.isRanked ? 'inline' : 'none';

    // Show AI difficulty row in settings modal for AI games
    if (aiDiffRow) aiDiffRow.style.display = state.isAI ? 'block' : 'none';

    // Timer
    if (state.started && !state.gameWinner) {
        if (state.moveDeadline) {
            startTimer(state.moveDeadline, state.serverNow);
        } else if (state.moveTimeout === 0) {
            clearInterval(timerInterval);
            timerDisplay.className = 'infinity-timer';
            timerDisplay.textContent = '⏱ ∞';
        } else {
            clearInterval(timerInterval);
            timerDisplay.className = 'hidden';
        }
    } else {
        clearInterval(timerInterval);
        if (timerDisplay) timerDisplay.className = 'hidden';
    }

    if (state.moveHistory) updateMoveHistory(state.moveHistory);

    if (state.gameWinner && !gameEnded) {
        if (!isSpectator) showVictoryAnimation(state.gameWinner);
    }
    gameEnded = !!state.gameWinner;

    if (historyIndex !== null) return; // don't overwrite history view

    const newMoveCount = state.moveHistory ? state.moveHistory.length : 0;
    const justMoved    = newMoveCount > lastMoveCount;
    lastMoveCount      = newMoveCount;

    drawBoardGrid(state, justMoved, false);
    lastWinners = [...state.winners];
}

function drawSnapshot(snapshot) {
    drawBoardGrid({
        boards:        snapshot.boards,
        winners:       snapshot.winners,
        boardWinLines: snapshot.boardWinLines,
        forced:        null,
        lastMove:      snapshot.lastMove,
        gameWinner:    snapshot.gameWinner || null,
        gameWinLine:   snapshot.gameWinLine || null,
        player:        snapshot.player,
        started:       true,
    }, false, true);
}

function drawBoardGrid(state, justMoved, isHistoryView) {
    boardDiv.innerHTML = "";
    const isMyTurn = !isHistoryView && !isSpectator && mySymbol === state.player && !state.gameWinner && state.started;
    const validBoards = new Set();
    if (isMyTurn) {
        if (state.forced !== null && state.forced !== undefined) {
            validBoards.add(state.forced);
        } else {
            for (let i = 0; i < 9; i++) {
                if (!state.winners[i]) validBoards.add(i);
            }
        }
    }
    for (let b = 0; b < 9; b++) {
        const mini    = document.createElement("div");
        mini.className = "mini-board";
        const boardWon  = state.winners[b];
        const isMetaWin = state.gameWinLine && state.gameWinLine.includes(b);
        if (boardWon && boardWon !== "D") {
            if (!isHistoryView) {
                if (lastWinners[b] !== boardWon) { playSound('win'); mini.classList.add('win-board'); }
            }
            mini.classList.add(`won-${boardWon}`);
            const overlay = document.createElement("span");
            overlay.className   = `overlay ${boardWon}`;
            overlay.textContent = boardWon;
            mini.appendChild(overlay);
        }
        if (isMetaWin) mini.classList.add('game-win');
        if (!isHistoryView && state.forced === b && !boardWon) mini.classList.add("forced");

        for (let c = 0; c < 9; c++) {
            const cell   = document.createElement("div");
            const symbol = state.boards[b][c];
            cell.className = "cell";
            if (symbol) {
                cell.classList.add(symbol);
                cell.textContent = symbol;
                if (!isHistoryView && justMoved && state.lastMove && state.lastMove[0] === b && state.lastMove[1] === c)
                    cell.classList.add('placed');
            }
            if (state.lastMove && state.lastMove[0] === b && state.lastMove[1] === c)
                cell.classList.add('last-move');
            if (boardWon && boardWon !== "D" && state.boardWinLines && state.boardWinLines[b])
                if (state.boardWinLines[b].includes(c)) cell.classList.add('win-cell');

            // No grey-out: just block pointer on invalid boards
            if (isMyTurn && (!validBoards.has(b) || boardWon || symbol))
                cell.classList.add('no-click');

            cell.onclick = () => {
                if (!isSpectator && mySymbol === state.player && !state.gameWinner && state.started) {
                    playSound('place');
                    socket.emit("move", { room: ROOM, board: b, cell: c });
                }
            };
            mini.appendChild(cell);
        }
        boardDiv.appendChild(mini);
    }
}

function showVictoryAnimation(winner) {
    myUsername = document.body.dataset.username;
    if (winner === "D") {
        victoryText.textContent    = "Draw!";
        victorySubtext.textContent = "A hard-fought battle.";
        playSound('win');
    } else if (winner === mySymbol) {
        victoryText.textContent    = "You Won! 🎉";
        victorySubtext.textContent = "Outstanding play!";
        playSound('gameWin');
        launchConfetti();
    } else {
        victoryText.textContent    = "You Lost";
        victorySubtext.textContent = "Better luck next time!";
        playSound('gameLose');
    }
    victoryModal.style.display = "flex";
    setTimeout(() => { victoryModal.style.display = "none"; }, 4000);
}
victoryModal.onclick = () => { victoryModal.style.display = "none"; };
