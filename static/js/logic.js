let latestData = {};

// History for temperature chart
let historyData = {
    labels: [],
    temp: [],
};

let chartInstance = null;

// Create chart canvas automatically
function createChart() {
    const dataBox = document.getElementById("dataBox");

    const canvas = document.createElement("canvas");
    canvas.id = "statsChart";
    canvas.style.height = "300px";

    dataBox.appendChild(canvas);

    chartInstance = new Chart(canvas, {
        type: "line",
        data: {
            labels: historyData.labels,
            datasets: [
                {
                    label: "Engine Temp (°C)",
                    data: historyData.temp,
                    borderColor: "red",
                    borderWidth: 2,
                    tension: 0.3,
                },
            ],
        },
        options: {
            layout: {
                padding: 5,
            },

            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: {
                        color: "white", // Màu chữ legend
                    },
                },
            },
            scales: {
                x: {
                    ticks: {
                        color: "white", // Màu chữ trục X
                    },
                },
                y: {
                    ticks: {
                        color: "white", // Màu chữ trục Y
                    },
                },
            },
        },
    });
}

async function getData() {
    try {
        let response = await fetch("/analyze");
        let data = await response.json();

        if (!data.error) {
            latestData = data;

            // 🔹 Update status
            document.getElementById("status").innerText =
                "🟢 Live Data Connected";

            // 🔹 Update vehicle info text
            document.getElementById("vehicleInfo").innerHTML = `
                🔋 Battery: ${data.battery_voltage ?? "N/A"} V<br>
                ⚡ Alternator: ${data.alternator_voltage ?? "N/A"} V<br>
                🌡 Temp: ${data.temperature ?? "N/A"} °C<br>
                ⛽ Fuel Inst: ${data.fuel_instant ?? "N/A"} L/100km<br>
                ⛽ Fuel Avg: ${data.fuel_avg ?? "N/A"} L/100km<br>
                🛞 Odometer: ${data.odometer ?? "N/A"} km
            `;

            // 🔹 Add data to history
            let now = new Date().toLocaleTimeString();
            historyData.labels.push(now);
            historyData.temp.push(data.temperature);

            if (historyData.labels.length > 10) {
                historyData.labels.shift();
                historyData.temp.shift();
            }

            // 🔹 Create chart first time
            if (!chartInstance) {
                createChart();
            } else {
                chartInstance.data.labels = historyData.labels;
                chartInstance.data.datasets[0].data = historyData.temp;
                chartInstance.update();
            }
        }
    } catch (e) {
        console.error("Error fetching data:", e);
        document.getElementById("status").innerText = "🔴 Connection Error";
    }
}

// Auto update every 5 seconds
setInterval(getData, 5000);
getData();
