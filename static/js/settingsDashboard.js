(function () {
    const root = document.getElementById("dashboard-settings");
    if (!root) return;
    const form = root.querySelector("form");
    const state = document.getElementById("settings-state");
    const reset = document.getElementById("reset-settings");
    const key = "drisafe.dashboard.preferences";
    const defaults = {
        temperatureUnit: "C",
        pressureUnit: "kPa",
        timeFormat: "24h",
        dateFormat: "yyyy-mm-dd",
        chartDensity: "1200",
        defaultWindow: "all",
        defaultSignals: "rpm,tps,ect,battery",
        theme: "system",
        language: "en",
        diagnosticDisclaimer: true,
    };

    function values() {
        return Object.fromEntries(new FormData(form).entries());
    }

    function apply(preferences) {
        Object.entries(preferences).forEach(([name, value]) => {
            const field = form.elements[name];
            if (!field) return;
            if (field.type === "checkbox") field.checked = Boolean(value);
            else field.value = value;
        });
    }

    function load() {
        try {
            apply({ ...defaults, ...JSON.parse(localStorage.getItem(key) || "{}") });
        } catch (error) {
            apply(defaults);
        }
    }

    form.addEventListener("submit", (event) => {
        event.preventDefault();
        const preferences = { ...defaults, ...values(), diagnosticDisclaimer: form.elements.diagnosticDisclaimer.checked };
        localStorage.setItem(key, JSON.stringify(preferences));
        state.textContent = "Saved";
        window.setTimeout(() => state.textContent = "", 1800);
    });

    reset.addEventListener("click", () => {
        localStorage.removeItem(key);
        apply(defaults);
        state.textContent = "Reset";
        window.setTimeout(() => state.textContent = "", 1800);
    });

    load();
})();
