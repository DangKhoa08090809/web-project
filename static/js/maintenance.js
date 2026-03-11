async function fetchMaintenanceData() {
    try {
        const response = await fetch("http://localhost:5000/analyze", {
            method: "GET",
            headers: {
                "Content-Type": "application/json",
            },
        });

        if (!response.ok) {
            const errorData = await response.json();
            document.getElementById("maintenance").innerHTML =
                `<p style="color:red;">⚠️ ${errorData.error || "Không thể tải dữ liệu."}</p>`;
            return;
        }

        const data = await response.json();
        const maintenanceList = data.maintenance;
        const container = document.getElementById("maintenance");

        if (!maintenanceList || maintenanceList.length === 0) {
            container.innerHTML = "<p>✅ Không có hạng mục bảo dưỡng nào.</p>";
            return;
        }

        const html = maintenanceList.map((item) => `<li>${item}</li>`).join("");

        container.innerHTML = `<ul>${html}</ul>`;
    } catch (error) {
        document.getElementById("maintenance").innerHTML =
            `<p style="color:red;">❌ Lỗi kết nối: ${error.message}</p>`;
    }
}

/* ============================================
   chatbox.js  — OBD2 Master AI Assistant
   Powered by Anthropic Claude API
   ============================================ */

// 🔑 Replace with your actual API key
const ANTHROPIC_API_KEY = "YOUR_API_KEY_HERE";

// ─── DOM refs ───────────────────────────────
const chatMessages = document.getElementById("chatMessages");
const chatInput = document.getElementById("chatInput");
const chatSendBtn = document.getElementById("chatSendBtn");
const chatStatus = document.getElementById("chatStatus");

// ─── System context ──────────────────────────
const SYSTEM_PROMPT = `You are an expert automotive OBD2 diagnostic assistant integrated into a vehicle diagnostic dashboard.
Your role is to:
- Explain OBD2 error codes clearly and concisely
- Suggest diagnostic procedures and repair steps
- Interpret maintenance alerts from the vehicle's live data
- Answer questions about vehicle health, sensors, and systems

Keep responses concise and technical but easy to understand. Use bullet points when listing steps.
If you mention an error code, always explain what system it relates to.`;

// ─── Chat history (for multi-turn context) ───
let conversationHistory = [];

// ─── Helpers ────────────────────────────────

function scrollToBottom() {
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function setStatus(text, thinking = false) {
    chatStatus.textContent = text;
    chatStatus.classList.toggle("thinking", thinking);
}

function appendMessage(role, text) {
    const wrapper = document.createElement("div");
    const avatar = document.createElement("div");
    const bubble = document.createElement("div");

    const isUser = role === "user";

    wrapper.className = `message ${isUser ? "user-message" : "bot-message"}`;
    avatar.className = `message-avatar ${isUser ? "user-avatar" : "bot-avatar"}`;
    avatar.textContent = isUser ? "YOU" : "AI";

    bubble.className = "message-bubble";
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
    avatar.className = "message-avatar bot-avatar";
    avatar.textContent = "AI";
    bubble.className = "message-bubble";
    dots.className = "typing-dots";
    dots.innerHTML = "<span></span><span></span><span></span>";

    bubble.appendChild(dots);
    wrapper.appendChild(avatar);
    wrapper.appendChild(bubble);
    chatMessages.appendChild(wrapper);
    scrollToBottom();

    return wrapper;
}

// ─── Current maintenance context ─────────────
function getMaintenanceContext() {
    const el = document.getElementById("maintenance");
    if (!el) return "";
    const text = el.innerText.trim();
    return text && text !== "Waiting Error..."
        ? `\n\nCurrent vehicle maintenance alerts from OBD2:\n${text}`
        : "";
}

// ─── Send message ─────────────────────────────
async function sendMessage() {
    const text = chatInput.value.trim();
    if (!text) return;

    // Clear input and disable button
    chatInput.value = "";
    chatSendBtn.disabled = true;
    setStatus("THINKING...", true);

    // Show user bubble
    appendMessage("user", text);

    // Add to history
    conversationHistory.push({ role: "user", content: text });

    // Typing indicator
    const typingEl = showTypingIndicator();

    try {
        const response = await fetch("https://api.anthropic.com/v1/messages", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                // Note: direct browser calls require CORS proxy or backend relay in production
            },
            body: JSON.stringify({
                model: "claude-sonnet-4-20250514",
                max_tokens: 1024,
                system: SYSTEM_PROMPT + getMaintenanceContext(),
                messages: conversationHistory,
            }),
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err?.error?.message || `HTTP ${response.status}`);
        }

        const data = await response.json();
        const reply =
            data.content?.[0]?.text || "Sorry, I couldn't generate a response.";

        // Add assistant reply to history
        conversationHistory.push({ role: "assistant", content: reply });

        // Replace typing indicator with actual response
        typingEl.remove();
        appendMessage("assistant", reply);
        setStatus("READY", false);
    } catch (error) {
        typingEl.remove();
        appendMessage("assistant", `⚠️ Error: ${error.message}`);
        setStatus("ERROR", false);
        // Reset status after 3 s
        setTimeout(() => setStatus("READY", false), 3000);
    } finally {
        chatSendBtn.disabled = false;
        chatInput.focus();
    }
}

// ─── Enter key support ───────────────────────
chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

// Fetch on page load
fetchMaintenanceData();

// Auto-refresh every 5 seconds
setInterval(fetchMaintenanceData, 5000);
