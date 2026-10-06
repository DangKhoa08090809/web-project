(function () {
    async function request(url, options) {
        const response = await fetch(url, {
            credentials: "same-origin",
            cache: "no-store",
            ...options,
            headers: {
                "Accept": "application/json",
                ...(options && options.headers ? options.headers : {}),
            },
        });
        let payload = null;
        try {
            payload = await response.json();
        } catch (error) {
            payload = {};
        }
        if (!response.ok) {
            const message = (payload.error && payload.error.message) || payload.error || response.statusText || "Request failed";
            throw new Error(typeof message === "string" ? message : JSON.stringify(message));
        }
        return payload;
    }

    function csrfToken() {
        const token = document.querySelector("input[name='csrf_token']");
        return token ? token.value : "";
    }

    window.drisafeApi = {
        sessions(query) {
            const params = query ? `?${new URLSearchParams(query)}` : "";
            return request(`/api/sessions${params}`);
        },
        sessionSamples(sessionId, options) {
            const params = new URLSearchParams(options || {});
            const suffix = params.toString() ? `?${params}` : "";
            return request(`/api/sessions/${sessionId}/samples${suffix}`);
        },
        sessionStatistics(sessionId) {
            return request(`/api/sessions/${sessionId}/statistics`);
        },
        sessionAnalysis(sessionId) {
            return request(`/api/sessions/${sessionId}/analysis`);
        },
        anomalyEvents(sessionId) {
            return request(`/api/sessions/${sessionId}/events`);
        },
        devices() {
            return request("/api/devices");
        },
        rerunAnalysis(sessionId) {
            return request(`/api/sessions/${sessionId}/analysis/re-run`, {
                method: "POST",
                headers: { "X-CSRFToken": csrfToken() },
            });
        },
        exportCsvUrl(sessionId) {
            return `/sessions/${sessionId}.csv`;
        },
        exportJsonUrl(sessionId) {
            return `/sessions/${sessionId}.json`;
        },
    };
})();
