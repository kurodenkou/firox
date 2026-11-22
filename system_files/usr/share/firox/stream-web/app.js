// Firox Stream in a web browser.
//
// The page served by the computer being streamed (firox-stream, the host).
// A WebSocket back to it carries what the apps' connection carries, as JSON
// ({k: kind, d: payload}): pairing with a PIN, joining or taking over, and
// this device's keyboard, mouse, touches and game controllers. The picture
// and sound arrive over WebRTC, which the host offers once it's streaming.
"use strict";

const $ = (id) => document.getElementById(id);
const TOKEN = "firox-stream-token", NAME = "firox-stream-name", QUALITY = "firox-stream-quality";

let ws = null, pc = null, info = {}, streaming = false, pairing = false, hostName = "", qualityKnown = false;

function store(key, value) {
    try {
        if (value === undefined) return localStorage.getItem(key);
        if (value === null) localStorage.removeItem(key);
        else localStorage.setItem(key, value);
    } catch (e) { /* private window: pair each time */ }
    return null;
}

function say(text, error) {
    const m = $("message");
    m.textContent = text || "";
    m.classList.toggle("error", !!error);
}

let toastTimer = 0;
function toast(text) {
    const t = $("toast");
    t.textContent = text;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), 3500);
}

function send(k, d) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ k, d: d === undefined ? {} : d }));
}

function defaultName() {
    const ua = navigator.userAgent;
    const what = /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iPhone or iPad"
        : /CrOS/.test(ua) ? "Chromebook" : /Mac/.test(ua) ? "Mac" : /Windows/.test(ua) ? "Windows"
        : /Linux/.test(ua) ? "Linux" : "this device";
    return `Browser on ${what}`;
}

// The name the host checks the PIN's proof against: it keeps the first 60
// characters (code points, as Python counts them)
function cleanName(text) {
    return Array.from(text.trim() || defaultName()).slice(0, 60).join("");
}

function hex(buf) {
    return Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, "0")).join("");
}

// What the apps send to show the PIN was typed: HMAC-SHA256 keyed by the PIN
// over the host's certificate fingerprint and this device's name
async function proof(pin, fingerprint, name) {
    const enc = new TextEncoder();
    const key = await crypto.subtle.importKey("raw", enc.encode(pin), { name: "HMAC", hash: "SHA-256" },
                                              false, ["sign"]);
    return hex(await crypto.subtle.sign("HMAC", key, enc.encode(`firox-stream|${fingerprint}|${name}`)));
}

// ----------------------------------------------------------- connecting --

function show(which) {
    for (const id of ["connect-form", "pin-form", "busy"]) $(id).hidden = id !== which;
}

function connect(name) {
    if (!window.isSecureContext || !window.RTCPeerConnection) {
        say("This browser can't stream: it needs WebRTC, on this page's https:// address.", true);
        return;
    }
    say("Connecting…");
    $("connect").disabled = true;
    ws = new WebSocket(`wss://${location.host}/ws`);
    ws.onopen = () => send("H", { name, token: store(TOKEN) || "", v: 1, web: true });
    ws.onmessage = (ev) => {
        let msg;
        try { msg = JSON.parse(ev.data); } catch (e) { return; }
        handle(msg.k, msg.d || {}, name).catch((e) => console.error(e));
    };
    ws.onclose = () => {
        const wasStreaming = streaming;
        stop();
        if (!$("message").classList.contains("error"))
            say(wasStreaming ? "Disconnected." : "The connection closed.", !wasStreaming);
    };
}

async function handle(k, d, name) {
    switch (k) {
    case "N":                                   // pair: the host shows a PIN
        pairing = true;
        hostName = d.name || hostName;
        for (const el of document.querySelectorAll(".host-name")) el.textContent = hostName;
        show("pin-form");
        say("");
        $("pin").value = "";
        $("pin").focus();
        $("pin-form").onsubmit = async (ev) => {
            ev.preventDefault();
            send("P", { proof: await proof($("pin").value.trim(), d.fingerprint || "", name) });
            say("Checking…");
        };
        break;
    case "W":
        say(`Wrong PIN. ${d.left} ${d.left === 1 ? "try" : "tries"} left.`, true);
        $("pin").value = "";
        $("pin").focus();
        break;
    case "T":
        store(TOKEN, d.token);
        pairing = false;
        show("connect-form");
        say("Paired. Starting…");
        break;
    case "B":                                   // someone is watching already
        $("busy-text").textContent = `${(d.viewers || []).join(", ")} ${d.viewers && d.viewers.length > 1
            ? "are" : "is"} already streaming this computer.`;
        show("busy");
        say("");
        $("join").onclick = () => { send("J", { mode: "join" }); show("connect-form"); say("Starting…"); };
        $("takeover").onclick = () => { send("J", { mode: "takeover" }); show("connect-form"); say("Starting…"); };
        break;
    case "E":
        say(d.error || "The other computer ended the stream.", true);
        if (/PIN|paired|pair/i.test(d.error || "")) store(TOKEN, null);
        break;
    case "O":                                   // streaming: the picture is on its way
        info = d;
        hostName = d.name || hostName;
        begin();
        break;
    case "offer":
        await answer(d.sdp);
        break;
    case "ice":
        if (pc && d.c) await pc.addIceCandidate({ candidate: d.c, sdpMLineIndex: d.m }).catch(() => {});
        break;
    case "S":
        if (d.joined) toast(`${d.joined} is watching too`);
        else if (d.left) toast(`${d.left} stopped watching`);
        break;
    case "L":
        info.locked = d.locked;
        $("b-unlock").hidden = !d.locked;
        toast(d.locked ? "The computer locked" : "The computer is unlocked");
        break;
    case "u":
        toast(d.ok ? "Unlocked" : (d.message || "Not unlocked"));
        break;
    case "R":
        rumble(d);
        break;
    case "z": {                                 // the picture quality the host is sending
        const first = !qualityKnown, mine = store(QUALITY);
        qualityKnown = true;
        if (first && mine && mine !== d.quality) {  // this browser's choice, from last time
            send("Z", { quality: mine });
            break;
        }
        if (d.quality) {
            const changed = $("quality").value !== d.quality;
            $("quality").value = d.quality;
            if (!first && changed)
                toast(`Picture quality: ${$("quality").selectedOptions[0].text}` + (d.by ? ` (set by ${d.by})` : ""));
        }
        break;
    }
    }
}

async function answer(sdp) {
    if (pc) pc.close();
    pc = new RTCPeerConnection({ iceServers: [], bundlePolicy: "max-bundle" });
    const stream = new MediaStream();
    const video = $("video");
    video.srcObject = stream;
    pc.ontrack = (ev) => {
        // as little waiting as the browser allows: this is a live screen
        try { ev.receiver.jitterBufferTarget = 0; } catch (e) { /* older browsers */ }
        try { ev.receiver.playoutDelayHint = 0; } catch (e) { /* only Chrome */ }
        stream.addTrack(ev.track);
        play();
    };
    pc.onicecandidate = (ev) => {
        if (ev.candidate && ev.candidate.candidate)
            send("ice", { c: ev.candidate.candidate, m: ev.candidate.sdpMLineIndex || 0 });
    };
    pc.onconnectionstatechange = () => {
        if (pc && pc.connectionState === "failed") {
            say("The picture couldn't get through. Is a firewall blocking UDP ports 48302–48399?", true);
            if (ws) ws.close();
        }
    };
    await pc.setRemoteDescription({ type: "offer", sdp });
    const ans = await pc.createAnswer();
    await pc.setLocalDescription(ans);
    send("answer", { sdp: pc.localDescription.sdp });
}

function play() {
    const video = $("video");
    video.muted = false;
    video.play().then(() => { $("tap").hidden = true; }).catch(() => {
        // the browser wants a tap before it plays sound
        video.muted = true;
        video.play().catch(() => {});
        $("tap").hidden = !info.audio;
    });
}

function begin() {
    streaming = true;
    $("start").hidden = true;
    $("stage").hidden = false;
    $("title").textContent = hostName || "Firox Stream";
    document.title = `${hostName} · Firox Stream`;
    $("b-unlock").hidden = !info.locked;
    for (const id of ["b-mouse", "b-keys"]) $(id).hidden = !info.input;
    if (!info.input) toast("Watching only: that computer can't take keyboard and mouse input");
    showBar();
    $("video").focus();
}

function stop() {
    streaming = false;
    if (pc) { pc.close(); pc = null; }
    ws = null;
    qualityKnown = false;
    if (document.pointerLockElement) document.exitPointerLock();
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    $("video").srcObject = null;
    $("stage").hidden = true;
    $("start").hidden = false;
    show("connect-form");
    $("connect").disabled = false;
    pads.clear();
}

// ----------------------------------------------------------------- input --

// Keys as Linux key codes (what the host's virtual keyboard types), by
// position (KeyboardEvent.code), so every layout works as on the host
const KEYS = {
    Escape: 1, Digit1: 2, Digit2: 3, Digit3: 4, Digit4: 5, Digit5: 6, Digit6: 7, Digit7: 8, Digit8: 9,
    Digit9: 10, Digit0: 11, Minus: 12, Equal: 13, Backspace: 14, Tab: 15, KeyQ: 16, KeyW: 17, KeyE: 18,
    KeyR: 19, KeyT: 20, KeyY: 21, KeyU: 22, KeyI: 23, KeyO: 24, KeyP: 25, BracketLeft: 26, BracketRight: 27,
    Enter: 28, ControlLeft: 29, KeyA: 30, KeyS: 31, KeyD: 32, KeyF: 33, KeyG: 34, KeyH: 35, KeyJ: 36,
    KeyK: 37, KeyL: 38, Semicolon: 39, Quote: 40, Backquote: 41, ShiftLeft: 42, Backslash: 43, KeyZ: 44,
    KeyX: 45, KeyC: 46, KeyV: 47, KeyB: 48, KeyN: 49, KeyM: 50, Comma: 51, Period: 52, Slash: 53,
    ShiftRight: 54, NumpadMultiply: 55, AltLeft: 56, Space: 57, CapsLock: 58, F1: 59, F2: 60, F3: 61,
    F4: 62, F5: 63, F6: 64, F7: 65, F8: 66, F9: 67, F10: 68, NumLock: 69, ScrollLock: 70, Numpad7: 71,
    Numpad8: 72, Numpad9: 73, NumpadSubtract: 74, Numpad4: 75, Numpad5: 76, Numpad6: 77, NumpadAdd: 78,
    Numpad1: 79, Numpad2: 80, Numpad3: 81, Numpad0: 82, NumpadDecimal: 83, IntlBackslash: 86, F11: 87,
    F12: 88, NumpadEnter: 96, ControlRight: 97, NumpadDivide: 98, PrintScreen: 99, AltRight: 100,
    Home: 102, ArrowUp: 103, PageUp: 104, ArrowLeft: 105, ArrowRight: 106, End: 107, ArrowDown: 108,
    PageDown: 109, Insert: 110, Delete: 111, AudioVolumeMute: 113, AudioVolumeDown: 114,
    AudioVolumeUp: 115, NumpadEqual: 117, Pause: 119, MetaLeft: 125, MetaRight: 126, OSLeft: 125,
    OSRight: 126, ContextMenu: 127, MediaTrackNext: 163, MediaPlayPause: 164, MediaTrackPrevious: 165,
    MediaStop: 166,
};

// Typed characters (on-screen keyboards send text, not keys) on a US layout
const PLAIN = "1234567890-=qwertyuiop[]asdfghjkl;'`\\zxcvbnm,./ ";
const SHIFTED = "!@#$%^&*()_+QWERTYUIOP{}ASDFGHJKL:\"~|ZXCVBNM<>? ";
const CODES = [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 30, 31,
               32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 57];

let queued = [], flushing = false, lastMove = null;
function input(ev) {
    if (!streaming || !info.input) return;
    if (ev[0] === "m") lastMove = ev;           // only the newest position matters
    else {
        if (lastMove) { queued.push(lastMove); lastMove = null; }
        queued.push(ev);
    }
    if (!flushing) {
        flushing = true;
        requestAnimationFrame(flush);
    }
}
function flush() {
    flushing = false;
    if (lastMove) { queued.push(lastMove); lastMove = null; }
    if (queued.length) send("I", queued);
    queued = [];
}

const typing = $("typing");
document.addEventListener("keydown", (ev) => {
    if (!streaming || ev.target === typing) return;
    const code = KEYS[ev.code];
    if (!code) return;
    ev.preventDefault();
    if (!ev.repeat) input(["k", code, 1]);
}, true);
document.addEventListener("keyup", (ev) => {
    if (!streaming || ev.target === typing) return;
    const code = KEYS[ev.code];
    if (!code) return;
    ev.preventDefault();
    input(["k", code, 0]);
}, true);
window.addEventListener("blur", () => { if (streaming) input(["r"]); });   // nothing stays held down

function press(code, shift) {
    if (shift) input(["k", 42, 1]);
    input(["k", code, 1]);
    input(["k", code, 0]);
    if (shift) input(["k", 42, 0]);
}
typing.addEventListener("beforeinput", (ev) => {
    ev.preventDefault();
    if (ev.inputType === "deleteContentBackward") return press(14, false);
    if (ev.inputType === "insertLineBreak" || ev.inputType === "insertParagraph") return press(28, false);
    for (const ch of ev.data || "") {
        if (ch === "\n") { press(28, false); continue; }
        let i = PLAIN.indexOf(ch);
        if (i >= 0) { press(CODES[i], false); continue; }
        i = SHIFTED.indexOf(ch);
        if (i >= 0) press(CODES[i], true);
    }
});
typing.addEventListener("keydown", (ev) => {
    // Named keys (Backspace, Enter, the arrows…) as keys: an empty box sends
    // no text for them. Characters come as text (beforeinput).
    const code = KEYS[ev.code];
    if (code && ev.key.length > 1 && ev.key !== "Unidentified" && ev.key !== "Process") {
        ev.preventDefault();
        press(code, false);
    }
});

// Where in the picture (0..1), allowing for the black bars around it
function position(ev) {
    const v = $("video"), r = v.getBoundingClientRect();
    const vw = v.videoWidth || r.width, vh = v.videoHeight || r.height;
    const scale = Math.min(r.width / vw, r.height / vh);
    const w = vw * scale, h = vh * scale;
    const x = r.left + (r.width - w) / 2, y = r.top + (r.height - h) / 2;
    return [Math.min(Math.max((ev.clientX - x) / w, 0), 1), Math.min(Math.max((ev.clientY - y) / h, 0), 1)];
}

// Browser buttons (left, middle, right, back, forward) as Linux's
const BUTTONS = [0x110, 0x112, 0x111, 0x113, 0x114];
const video = $("video");
const locked = () => document.pointerLockElement === video;
video.addEventListener("pointermove", (ev) => {
    if (locked()) input(["d", ev.movementX, ev.movementY]);
    else input(["m", ...position(ev)]);
    showBar(ev.clientY);
});
video.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    video.focus();
    video.setPointerCapture(ev.pointerId);
    if (!locked()) input(["m", ...position(ev)]);
    if (BUTTONS[ev.button] !== undefined) input(["b", BUTTONS[ev.button], 1]);
});
video.addEventListener("pointerup", (ev) => {
    if (!locked()) input(["m", ...position(ev)]);
    if (BUTTONS[ev.button] !== undefined) input(["b", BUTTONS[ev.button], 0]);
});
video.addEventListener("pointercancel", () => input(["b", 0x110, 0]));
video.addEventListener("contextmenu", (ev) => ev.preventDefault());
video.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    // notches: about 100 pixels or 3 lines each (y down, as the apps send)
    const per = ev.deltaMode === 1 ? 3 : ev.deltaMode === 2 ? 0.1 : 100;
    input(["s", ev.deltaX / per, ev.deltaY / per]);
}, { passive: false });

// ------------------------------------------------------- game controllers --

// The standard layout's buttons as SDL's (the wire format): A B X Y, LB RB,
// (LT RT are axes), Back Start, the sticks, the D-pad, Guide
const PAD_BITS = { 0: 0, 1: 1, 2: 2, 3: 3, 4: 9, 5: 10, 8: 4, 9: 6, 10: 7, 11: 8, 12: 11, 13: 12, 14: 13,
                   15: 14, 16: 5 };
const pads = new Map();                         // index -> last state sent

function stick(v) {
    if (Math.abs(v) < 0.08) return 0;
    return Math.max(-32768, Math.min(32767, Math.round(v * 32767)));
}
function trigger(b) {
    return b ? Math.round(Math.max(0, Math.min(1, b.value)) * 32767) : 0;
}
function pollPads() {
    requestAnimationFrame(pollPads);
    if (!streaming || !info.gamepads || !navigator.getGamepads) return;
    const seen = new Set();
    for (const gp of navigator.getGamepads()) {
        if (!gp || !gp.connected || gp.index > 3) continue;
        seen.add(gp.index);
        let bits = 0;
        gp.buttons.forEach((b, i) => { if (b.pressed && PAD_BITS[i] !== undefined) bits |= 1 << PAD_BITS[i]; });
        const a = gp.axes;
        const state = [bits, stick(a[0] || 0), stick(a[1] || 0), stick(a[2] || 0), stick(a[3] || 0),
                       trigger(gp.buttons[6]), trigger(gp.buttons[7])];
        const last = pads.get(gp.index);
        if (last && last.every((v, i) => v === state[i])) continue;
        const msg = { p: gp.index, s: state };
        if (!last) { msg.name = gp.id; toast(`Controller ${gp.index + 1} connected`); }
        pads.set(gp.index, state);
        send("G", msg);
    }
    for (const index of [...pads.keys()]) {
        if (!seen.has(index)) {
            pads.delete(index);
            send("G", { p: index, gone: 1 });
            toast(`Controller ${index + 1} unplugged`);
        }
    }
}
requestAnimationFrame(pollPads);

function rumble(d) {
    const gp = navigator.getGamepads ? navigator.getGamepads()[d.p] : null;
    const act = gp && gp.vibrationActuator;
    if (!act) return;
    if (!d.ms || (!d.lo && !d.hi)) {
        if (act.reset) act.reset().catch(() => {});
        return;
    }
    act.playEffect("dual-rumble", { duration: Math.min(d.ms, 5000), strongMagnitude: (d.lo || 0) / 65535,
                                    weakMagnitude: (d.hi || 0) / 65535 }).catch(() => {});
}

document.addEventListener("visibilitychange", () => {
    if (!streaming) return;
    if (document.hidden) {
        input(["r"]);
        send("G", { idle: 1 });                 // let go of the controllers in the background
        pads.clear();
    }
});

// ---------------------------------------------------------------- the bar --

let barTimer = 0;
function showBar(y) {
    const bar = $("bar");
    if (y !== undefined && y > 80 && bar.classList.contains("hidden")) return;
    bar.classList.remove("hidden");
    clearTimeout(barTimer);
    barTimer = setTimeout(() => { if (!bar.matches(":hover")) bar.classList.add("hidden"); }, 3000);
}
$("bar").addEventListener("mouseleave", () => showBar());
video.addEventListener("touchstart", (ev) => { if (ev.touches.length >= 3) showBar(); }, { passive: true });

$("b-full").onclick = async () => {
    if (document.fullscreenElement) return document.exitFullscreen();
    await $("stage").requestFullscreen().catch(() => toast("This browser won't go full screen"));
    // Chrome: the Esc, Alt+Tab and Windows keys go to the other computer too
    if (navigator.keyboard && navigator.keyboard.lock) navigator.keyboard.lock().catch(() => {});
};
$("b-mouse").onclick = () => {
    if (locked()) document.exitPointerLock();
    else {
        const asked = video.requestPointerLock();     // a promise in newer browsers
        if (asked && asked.catch) asked.catch(() => toast("This browser won't capture the mouse"));
    }
};
document.addEventListener("pointerlockchange", () => {
    $("b-mouse").classList.toggle("on", locked());
    if (locked()) toast("The mouse is captured. Press Esc to let go.");
});
$("b-keys").onclick = () => { typing.value = ""; typing.focus(); };
$("b-wake").onclick = () => { send("W"); toast("Waking its screen"); };
$("b-unlock").onclick = () => {
    const password = prompt(`The password for ${hostName}:`);
    if (password) send("U", { password });
};
$("b-stop").onclick = () => {
    send("Q");
    say("Disconnected.");
    if (ws) ws.close();
};
$("quality").onchange = () => {
    store(QUALITY, $("quality").value);         // remembered in this browser
    send("Z", { quality: $("quality").value });
    $("video").focus();
};
$("tap").onclick = () => { $("video").muted = false; $("video").play(); $("tap").hidden = true; };

// --------------------------------------------------------------- the start --

$("name").value = store(NAME) || defaultName();
$("host").textContent = location.hostname;
$("connect-form").onsubmit = (ev) => {
    ev.preventDefault();
    const name = cleanName($("name").value);
    store(NAME, name);
    connect(name);
};
$("pin-cancel").onclick = () => { if (ws) ws.close(); say("Pairing cancelled."); };
$("busy-cancel").onclick = () => { if (ws) ws.close(); say(""); };
