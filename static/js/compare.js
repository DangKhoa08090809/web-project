(function () {
    const baselineSelect = document.getElementById("baseline-session");
    const comparisonSelect = document.getElementById("comparison-session");
    const parameterSelect = document.getElementById("compare-parameter");
    const button = document.getElementById("run-compare");
    const empty = document.getElementById("compare-empty");
    const results = document.getElementById("compare-results");
    const metricsBody = document.getElementById("compare-metrics");
    const title = document.getElementById("compare-title");
    const canvas = document.getElementById("compare-chart");
    if (!baselineSelect || !comparisonSelect) return;

    let chart = null;
    let payload = null;

    function formatMetric(value, metric) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
        const precision = Number(metric.precision || 0);
        const suffix = metric.unit ? ` ${metric.unit}` : "";
        return `${Number(value).toFixed(precision)}${suffix}`;
    }

    function fillParameters(parameters) {
        const selected = parameterSelect.value;
        parameterSelect.textContent = "";
        parameters.forEach((parameter) => {
            const option = document.createElement("option");
            option.value = parameter.key;
            option.textContent = parameter.label;
            parameterSelect.append(option);
        });
        if ([...parameterSelect.options].some((option) => option.value === selected)) {
            parameterSelect.value = selected;
        }
    }

    function renderMetrics() {
        metricsBody.textContent = "";
        payload.metrics.forEach((metric) => {
            const row = document.createElement("tr");
            const delta = metric.delta === null || metric.delta === undefined ? "--" : formatMetric(metric.delta, metric);
            row.innerHTML = `<td>${metric.label}</td><td>${formatMetric(metric.baseline, metric)}</td><td>${formatMetric(metric.comparison, metric)}</td><td>${delta}</td>`;
            metricsBody.append(row);
        });
        title.textContent = `${payload.baseline.session_id} versus ${payload.comparison.session_id}`;
    }

    function samplesFor(samples, key) {
        return samples
            .filter((sample) => typeof sample[key] === "number" && Number.isFinite(sample[key]))
            .map((sample) => ({ x: sample.elapsed_seconds, y: sample[key] }));
    }

    function renderChart() {
        const key = parameterSelect.value;
        const parameter = payload.parameters.find((item) => item.key === key);
        if (chart) chart.destroy();
        chart = new Chart(canvas, {
            type: "line",
            data: {
                datasets: [
                    {
                        label: `Baseline · ${payload.baseline.session_id}`,
                        data: samplesFor(payload.baseline_samples, key),
                        borderColor: "#0b65c2",
                        backgroundColor: "#0b65c2",
                        pointRadius: 0,
                        borderWidth: 2,
                    },
                    {
                        label: `Comparison · ${payload.comparison.session_id}`,
                        data: samplesFor(payload.comparison_samples, key),
                        borderColor: "#c2410c",
                        backgroundColor: "#c2410c",
                        pointRadius: 0,
                        borderWidth: 2,
                    },
                ],
            },
            options: {
                animation: false,
                maintainAspectRatio: false,
                parsing: false,
                responsive: true,
                interaction: { mode: "nearest", intersect: false },
                scales: {
                    x: { type: "linear", title: { display: true, text: "Elapsed time from session start" } },
                    y: { title: { display: true, text: parameter ? parameter.unit : "" } },
                },
            },
        });
    }

    async function compare() {
        if (!baselineSelect.value || !comparisonSelect.value || baselineSelect.value === comparisonSelect.value) {
            empty.hidden = false;
            empty.querySelector("p").textContent = "Choose two different uploaded sessions.";
            results.hidden = true;
            return;
        }
        button.disabled = true;
        button.textContent = "Loading";
        try {
            payload = await window.drisafeApi.compare(baselineSelect.value, comparisonSelect.value);
            fillParameters(payload.parameters);
            renderMetrics();
            renderChart();
            empty.hidden = true;
            results.hidden = false;
        } catch (error) {
            empty.hidden = false;
            empty.querySelector("p").textContent = error.message;
            results.hidden = true;
        } finally {
            button.disabled = false;
            button.textContent = "Compare sessions";
        }
    }

    parameterSelect.addEventListener("change", () => payload && renderChart());
    button.addEventListener("click", compare);
    if (baselineSelect.options.length >= 2) compare();
})();
