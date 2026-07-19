(function () {
    const sessionSelect = document.getElementById("tool-session");
    const eventSelect = document.getElementById("tool-event");
    const timeInput = document.getElementById("tool-time");
    const loadButton = document.getElementById("tool-load");
    const playButton = document.getElementById("play-toggle");
    const previousButton = document.getElementById("previous-sample");
    const nextButton = document.getElementById("next-sample");
    const speedSelect = document.getElementById("playback-speed");
    const slider = document.getElementById("sample-slider");
    const position = document.getElementById("sample-position");
    const table = document.getElementById("parameter-table");
    const search = document.getElementById("parameter-search");
    const invalidOnly = document.getElementById("filter-invalid");
    const changedOnly = document.getElementById("filter-changed");
    const resetMinMax = document.getElementById("reset-minmax");
    const rawFrame = document.getElementById("tool-raw-frame");
    const parsedFields = document.getElementById("parsed-fields");
    const copyFrame = document.getElementById("copy-frame");
    const downloadSample = document.getElementById("download-sample");
    const toggleRadix = document.getElementById("toggle-radix");
    const detailFields = {};
    document.querySelectorAll("[data-tool-field]").forEach((node) => detailFields[node.dataset.toolField] = node);
    if (!sessionSelect) return;

    const parameterMeta = new Map();
    let samples = [];
    let events = [];
    let index = 0;
    let timer = null;
    let decimalRaw = false;
    let minMax = {};

    function stop() {
        if (timer) window.clearInterval(timer);
        timer = null;
        playButton.textContent = "▶";
    }

    function play() {
        if (!samples.length) return;
        timer = window.setInterval(() => {
            if (index >= samples.length - 1) {
                stop();
                return;
            }
            index += 1;
            renderSample();
        }, Math.max(60, 450 / Number(speedSelect.value || 1)));
        playButton.textContent = "Ⅱ";
    }

    function setText(name, value) {
        if (detailFields[name]) detailFields[name].textContent = value;
    }

    function displayValue(value, precision) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
        return Number(value).toFixed(Number(precision || 0));
    }

    function rawToString(raw) {
        if (raw === null || raw === undefined) return "--";
        if (typeof raw === "string") {
            if (!decimalRaw) return raw;
            const bytes = raw.match(/.{1,2}/g) || [];
            return bytes.map((byte) => Number.parseInt(byte, 16)).filter((byte) => Number.isFinite(byte)).join(" ");
        }
        return JSON.stringify(raw, null, 2);
    }

    function rawLength(raw) {
        if (typeof raw === "string") return `${Math.floor(raw.length / 2)} bytes`;
        if (raw && typeof raw === "object") return `${JSON.stringify(raw).length} chars`;
        return "--";
    }

    function checksum(raw) {
        if (raw && typeof raw === "object") {
            if (raw.checksum_ok === true) return "Pass";
            if (raw.checksum_ok === false) return "Fail";
        }
        return "Not reported";
    }

    function validityFor(sample, key) {
        const value = sample[key];
        if (value === null || value === undefined || Number.isNaN(Number(value))) return "Missing";
        if (key === "battery" && (value < 11.8 || value > 15.2)) return "Attention";
        if (key === "ect" && value >= 112) return "Attention";
        if (sample.is_anomalous && (sample.signals || []).some((signal) => signal.toLowerCase().includes(key.split("_")[0]))) return "Attention";
        return "Valid";
    }

    function rebuildMinMax() {
        minMax = {};
        parameterMeta.forEach((meta, key) => {
            const values = samples.map((sample) => sample[key]).filter((value) => typeof value === "number" && Number.isFinite(value));
            if (values.length) minMax[key] = { min: Math.min(...values), max: Math.max(...values) };
        });
    }

    function renderParameters(sample) {
        const query = search.value.trim().toLowerCase();
        const previousSample = index > 0 ? samples[index - 1] : null;
        table.textContent = "";
        let count = 0;
        parameterMeta.forEach((meta, key) => {
            const valid = validityFor(sample, key);
            const changed = previousSample && previousSample[key] !== sample[key];
            if (query && !meta.label.toLowerCase().includes(query) && !key.includes(query)) return;
            if (invalidOnly.checked && valid === "Valid") return;
            if (changedOnly.checked && !changed) return;
            const row = document.createElement("tr");
            row.innerHTML = `<td>${meta.label}</td><td>${displayValue(sample[key], meta.precision)}</td><td>${meta.unit}</td><td>${displayValue(minMax[key] && minMax[key].min, meta.precision)}</td><td>${displayValue(minMax[key] && minMax[key].max, meta.precision)}</td><td>${key}</td><td>${sample[key] === null || sample[key] === undefined ? "Missing" : "Decoded"}</td><td><span class="badge ${valid === "Valid" ? "normal" : "minor-anomaly"}">${valid}</span></td>`;
            table.append(row);
            count += 1;
        });
        if (!count) {
            const row = document.createElement("tr");
            row.innerHTML = `<td colspan="8" class="empty">No parameters match the current filters.</td>`;
            table.append(row);
        }
    }

    function renderParsed(raw) {
        parsedFields.textContent = "";
        if (!raw || typeof raw !== "object") {
            const row = document.createElement("tr");
            row.innerHTML = `<td colspan="2" class="empty">No parsed raw fields available.</td>`;
            parsedFields.append(row);
            return;
        }
        Object.entries(raw).forEach(([key, value]) => {
            const row = document.createElement("tr");
            row.innerHTML = `<td>${key}</td><td><code>${JSON.stringify(value)}</code></td>`;
            parsedFields.append(row);
        });
    }

    function renderSample() {
        const sample = samples[index];
        slider.value = String(index);
        position.textContent = `${index + 1} / ${samples.length} · ${Math.round(sample.elapsed_seconds || 0)}s`;
        setText("timestamp", sample.timestamp || "--");
        setText("seq", sample.seq);
        setText("raw_length", rawLength(sample.raw_frame));
        setText("checksum", checksum(sample.raw_frame));
        rawFrame.textContent = rawToString(sample.raw_frame);
        renderParsed(sample.raw_frame);
        renderParameters(sample);
    }

    function jumpToElapsed(seconds) {
        if (!samples.length) return;
        const target = Number(seconds) || 0;
        let best = 0;
        let bestDistance = Infinity;
        samples.forEach((sample, sampleIndex) => {
            const distance = Math.abs((sample.elapsed_seconds || 0) - target);
            if (distance < bestDistance) {
                bestDistance = distance;
                best = sampleIndex;
            }
        });
        index = best;
        renderSample();
    }

    async function loadSession() {
        stop();
        loadButton.disabled = true;
        loadButton.textContent = "Loading";
        try {
            const payload = await window.drisafeApi.sessionSamples(sessionSelect.value, { max_points: 5000 });
            samples = payload.samples || [];
            events = payload.events || [];
            parameterMeta.clear();
            (payload.parameters || []).forEach((parameter) => parameterMeta.set(parameter.key, parameter));
            if (samples.some((sample) => typeof sample.anomaly_score === "number")) {
                parameterMeta.set("anomaly_score", { key: "anomaly_score", label: "Anomaly score", unit: "", precision: 2 });
            }
            eventSelect.textContent = "";
            const emptyOption = document.createElement("option");
            emptyOption.value = "";
            emptyOption.textContent = "No event selected";
            eventSelect.append(emptyOption);
            events.forEach((event) => {
                const option = document.createElement("option");
                option.value = event.start_elapsed;
                option.textContent = `${event.severity} · ${event.title} · ${event.duration_label}`;
                eventSelect.append(option);
            });
            index = 0;
            slider.max = String(Math.max(0, samples.length - 1));
            rebuildMinMax();
            if (samples.length) renderSample();
            else {
                table.innerHTML = `<tr><td colspan="8" class="empty">No samples are available in this session.</td></tr>`;
                rawFrame.textContent = "--";
                position.textContent = "--";
            }
        } catch (error) {
            table.innerHTML = `<tr><td colspan="8" class="empty">${error.message}</td></tr>`;
        } finally {
            loadButton.disabled = false;
            loadButton.textContent = "Load session";
        }
    }

    loadButton.addEventListener("click", loadSession);
    playButton.addEventListener("click", () => timer ? stop() : play());
    previousButton.addEventListener("click", () => {
        stop();
        index = Math.max(0, index - 1);
        renderSample();
    });
    nextButton.addEventListener("click", () => {
        stop();
        index = Math.min(samples.length - 1, index + 1);
        renderSample();
    });
    slider.addEventListener("input", () => {
        stop();
        index = Number(slider.value);
        renderSample();
    });
    speedSelect.addEventListener("change", () => {
        if (timer) {
            stop();
            play();
        }
    });
    timeInput.addEventListener("change", () => jumpToElapsed(timeInput.value));
    eventSelect.addEventListener("change", () => eventSelect.value && jumpToElapsed(eventSelect.value));
    [search, invalidOnly, changedOnly].forEach((node) => node.addEventListener("input", () => samples.length && renderSample()));
    resetMinMax.addEventListener("click", () => {
        rebuildMinMax();
        if (samples.length) renderSample();
    });
    toggleRadix.addEventListener("click", () => {
        decimalRaw = !decimalRaw;
        toggleRadix.textContent = decimalRaw ? "Decimal" : "Hex";
        if (samples.length) renderSample();
    });
    copyFrame.addEventListener("click", () => {
        if (navigator.clipboard && rawFrame.textContent) navigator.clipboard.writeText(rawFrame.textContent);
    });
    downloadSample.addEventListener("click", () => {
        if (!samples.length) return;
        const blob = new Blob([JSON.stringify(samples[index], null, 2)], { type: "application/json" });
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = `${samples[index].session_id || "sample"}-${samples[index].seq}.json`;
        link.click();
        URL.revokeObjectURL(link.href);
    });

    if (sessionSelect.options.length) loadSession();
})();
