(function () {
    const root = document.getElementById("live-preview");
    if (!root) return;

    const latestUrl = root.dataset.latestUrl;
    const streamUrl = root.dataset.streamUrl;
    const ttlSeconds = Number(root.dataset.ttlSeconds || 15);
    const controlUrl = root.dataset.controlUrl;
    const enableUrl = root.dataset.enableUrl;
    const disableUrl = root.dataset.disableUrl;
    const statePill = document.getElementById("live-state");
    const rawFrame = document.getElementById("raw-frame");
    const copyButton = document.getElementById("copy-raw-frame");
    const controlState = document.getElementById("live-control-state");
    const controlMessage = document.getElementById("live-control-message");
    const controlButton = document.getElementById("live-control-action");
    const controlCsrf = document.getElementById("live-control-csrf");
    const fields = {};
    document.querySelectorAll("[data-field]").forEach((node) => {
        fields[node.dataset.field] = node;
    });

    let source = null;
    let reconnectTimer = null;
    let stateTimer = null;
    let controlTimer = null;
    let reconnectDelay = 1000;
    let connected = false;
    let latestSample = null;
    let rawFrameText = "";
    let controlMode = "offline";

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
        setText("tps_raw_candidate", displayInteger(sample.tps_raw_candidate));
        setText("ect_c_candidate", displayNumber(sample.ect_c_candidate ?? sample.ect, 1, " C"));
        setText("iat_c", displayNumber(sample.iat_c ?? sample.iat, 1, " C"));
        setText("map_raw", displayInteger(sample.map_raw));
        setText("battery", displayNumber(sample.battery_voltage ?? sample.battery, 2, " V"));
        setText("injector_ms", displayNumber(sample.injector_ms, 2, " ms"));
        setText("fuel_cut_inferred", sample.fuel_cut_inferred === undefined ? "--" : sample.fuel_cut_inferred ? "YES" : "NO");
        setText("session_id", sample.session_id || "--");
        setText("seq", displayInteger(sample.seq));
        setText("device_time_ms", displayInteger(sample.device_time_ms, " ms"));
        setText("server_received_at", displayDate(sample.server_received_at));
        setText("decoder", sample.decoder_id && sample.decoder_version ? `${sample.decoder_id}:${sample.decoder_version}` : sample.parser_version || "--");
        setText("ecu_profile_id", sample.ecu_profile_id || "--");
        setText("frame_valid", sample.frame_valid === undefined ? "--" : sample.frame_valid ? "YES" : "NO");
        setText("checksum_valid", sample.checksum_valid === undefined ? "--" : sample.checksum_valid ? "PASS" : "FAIL");
        setText("injector_raw", displayInteger(sample.injector_raw));
        setText("raw_length", displayInteger(sample.raw_length, " bytes"));
        rawFrameText = sample.raw_frame || "";
        rawFrame.textContent = rawFrameText || "--";
        copyButton.disabled = !rawFrameText;
        updateState();
    }

    function csrfToken() {
        return controlCsrf ? controlCsrf.value : "";
    }

    function controlLabel(mode) {
        return {
            on: "ON",
            off: "OFF",
            enabling: "ENABLING...",
            disabling: "DISABLING...",
            offline: "UNAVAILABLE",
        }[mode] || "UNAVAILABLE";
    }

    function renderControl(control) {
        if (!controlState || !controlMessage || !controlButton) return;
        const online = control && control.control_online;
        controlMode = online ? control.state : "offline";
        controlState.textContent = controlLabel(controlMode);
        controlButton.disabled = !online || controlMode === "enabling" || controlMode === "disabling";

        if (!online) {
            controlMessage.textContent = "Live Mode unavailable - ECU Reader offline.";
            controlButton.textContent = "Enable Live Mode";
            return;
        }
        if (controlMode === "on") {
            controlMessage.textContent = `Boot ${control.current_boot_id} is reporting Live Mode on.`;
            controlButton.textContent = "Disable Live Mode";
        } else if (controlMode === "off") {
            controlMessage.textContent = `Boot ${control.current_boot_id} is ready for Live Mode control.`;
            controlButton.textContent = "Enable Live Mode";
        } else if (controlMode === "enabling") {
            controlMessage.textContent = "Enable command is waiting for ECU Reader acknowledgement.";
            controlButton.textContent = "Enable Live Mode";
        } else if (controlMode === "disabling") {
            controlMessage.textContent = "Disable command is waiting for ECU Reader acknowledgement.";
            controlButton.textContent = "Disable Live Mode";
        }
    }

    function renderControlError(message) {
        if (!controlState || !controlMessage || !controlButton) return;
        controlMode = "offline";
        controlState.textContent = "UNAVAILABLE";
        controlMessage.textContent = message || "Live Mode control is unavailable.";
        controlButton.disabled = true;
    }

    async function loadControl() {
        if (!controlUrl) return;
        try {
            const payload = await fetch(controlUrl, { cache: "no-store", credentials: "same-origin" }).then((response) => {
                if (!response.ok) throw new Error("Live Mode control is unavailable.");
                return response.json();
            });
            renderControl(payload.control);
        } catch (error) {
            renderControlError(error.message);
        }
    }

    async function submitControl() {
        if (!controlButton || controlButton.disabled) return;
        const url = controlMode === "on" ? disableUrl : enableUrl;
        const pendingMode = controlMode === "on" ? "disabling" : "enabling";
        renderControl({
            control_online: true,
            state: pendingMode,
            current_boot_id: "",
        });
        try {
            const response = await fetch(url, {
                method: "POST",
                credentials: "same-origin",
                cache: "no-store",
                headers: {
                    "Accept": "application/json",
                    "X-CSRFToken": csrfToken(),
                },
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                const error = payload.error && payload.error.message ? payload.error.message : "Live Mode command failed.";
                throw new Error(error);
            }
            renderControl(payload.control);
        } catch (error) {
            renderControlError(error.message);
        }
    }

    function scheduleReconnect() {
        if (reconnectTimer || document.hidden) return;
        reconnectTimer = window.setTimeout(() => {
            reconnectTimer = null;
            connectStream();
            reconnectDelay = Math.min(reconnectDelay * 2, 30000);
        }, reconnectDelay);
    }

    function clearReconnect() {
        if (!reconnectTimer) return;
        window.clearTimeout(reconnectTimer);
        reconnectTimer = null;
    }

    function closeStream() {
        if (source) {
            source.close();
            source = null;
        }
    }

    function connectStream() {
        if (document.hidden) return;
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

    function startTimers() {
        if (!stateTimer) stateTimer = window.setInterval(updateState, 1000);
        if (controlUrl && !controlTimer) controlTimer = window.setInterval(loadControl, 2000);
    }

    function stopTimers() {
        if (stateTimer) {
            window.clearInterval(stateTimer);
            stateTimer = null;
        }
        if (controlTimer) {
            window.clearInterval(controlTimer);
            controlTimer = null;
        }
    }

    function suspendLivePreview() {
        clearReconnect();
        closeStream();
        stopTimers();
        connected = false;
        updateState();
    }

    function resumeLivePreview() {
        startTimers();
        loadLatest();
        loadControl();
        connectStream();
    }

    copyButton.addEventListener("click", () => {
        if (!rawFrameText || !navigator.clipboard) return;
        navigator.clipboard.writeText(rawFrameText);
    });
    if (controlButton) {
        controlButton.addEventListener("click", submitControl);
    }

    document.addEventListener("visibilitychange", () => {
        if (document.hidden) suspendLivePreview();
        else resumeLivePreview();
    });
    window.addEventListener("beforeunload", suspendLivePreview);
    copyButton.disabled = true;
    resumeLivePreview();
})();
