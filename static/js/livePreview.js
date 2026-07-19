(function () {
    const root = document.getElementById("live-preview");
    if (!root) return;

    const latestUrl = root.dataset.latestUrl;
    const streamUrl = root.dataset.streamUrl;
    const ttlSeconds = Number(root.dataset.ttlSeconds || 15);
    const statePill = document.getElementById("live-state");
    const rawFrame = document.getElementById("raw-frame");
    const copyButton = document.getElementById("copy-raw-frame");
    const fields = {};
    document.querySelectorAll("[data-field]").forEach((node) => {
        fields[node.dataset.field] = node;
    });

    let source = null;
    let reconnectTimer = null;
    let reconnectDelay = 1000;
    let connected = false;
    let latestSample = null;
    let rawFrameText = "";

    function setText(name, value) {
        if (fields[name]) fields[name].textContent = value;
    }

    function displayNumber(value, digits, suffix) {
        if (value === null || value === undefined) return "--";
        if (typeof value !== "number" || !Number.isFinite(value)) return "--";
        return `${value.toFixed(digits)}${suffix || ""}`;
    }

    function displayInteger(value, suffix) {
        if (value === null || value === undefined) return "--";
        if (typeof value !== "number" || !Number.isFinite(value)) return "--";
        return `${Math.trunc(value)}${suffix || ""}`;
    }

    function displayDate(value) {
        if (!value) return "--";
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return "--";
        return date.toLocaleString();
    }

    function sampleAgeSeconds() {
        if (!latestSample || !latestSample.server_received_at) return null;
        const received = new Date(latestSample.server_received_at).getTime();
        if (Number.isNaN(received)) return null;
        return Math.max(0, Math.floor((Date.now() - received) / 1000));
    }

    function setState(label, mode) {
        statePill.textContent = label;
        statePill.className = `status ${mode}`;
    }

    function updateState() {
        const age = sampleAgeSeconds();
        if (age === null || age > ttlSeconds) {
            setText("sample_age", "--");
            setState(connected ? "NO DATA" : "DISCONNECTED", "idle");
            return;
        }
        setText("sample_age", `${age}s ago`);
        if (!connected) {
            setState("DISCONNECTED", "idle");
        } else if (age < 3) {
            setState("LIVE", "online");
        } else {
            setState("STALE", "idle");
        }
    }

    function renderSample(sample) {
        latestSample = sample;
        setText("rpm", displayInteger(sample.rpm));
        setText("tps", displayNumber(sample.tps, 1, "%"));
        setText("tps_voltage", displayNumber(sample.tps_voltage, 2, " V"));
        setText("ect", displayNumber(sample.ect, 1, " C"));
        setText("iat", displayNumber(sample.iat, 1, " C"));
        setText("battery", displayNumber(sample.battery, 2, " V"));
        setText("injector_ms", displayNumber(sample.injector_ms, 2, " ms"));
        setText("fuel_cut_inferred", sample.fuel_cut_inferred === undefined ? "--" : sample.fuel_cut_inferred ? "YES" : "NO");
        setText("session_id", sample.session_id || "--");
        setText("seq", displayInteger(sample.seq));
        setText("device_time_ms", displayInteger(sample.device_time_ms, " ms"));
        setText("server_received_at", displayDate(sample.server_received_at));
        setText("parser_version", sample.parser_version || "--");
        setText("injector_raw", displayInteger(sample.injector_raw));
        setText("raw_length", displayInteger(sample.raw_length, " bytes"));
        rawFrameText = sample.raw_frame || "";
        rawFrame.textContent = rawFrameText || "--";
        copyButton.disabled = !rawFrameText;
        updateState();
    }

    function scheduleReconnect() {
        if (reconnectTimer) return;
        reconnectTimer = window.setTimeout(() => {
            reconnectTimer = null;
            connectStream();
            reconnectDelay = Math.min(reconnectDelay * 2, 30000);
        }, reconnectDelay);
    }

    function closeStream() {
        if (source) {
            source.close();
            source = null;
        }
    }

    function connectStream() {
        closeStream();
        source = new EventSource(streamUrl, { withCredentials: true });
        source.onopen = () => {
            connected = true;
            reconnectDelay = 1000;
            updateState();
        };
        source.addEventListener("telemetry", (event) => {
            try {
                connected = true;
                renderSample(JSON.parse(event.data));
            } catch (error) {
                connected = false;
                updateState();
            }
        });
        source.addEventListener("live-error", () => {
            connected = false;
            updateState();
        });
        source.onerror = () => {
            connected = false;
            updateState();
            closeStream();
            scheduleReconnect();
        };
    }

    function loadLatest() {
        fetch(latestUrl, { cache: "no-store", credentials: "same-origin" })
            .then((response) => response.ok ? response.json() : Promise.reject(new Error("latest unavailable")))
            .then((payload) => {
                if (payload.sample) renderSample(payload.sample);
                else updateState();
            })
            .catch(() => updateState());
    }

    copyButton.addEventListener("click", () => {
        if (!rawFrameText || !navigator.clipboard) return;
        navigator.clipboard.writeText(rawFrameText);
    });

    window.addEventListener("beforeunload", closeStream);
    window.setInterval(updateState, 1000);
    copyButton.disabled = true;
    loadLatest();
    connectStream();
})();
