import { InitWebSocket, setMessageHandler, sendData } from "./webSocket.js";

const API_BASE = "http://127.0.0.1:5000";

const wsStatus = document.getElementById("ws");
const vehicleStatus = document.getElementById("vehicleStatus");

const btnArea = document.getElementById("btnArea");
const clearDTC = document.getElementById("clearDTC");

//clearDTC.addEventListener("click", function () {
//    sendData("clear_dtc");
//});

const errorsField = document.getElementById("errorCodes");
const mil = document.getElementById("DistanceTraveledWithMIL");

function handleWebSocketMessage(wsMessage) {
    if (!wsMessage) {
        wsStatus.style.fill = "red";
        return;
    }

    wsStatus.style.fill = "#00ff00";
    vehicleStatus.style.fill = wsMessage.vehicleStatus ? "#00ff00" : "red";

    let DTCs = wsMessage.DTCs;
    if (wsMessage.vehicleStatus == false) {
        errorsField.innerHTML = "Not Connected to the Vehicle.";
        btnArea.style.display = "none";
    } else if (DTCs) {
        errorsField.innerHTML = DTCs;
        btnArea.style.display = "flex";
    } else {
        errorsField.innerHTML = "No errors detected.";
        btnArea.style.display = "none";
    }
}

const errorElement = document.getElementById("errorCodes");

errorElement.addEventListener("click", analyzeError);

async function analyzeError() {
    try {
        const response = await fetch(`${API_BASE}/analyze`);

        const result = await response.json();

        if (result.error) {
            console.log(result.error);
            return;
        }

        displayAnalysis(result);
    } catch (error) {
        console.error("Error:", error);
    }
}

function displayAnalysis(result) {
    const alertsBox = document.getElementById("alertsBox");
    const maintenanceBox = document.getElementById("maintenanceBox");

    alertsBox.innerHTML = "";
    maintenanceBox.innerHTML = "";

    result.alerts.forEach((alert) => {
        alertsBox.innerHTML += `<p>${alert}</p>`;
    });

    result.maintenance.forEach((item) => {
        maintenanceBox.innerHTML += `<p>${item}</p>`;
    });
}

setInterval(analyzeError, 2000); // mỗi 2 giây lấy dữ liệu

//setMessageHandler(handleWebSocketMessage);
//InitWebSocket();
