const socket = io();

const createBtn   = document.getElementById("create");
const createAiBtn = document.getElementById("create-ai");
const joinBtn     = document.getElementById("join");
const roomInput   = document.getElementById("room");

createBtn.onclick   = () => socket.emit("create", {});
createAiBtn.onclick = () => socket.emit("create", { ai: true });

socket.on("created", room => { window.location.href = `/game/${room}`; });

function joinGame() {
    const room = roomInput.value.trim();
    if (room) window.location.href = `/game/${room}`;
}
joinBtn.onclick = joinGame;
roomInput.onkeydown = e => { if (e.key === 'Enter') joinGame(); };

socket.on('already_in_game', data => {
    let err = document.querySelector('.error');
    if (!err) {
        err = document.createElement('div');
        err.className = 'error';
        const sub = document.querySelector('.subtitle');
        sub ? sub.insertAdjacentElement('afterend', err) : document.querySelector('.home-card').prepend(err);
    }
    err.textContent = data.error;
    err.style.display = 'block';
});
