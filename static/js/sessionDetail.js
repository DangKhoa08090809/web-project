(function () {
    const root = document.getElementById("session-workbench");
    if (!root) return;

    const state = document.getElementById("chart-state");
    const chartsNode = document.getElementById("charts");
    const togglesNode = document.getElementById("signal-toggles");
    const resetButton = document.getElementById("reset-zoom");
    const eventDetail = document.getElementById("event-detail");
    const charts = [];
    let payload = null;

    const colors = {
        rpm: "#0b65c2",
        tps: "#148c62",
        ect: "#c2410c",
        iat: "#7c3aed",
        battery: "#b45309",
        injector_ms: "#be185d",
        ignition_deg: "#475569",
        anomaly_score: "#dc2626",
    };

    function formatSeconds(value) {
        const seconds = Math.max(0, Math.round(Number(value) || 0));
        const minutes = Math.floor(seconds / 60);
        const remaining = seconds % 60;
        return minutes ? `${minutes}:${String(remaining).padStart(2, "0")}` : `${remaining}s`;
    }

    const anomalyBands = {
        id: "anomalyBands",
        beforeDatasetsDraw(chart, args, options) {
            const events = options.events || [];
            const area = chart.chartArea;
            const xScale = chart.scales.x;
            if (!area || !xScale || !events.length) return;
            const ctx = chart.ctx;
            ctx.save();
            ctx.fillStyle = "rgba(220, 38, 38, 0.09)";
            events.forEach((event) => {
                const start = xScale.getPixelForValue(event.start_elapsed);
                const end = xScale.getPixelForValue(Math.max(event.end_elapsed, event.start_elapsed + 1));
                const left = Math.max(area.left, Math.min(start, end));
                const right = Math.min(area.right, Math.max(start, end));
                if (right > left) ctx.fillRect(left, area.top, right - left, area.bottom - area.top);
            });
            ctx.restore();
        },
    };

    function visibleKeys() {
        return Array.from(togglesNode.querySelectorAll("input:checked")).map((input) => input.value);
    }

    function parameterList() {
        const parameters = payload.parameters.slice();
        if (payload.samples.some((sample) => typeof sample.anomaly_score === "number")) {
            parameters.push({ key: "anomaly_score", label: "Anomaly score", unit: "", precision: 2 });
        }
        return parameters;
    }

    function createToggle(parameter, checked) {
        const label = document.createElement("label");
        label.className = "toggle-pill";
        const input = document.createElement("input");
        input.type = "checkbox";
        input.value = parameter.key;
        input.checked = checked;
        input.addEventListener("change", renderCharts);
        label.append(input, document.createTextNode(parameter.label));
        return label;
    }

    function clearCharts() {
        while (charts.length) {
            charts.pop().destroy();
        }
        chartsNode.textContent = "";
    }

    function datasetFor(parameter) {
        return payload.samples
            .filter((sample) => typeof sample[parameter.key] === "number" && Number.isFinite(sample[parameter.key]))
            .map((sample) => ({ x: sample.elapsed_seconds, y: sample[parameter.key] }));
    }

    function renderCharts() {
        clearCharts();
        const keys = visibleKeys();
        const selected = parameterList().filter((parameter) => keys.includes(parameter.key));
        if (!selected.length) {
            state.hidden = false;
            state.textContent = "Select at least one signal to display.";
            return;
        }
        state.hidden = true;
        selected.forEach((parameter) => {
            const panel = document.createElement("article");
            panel.className = "mini-chart";
            const heading = document.createElement("div");
            heading.className = "mini-chart-title";
            heading.innerHTML = `<strong>${parameter.label}</strong><span>${parameter.unit || "score"}</span>`;
            const canvas = document.createElement("canvas");
            panel.append(heading, canvas);
            chartsNode.append(panel);

            const chart = new Chart(canvas, {
                type: "line",
                data: {
                    datasets: [{
                        label: parameter.label,
                        data: datasetFor(parameter),
                        borderColor: colors[parameter.key] || "#0f766e",
                        backgroundColor: colors[parameter.key] || "#0f766e",
                        pointRadius: 0,
                        borderWidth: 2,
                        tension: 0.18,
                    }],
                },
                options: {
                    animation: false,
                    responsive: true,
                    maintainAspectRatio: false,
                    parsing: false,
                    interaction: { mode: "nearest", intersect: false },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                title(items) {
                                    return items.length ? `Elapsed ${formatSeconds(items[0].parsed.x)}` : "";
                                },
                            },
                        },
                        anomalyBands: { events: payload.events },
                        zoom: {
                            pan: { enabled: true, mode: "x" },
                            zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: "x" },
                        },
                    },
                    scales: {
                        x: {
                            type: "linear",
                            title: { display: true, text: "Elapsed time from session start" },
                            ticks: { callback: formatSeconds },
                        },
                        y: {
                            title: { display: true, text: parameter.unit || "score" },
                        },
                    },
                },
                plugins: [anomalyBands],
            });
            charts.push(chart);
        });
    }

    function setRange(start, end) {
        const left = Math.max(0, Number(start) || 0);
        const right = Math.max(left + 1, Number(end) || left + 1);
        charts.forEach((chart) => {
            chart.options.scales.x.min = left;
            chart.options.scales.x.max = right;
            chart.update("none");
        });
    }

    function resetRange() {
        charts.forEach((chart) => {
            delete chart.options.scales.x.min;
            delete chart.options.scales.x.max;
            if (typeof chart.resetZoom === "function") chart.resetZoom();
            else chart.update("none");
        });
    }

    function showEvent(event) {
        setRange(Math.max(0, event.start_elapsed - 5), event.end_elapsed + 5);
        eventDetail.innerHTML = "";
        const title = document.createElement("strong");
        title.textContent = event.title;
        const meta = document.createElement("p");
        meta.textContent = `${event.severity} · ${event.duration_label} · max score ${event.max_anomaly_score.toFixed(2)} · ${event.abnormal_sample_count} abnormal samples`;
        const signals = document.createElement("p");
        signals.textContent = `Main unusual signals: ${event.signals.join(", ")}`;
        const suggestion = document.createElement("p");
        suggestion.textContent = event.suggestion;
        eventDetail.append(title, meta, signals, suggestion);
    }

    function bindEvents() {
        document.querySelectorAll(".event-button").forEach((button) => {
            const event = payload.events.find((item) => item.id === button.id);
            if (!event) return;
            button.addEventListener("click", () => showEvent(event));
        });
        document.querySelectorAll("[data-window]").forEach((button) => {
            button.addEventListener("click", () => {
                const value = button.dataset.window;
                if (value === "all") {
                    resetRange();
                    return;
                }
                const seconds = Number(value);
                setRange(0, seconds);
            });
        });
        resetButton.addEventListener("click", resetRange);
    }

    async function load() {
        try {
            payload = await fetch(root.dataset.samplesUrl, { credentials: "same-origin", cache: "no-store" }).then((response) => {
                if (!response.ok) throw new Error("Session samples are unavailable");
                return response.json();
            });
            if (window.ChartZoom) Chart.register(window.ChartZoom);
            const parameters = parameterList();
            togglesNode.textContent = "";
            parameters.forEach((parameter, index) => togglesNode.append(createToggle(parameter, index < 4 || parameter.key === "anomaly_score")));
            renderCharts();
            bindEvents();
            if (payload.downsampled) {
                const note = document.createElement("p");
                note.className = "muted";
                note.textContent = `Charts are downsampled to ${payload.sample_count} of ${payload.source_count} records.`;
                root.append(note);
            }
        } catch (error) {
            state.hidden = false;
            state.textContent = error.message || "Unable to load session samples.";
        }
    }

    window.addEventListener("load", load);
})();
