const BASE_URL = "https://your-domain.com"; // 🔁 Replace with your actual web URL

async function fetchMaintenanceData() {
    try {
        const response = await fetch(`${BASE_URL}/analyze`, {
            method: "GET",
            headers: {
                "Content-Type": "application/json",
            },
        });

        if (!response.ok) {
            const errorData = await response.json();
            document.getElementById("maintenance").innerHTML =
                `<p style="color:red;">⚠️ ${errorData.error || "Không thể tải dữ liệu."}</p>`;
            return;
        }

        const data = await response.json();
        const maintenanceList = data.maintenance;
        const container = document.getElementById("maintenance");

        if (!maintenanceList || maintenanceList.length === 0) {
            container.innerHTML = "<p>✅ Không có hạng mục bảo dưỡng nào.</p>";
            return;
        }

        const html = maintenanceList.map((item) => `<li>${item}</li>`).join("");

        container.innerHTML = `<ul>${html}</ul>`;
    } catch (error) {
        document.getElementById("maintenance").innerHTML =
            `<p style="color:red;">❌ Lỗi kết nối: ${error.message}</p>`;
    }
}

fetchMaintenanceData();
setInterval(fetchMaintenanceData, 5000);
