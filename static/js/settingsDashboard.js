(function () {
    const root = document.getElementById("dashboard-settings");
    if (!root) return;
    const form = root.querySelector("form");
    const state = document.getElementById("settings-state");
    const reset = document.getElementById("reset-settings");
    const key = "drisafe.dashboard.preferences";
    const defaults = {
        language: "vi",
    };

    function values() {
        return Object.fromEntries(new FormData(form).entries());
    }

    function apply(preferences) {
        Object.entries(preferences).forEach(([name, value]) => {
            const field = form.elements[name];
            if (!field) return;
            field.value = value;
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
        const preferences = { ...defaults, ...values() };
        localStorage.setItem(key, JSON.stringify(preferences));
        if (window.drisafeI18n) window.drisafeI18n.setLanguage(preferences.language);
        state.textContent = "Saved";
        window.setTimeout(() => state.textContent = "", 1800);
    });

    reset.addEventListener("click", () => {
        localStorage.removeItem(key);
        apply(defaults);
        if (window.drisafeI18n) window.drisafeI18n.setLanguage(defaults.language);
        state.textContent = "Reset";
        window.setTimeout(() => state.textContent = "", 1800);
    });

    load();
})();
