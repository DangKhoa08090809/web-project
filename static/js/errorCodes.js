const API_BASE = "http://127.0.0.1:5000";

const errorsField = document.getElementById("errorCodes");
const mil = document.getElementById("DistanceTraveledWithMIL");
const btnArea = document.getElementById("btnArea");
const wsIcon = document.getElementById("ws");
const vehicleIcon = document.getElementById("vehicleStatus");

async function fetchAndDisplay() {
    try {
        const response = await fetch(`${API_BASE}/analyze`);

        if (!response.ok) {
            setDisconnectedState("No data yet from simulator...");
            return;
        }

        const result = await response.json();

        if (result.error) {
            setDisconnectedState(result.error);
            return;
        }

        wsIcon.style.fill = "#00ff00";
        vehicleIcon.style.fill = "#00ff00";

        errorsField.innerHTML = result.alerts
            .map((a) => `<p>${a}</p>`)
            .join("");

        mil.textContent = result.odometer.toLocaleString();

        btnArea.style.display = "flex";
    } catch (err) {
        setDisconnectedState("Cannot connect to server.");
        console.error("Fetch error:", err);
    }
}

function setDisconnectedState(message) {
    wsIcon.style.fill = "red";
    vehicleIcon.style.fill = "red";
    errorsField.textContent = message;
    btnArea.style.display = "none";
    mil.textContent = "0";
}

fetchAndDisplay();
setInterval(fetchAndDisplay, 2000);
