(function () {
    document.querySelectorAll(".js-analysis-rerun").forEach((button) => {
        const state = button.parentElement.querySelector(".js-analysis-state");
        button.addEventListener("click", async () => {
            button.disabled = true;
            state.textContent = "Queued";
            await new Promise((resolve) => window.setTimeout(resolve, 250));
            state.textContent = "Processing";
            try {
                const payload = await window.drisafeApi.rerunAnalysis(button.dataset.session);
                state.textContent = payload.state === "completed" ? "Completed" : payload.state;
            } catch (error) {
                state.textContent = "Failed";
                state.title = error.message;
            } finally {
                button.disabled = false;
            }
        });
    });
})();
