// ─── DOM REFS ────────────────────────────────
const chatMessages = document.getElementById("chatMessages");
const chatInput = document.getElementById("chatInput");
const chatSendBtn = document.getElementById("chatSendBtn");
const chatStatus = document.getElementById("chatStatus");

// ─── CONVERSATION HISTORY ────────────────────
let conversationHistory = [];

// ─── SYSTEM PROMPT ───────────────────────────
const SYSTEM_PROMPT = `You are an expert automotive OBD2 diagnostic assistant built into a vehicle diagnostic dashboard.
Your role is to:
- Explain OBD2 error codes clearly and simply
- Suggest step-by-step diagnostic and repair procedures
- Interpret live maintenance alerts from the vehicle
- Answer questions about vehicle health, sensors, and systems

Keep responses concise and helpful. Use short bullet points when listing steps.
If referencing an error code, always explain which system it affects.`;

// ─── HELPERS ─────────────────────────────────

function scrollToBottom() {
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function setStatus(text, mode = "ready") {
    chatStatus.textContent = text;
    chatStatus.className = "chat-status";
    if (mode === "thinking") chatStatus.classList.add("thinking");
    if (mode === "error") chatStatus.classList.add("error");
}

function appendMessage(role, text) {
    const isUser = role === "user";
    const wrapper = document.createElement("div");
    const avatar = document.createElement("div");
    const bubble = document.createElement("div");

    wrapper.className = `message ${isUser ? "user-message" : "bot-message"}`;
    avatar.className = `avatar ${isUser ? "user-avatar" : "bot-avatar"}`;
    avatar.textContent = isUser ? "YOU" : "AI";
    bubble.className = "bubble";
    bubble.textContent = text;

    wrapper.appendChild(avatar);
    wrapper.appendChild(bubble);
    chatMessages.appendChild(wrapper);
    scrollToBottom();
    return wrapper;
}

function showTypingIndicator() {
    const wrapper = document.createElement("div");
    const avatar = document.createElement("div");
    const bubble = document.createElement("div");
    const dots = document.createElement("div");

    wrapper.className = "message bot-message typing-indicator";
    avatar.className = "avatar bot-avatar";
    avatar.textContent = "AI";
    bubble.className = "bubble";
    dots.className = "typing-dots";
    dots.innerHTML = "<span></span><span></span><span></span>";

    bubble.appendChild(dots);
    wrapper.appendChild(avatar);
    wrapper.appendChild(bubble);
    chatMessages.appendChild(wrapper);
    scrollToBottom();
    return wrapper;
}

function getMaintenanceContext() {
    const el = document.getElementById("maintenance");
    if (!el) return "";
    const text = el.innerText.trim();
    return text && text !== "Waiting data..."
        ? `\n\nCurrent vehicle maintenance alerts from OBD2 scan:\n${text}`
        : "";
}

// ─── SEND MESSAGE ─────────────────────────────
async function sendMessage() {
    const text = chatInput.value.trim();
    if (!text) return;

    chatInput.value = "";
    chatSendBtn.disabled = true;
    setStatus("THINKING...", "thinking");

    appendMessage("user", text);
    conversationHistory.push({ role: "user", content: text });

    const typingEl = showTypingIndicator();

    try {
        // ✅ Calls your Flask /chat route — API key stays on the server
        const response = await fetch("/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                system: SYSTEM_PROMPT + getMaintenanceContext(),
                messages: conversationHistory,
            }),
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || `HTTP ${response.status}`);
        }

        const reply = data.reply || "Sorry, I couldn't generate a response.";

        conversationHistory.push({ role: "assistant", content: reply });
        typingEl.remove();
        appendMessage("assistant", reply);
        setStatus("READY", "ready");
    } catch (error) {
        typingEl.remove();
        appendMessage("assistant", `⚠️ Error: ${error.message}`);
        setStatus("ERROR", "error");
        setTimeout(() => setStatus("READY", "ready"), 3000);
    } finally {
        chatSendBtn.disabled = false;
        chatInput.focus();
    }
}

// ─── ENTER KEY ───────────────────────────────
chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});
