(function () {
    const root = document.getElementById("session-workbench");
    if (!root) return;

    const state = document.getElementById("chart-state");
    const chartsNode = document.getElementById("charts");
    const togglesNode = document.getElementById("signal-toggles");
    const resetButton = document.getElementById("reset-zoom");
    const retryChartsButton = document.getElementById("retry-charts");
    const chartDownsampleNote = document.getElementById("chart-downsample-note");
    const eventDetail = document.getElementById("event-detail");
    const timelineState = document.getElementById("anomaly-timeline-state");
    const timelineViewport = document.getElementById("anomaly-timeline-viewport");
    const timelineTrack = document.getElementById("anomaly-timeline-track");
    const timelineCard = document.getElementById("anomaly-timeline-card");
    const charts = [];
    const MAX_TIMELINE_BUCKETS = 64;
    const MIN_TRACK_WIDTH = 960;
    const MAX_TRACK_WIDTH = 3600;
    let payload = null;
    let availableChartParameters = [];
    let activeRange = null;
    let chartsInitialized = false;
    let chartsInitializing = false;
    let chartPluginRegistered = false;
    let controlsBound = false;
    let timelineBuckets = [];
    let selectedTimelineSegment = null;

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
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const remaining = seconds % 60;
        if (hours) {
            return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remaining).padStart(2, "0")}`;
        }
        return `${String(minutes).padStart(2, "0")}:${String(remaining).padStart(2, "0")}`;
    }

    function visibleKeys() {
        return Array.from(togglesNode.querySelectorAll("input:checked")).map((input) => input.value);
    }

    function parameterList() {
        const parameters = payload.parameters.filter((parameter) => parameter.key !== "tps_voltage");
        if (payload.samples.some((sample) => typeof sample.anomaly_score === "number")) {
            parameters.push({ key: "anomaly_score", label: "Anomaly score", unit: "", precision: 2 });
        }
        return parameters;
    }

    function chartableParameters() {
        return parameterList().filter((parameter) => payload.samples.some((sample) => {
            const value = sample[parameter.key];
            return typeof value === "number" && Number.isFinite(value);
        }));
    }

    function createToggle(parameter, checked) {
        const label = document.createElement("label");
        label.className = "toggle-pill";
        const input = document.createElement("input");
        input.type = "checkbox";
        input.value = parameter.key;
        input.checked = checked;
        input.addEventListener("change", () => {
            try {
                renderCharts();
            } catch (error) {
                clearChartContent();
                chartsInitialized = false;
                showChartState("Unable to load chart data.", true);
            }
        });
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

    function renderCharts() {
        clearCharts();
        const keys = visibleKeys();
        const selected = availableChartParameters.filter((parameter) => keys.includes(parameter.key));
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
            const title = document.createElement("strong");
            title.textContent = parameter.label;
            const unit = document.createElement("span");
            unit.textContent = parameter.unit || "score";
            heading.append(title, unit);
            const canvas = document.createElement("canvas");
            panel.append(heading, canvas);
            chartsNode.append(panel);

            const xScale = {
                type: "linear",
                title: { display: true, text: "Elapsed time from session start" },
                ticks: { callback: formatSeconds },
            };
            if (activeRange) {
                xScale.min = activeRange.start;
                xScale.max = activeRange.end;
            }
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
                        x: xScale,
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
        activeRange = { start: left, end: right };
        charts.forEach((chart) => {
            chart.options.scales.x.min = left;
            chart.options.scales.x.max = right;
            chart.update("none");
        });
    }

    function resetRange() {
        activeRange = null;
        charts.forEach((chart) => {
            delete chart.options.scales.x.min;
            delete chart.options.scales.x.max;
            if (typeof chart.resetZoom === "function") chart.resetZoom();
            else chart.update("none");
        });
    }

    function clearChartContent() {
        clearCharts();
        togglesNode.textContent = "";
        availableChartParameters = [];
    }

    function showChartState(message, retryable = false) {
        state.hidden = false;
        state.textContent = message;
        if (retryChartsButton) retryChartsButton.hidden = !retryable;
    }

    function nextFrame() {
        return new Promise((resolve) => window.requestAnimationFrame(resolve));
    }

    async function initializeCharts() {
        if (chartsInitialized || chartsInitializing) return;
        if (!payload) {
            showChartState("Loading charts...");
            return;
        }

        chartsInitializing = true;
        showChartState("Loading charts...");
        await nextFrame();
        if (!root.open) {
            chartsInitializing = false;
            return;
        }

        try {
            if (typeof window.Chart !== "function") throw new Error("Chart library is unavailable");
            if (window.ChartZoom && !chartPluginRegistered) {
                Chart.register(window.ChartZoom);
                chartPluginRegistered = true;
            }
            availableChartParameters = chartableParameters();
            if (!availableChartParameters.length) {
                chartsInitialized = true;
                showChartState("No chartable telemetry is available for this session.");
                return;
            }
            togglesNode.textContent = "";
            availableChartParameters.forEach((parameter, index) => {
                togglesNode.append(createToggle(parameter, index < 4 || parameter.key === "anomaly_score"));
            });
            renderCharts();
            chartsInitialized = true;
        } catch (error) {
            clearChartContent();
            showChartState("Unable to load chart data.", true);
        } finally {
            chartsInitializing = false;
        }
    }

    function requestCharts() {
        if (payload) initializeCharts();
        else showChartState("Loading charts...");
    }

    function handleChartDisclosure() {
        if (!root.open) return;
        if (chartsInitialized) {
            charts.forEach((chart) => {
                if (typeof chart.resize === "function") chart.resize();
            });
            return;
        }
        requestCharts();
    }

    function timelineDuration() {
        const values = [
            payload && payload.session && payload.session.duration_seconds,
            ...(payload ? payload.samples.map((sample) => sample.elapsed_seconds) : []),
            ...(payload ? payload.events.flatMap((event) => [event.start_elapsed, event.end_elapsed]) : []),
        ];
        return Math.max(1, ...values.map((value) => Number(value)).filter(Number.isFinite));
    }

    function severityRank(event) {
        const severity = String(event.severity || "").toLowerCase();
        if (severity.includes("high")) return 3;
        if (severity.includes("attention")) return 2;
        return 1;
    }

    function timelineSeverity(event) {
        const rank = severityRank(event);
        return rank === 3 ? "high" : rank === 2 ? "attention" : "monitor";
    }

    function bucketCountFor(duration) {
        return Math.min(MAX_TIMELINE_BUCKETS, Math.max(24, Math.ceil(duration / 20)));
    }

    function buildTimelineBuckets(events, duration) {
        const count = bucketCountFor(duration);
        const buckets = Array.from({ length: count }, (_, index) => ({ index, events: [], primary: null }));
        events.forEach((event) => {
            const start = Math.max(0, Math.min(duration, Number(event.start_elapsed) || 0));
            const end = Math.max(start, Math.min(duration, Number(event.end_elapsed) || start));
            const first = Math.min(count - 1, Math.floor((start / duration) * count));
            const last = Math.min(count - 1, Math.max(first, Math.ceil((end / duration) * count) - 1));
            for (let index = first; index <= last; index += 1) {
                buckets[index].events.push(event);
            }
        });
        buckets.forEach((bucket) => {
            bucket.primary = bucket.events.reduce((best, event) => {
                if (!best) return event;
                const bestRank = [severityRank(best), Number(best.max_anomaly_score) || 0];
                const eventRank = [severityRank(event), Number(event.max_anomaly_score) || 0];
                return eventRank[0] > bestRank[0] || (eventRank[0] === bestRank[0] && eventRank[1] > bestRank[1]) ? event : best;
            }, null);
        });
        return buckets;
    }

    function timelineTrackWidth(duration) {
        if (duration <= 300) return null;
        return Math.min(MAX_TRACK_WIDTH, Math.max(MIN_TRACK_WIDTH, Math.ceil(duration / 60) * 96));
    }

    function bucketRange(bucket, duration) {
        const length = duration / timelineBuckets.length;
        return {
            start: bucket.index * length,
            end: Math.min(duration, (bucket.index + 1) * length),
        };
    }

    function bucketLabel(bucket, duration) {
        const range = bucketRange(bucket, duration);
        const timeRange = `${formatSeconds(range.start)}-${formatSeconds(range.end)}`;
        if (!bucket.events.length) return `Normal data from ${timeRange}.`;
        const intervalLabel = bucket.events.length === 1 ? "anomaly interval" : "anomaly intervals";
        return `${bucket.events.length} grouped ${intervalLabel} from ${timeRange}.`;
    }

    function renderTimeline() {
        if (!timelineViewport || !timelineTrack || !timelineState) return;
        const duration = timelineDuration();
        timelineBuckets = buildTimelineBuckets(payload.events, duration);
        const width = timelineTrackWidth(duration);
        timelineTrack.textContent = "";
        timelineTrack.style.width = width ? `${width}px` : "";
        timelineTrack.style.setProperty("--timeline-bucket-count", timelineBuckets.length);

        const segments = document.createElement("div");
        segments.className = "anomaly-timeline-segments";
        timelineBuckets.forEach((bucket) => {
            if (!bucket.events.length) {
                const normal = document.createElement("span");
                normal.className = "timeline-segment normal";
                normal.setAttribute("aria-hidden", "true");
                segments.append(normal);
                return;
            }
            const segment = document.createElement("button");
            segment.type = "button";
            segment.className = `timeline-segment ${timelineSeverity(bucket.primary)}`;
            segment.setAttribute("aria-label", bucketLabel(bucket, duration));
            segment.title = bucketLabel(bucket, duration);
            segment.addEventListener("click", () => selectTimelineBucket(bucket, duration));
            bucket.segment = segment;
            segments.append(segment);
        });

        const axis = document.createElement("div");
        axis.className = "timeline-axis";
        const labelCount = duration < 600 ? 5 : 6;
        for (let index = 0; index < labelCount; index += 1) {
            const label = document.createElement("span");
            const position = index / (labelCount - 1);
            label.className = "timeline-axis-label";
            label.style.left = `${position * 100}%`;
            label.textContent = formatSeconds(duration * position);
            axis.append(label);
        }

        timelineTrack.append(segments, axis);
        timelineViewport.hidden = false;
        if (payload.events.length) {
            timelineState.hidden = true;
        } else {
            timelineState.hidden = false;
            timelineState.textContent = "No grouped anomaly events were detected in available telemetry.";
        }
    }

    function showEvent(event, bucket = null, rangeLabel = "") {
        const intervalCount = bucket ? bucket.events.length : 1;
        setRange(Math.max(0, event.start_elapsed - 5), event.end_elapsed + 5);
        eventDetail.textContent = "";
        const title = document.createElement("strong");
        title.textContent = event.title;
        const meta = document.createElement("p");
        const interval = rangeLabel || `${formatSeconds(event.start_elapsed)}-${formatSeconds(event.end_elapsed)}`;
        meta.textContent = `${event.severity} · ${interval} · max score ${event.max_anomaly_score.toFixed(2)} · ${event.abnormal_sample_count} abnormal samples`;
        const signals = document.createElement("p");
        signals.textContent = `Main unusual signals: ${event.signals.join(", ")}`;
        eventDetail.append(title, meta, signals);
        if (intervalCount > 1) {
            const grouped = document.createElement("p");
            grouped.className = "muted";
            grouped.textContent = `${intervalCount} grouped anomaly intervals fall within this time segment.`;
            const label = document.createElement("label");
            label.className = "event-choice";
            const labelText = document.createElement("span");
            labelText.textContent = "Choose an anomaly interval";
            const select = document.createElement("select");
            bucket.events.forEach((candidate) => {
                const option = document.createElement("option");
                option.value = candidate.id;
                option.selected = candidate.id === event.id;
                option.textContent = `${formatSeconds(candidate.start_elapsed)}-${formatSeconds(candidate.end_elapsed)} | ${candidate.severity}`;
                select.append(option);
            });
            select.addEventListener("change", () => {
                const selected = bucket.events.find((candidate) => candidate.id === select.value);
                if (selected) showEvent(selected, bucket, rangeLabel);
            });
            label.append(labelText, select);
            eventDetail.append(grouped, label);
        }
        const suggestion = document.createElement("p");
        suggestion.textContent = event.suggestion;
        eventDetail.append(suggestion);
    }

    function selectTimelineBucket(bucket, duration, shouldScroll = false, eventToShow = null) {
        if (!bucket || !bucket.primary) return;
        if (selectedTimelineSegment) selectedTimelineSegment.removeAttribute("aria-pressed");
        selectedTimelineSegment = bucket.segment || null;
        if (selectedTimelineSegment) selectedTimelineSegment.setAttribute("aria-pressed", "true");
        const range = bucketRange(bucket, duration);
        showEvent(eventToShow || bucket.primary, bucket, `${formatSeconds(range.start)}-${formatSeconds(range.end)}`);
        if (shouldScroll && bucket.segment) {
            bucket.segment.scrollIntoView({ block: "nearest", inline: "center" });
        }
    }

    function focusEventFromHash() {
        const eventId = decodeURIComponent(window.location.hash.slice(1));
        if (!eventId || !payload) return;
        const bucket = timelineBuckets.find((candidate) => candidate.events.some((event) => event.id === eventId));
        if (bucket) {
            selectTimelineBucket(bucket, timelineDuration(), true, bucket.events.find((event) => event.id === eventId));
            if (timelineCard) timelineCard.scrollIntoView({ block: "start" });
        }
    }

    function bindEvents() {
        if (controlsBound) return;
        controlsBound = true;
        document.querySelectorAll("[data-window]").forEach((button) => {
            button.addEventListener("click", () => {
                const value = button.dataset.window;
                if (value === "all") {
                    resetRange();
                    return;
                }
                setRange(0, Number(value));
            });
        });
        if (resetButton) resetButton.addEventListener("click", resetRange);
        if (retryChartsButton) {
            retryChartsButton.addEventListener("click", () => {
                chartsInitialized = false;
                clearChartContent();
                if (payload) initializeCharts();
                else load();
            });
        }
        root.addEventListener("toggle", handleChartDisclosure);
        window.addEventListener("hashchange", focusEventFromHash);
    }

    async function load() {
        try {
            payload = await fetch(root.dataset.samplesUrl, { credentials: "same-origin", cache: "no-store" }).then((response) => {
                if (!response.ok) throw new Error("Session samples are unavailable");
                return response.json();
            });
            renderTimeline();
            focusEventFromHash();
            if (payload.downsampled && chartDownsampleNote) {
                chartDownsampleNote.hidden = false;
                chartDownsampleNote.textContent = `Charts are downsampled to ${payload.sample_count} of ${payload.source_count} records.`;
            }
            if (root.open) requestCharts();
        } catch (error) {
            const message = error.message || "Unable to load session samples.";
            showChartState("Unable to load chart data.", true);
            if (timelineState) {
                timelineState.hidden = false;
                timelineState.textContent = message;
            }
            if (timelineViewport) timelineViewport.hidden = true;
        }
    }

    bindEvents();
    if (document.readyState === "complete") load();
    else window.addEventListener("load", load, { once: true });
})();
