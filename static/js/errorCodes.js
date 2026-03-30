async function fetchErrorCodesData() {
    try {
        const response = await fetch("/analyze", {
            method: "GET",
            headers: { "Content-Type": "application/json" },
        });

        if (!response.ok) {
            const errorData = await response.json();
            document.getElementById("errorCodes").innerHTML =
                `<p style="color:red;">⚠️ ${errorData.error || "Không thể tải dữ liệu."}</p>`;
            return;
        }

        const data = await response.json();
        const alertList = data.alerts;
        const container = document.getElementById("errorCodes");

        if (!alertList || alertList.length === 0) {
            container.innerHTML = "<p>✅ Không có lỗi nào.</p>";
            return;
        }

        container.innerHTML = `<ul>${alertList.map((item) => `<li>${item}</li>`).join("")}</ul>`;
    } catch (error) {
        document.getElementById("errorCodes").innerHTML =
            `<p style="color:red;">❌ Lỗi kết nối: ${error.message}</p>`;
    }
}

fetchErrorCodesData();
setInterval(fetchErrorCodesData, 5000);
