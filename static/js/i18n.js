(function () {
    const languageKey = "drisafe.language";
    const settingsKey = "drisafe.dashboard.preferences";
    const supportedLanguages = new Set(["en", "vi"]);
    const originalText = new WeakMap();
    const originalAttributes = new WeakMap();
    let translatedTextNodes = new WeakSet();
    let applying = false;
    let originalTitle = document.title;

    const vi = {
        "Language": "Ngôn ngữ",
        "English": "Tiếng Anh",
        "Overview": "Tổng quan",
        "Sessions": "Phiên",
        "AI Analysis": "Phân tích AI",
        "Tool": "Công cụ",
        "Devices": "Thiết bị",
        "Settings": "Cài đặt",
        "Log out": "Đăng xuất",
        "Browse sessions": "Xem phiên",
        "Historical ECU analysis": "Phân tích ECU lịch sử",
        "Uploaded ride sessions, cautious anomaly screening, and ECU reader synchronization status.": "Các phiên đã tải lên, sàng lọc bất thường thận trọng và trạng thái đồng bộ đầu đọc ECU.",
        "Total uploaded sessions": "Tổng phiên đã tải lên",
        "Durable uploaded ride logs": "Nhật ký chuyến chạy đã tải lên",
        "Sessions analyzed": "Phiên đã phân tích",
        "Completed local screening": "Đã hoàn tất sàng lọc cục bộ",
        "Sessions requiring attention": "Phiên cần chú ý",
        "Requires attention or high anomaly": "Cần chú ý hoặc bất thường cao",
        "Recent anomaly events": "Sự kiện bất thường gần đây",
        "Grouped events in recent sessions": "Sự kiện được nhóm trong các phiên gần đây",
        "Last synchronization time": "Lần đồng bộ gần nhất",
        "Most recent device upload/contact": "Lần thiết bị tải lên/liên hệ gần nhất",
        "Registered devices": "Thiết bị đã đăng ký",
        "Enabled and disabled readers": "Đầu đọc đang bật và đã tắt",
        "Latest diagnostic result": "Kết quả chẩn đoán mới nhất",
        "Vehicle condition": "Tình trạng xe",
        "View details": "Xem chi tiết",
        "Session details will show evidence when a finding is available.": "Chi tiết phiên sẽ hiển thị bằng chứng khi có khuyến nghị.",
        "Duration": "Thời lượng",
        "Samples": "Mẫu",
        "Maximum anomaly score": "Điểm bất thường cao nhất",
        "Anomalous sample ratio": "Tỷ lệ mẫu bất thường",
        "Grouped events": "Sự kiện đã nhóm",
        "No grouped anomaly events were detected in recent uploaded sessions.": "Không phát hiện sự kiện bất thường được nhóm trong các phiên tải lên gần đây.",
        "Recent diagnoses": "Chẩn đoán gần đây",
        "Analyzed sessions": "Phiên đã phân tích",
        "No analyzed sessions yet.": "Chưa có phiên đã phân tích.",
        "Device synchronization": "Đồng bộ thiết bị",
        "Reader state": "Trạng thái đầu đọc",
        "No registered devices.": "Chưa có thiết bị đã đăng ký.",
        "No uploaded sessions yet": "Chưa có phiên tải lên",
        "Durable ride sessions will appear here after an ECU reader uploads stored data.": "Phiên chạy sẽ xuất hiện ở đây sau khi đầu đọc ECU tải dữ liệu đã lưu.",
        "Register a device": "Đăng ký thiết bị",
        "Uploaded ride history": "Lịch sử phiên đã tải lên",
        "Browse stored ECU sessions. This is historical uploaded data, not a live driving stream.": "Duyệt các phiên ECU đã lưu. Đây là dữ liệu lịch sử đã tải lên, không phải luồng lái xe trực tiếp.",
        "Search": "Tìm kiếm",
        "Date from": "Từ ngày",
        "Date to": "Đến ngày",
        "Device": "Thiết bị",
        "Vehicle": "Xe",
        "Processing state": "Trạng thái xử lý",
        "Analysis result": "Kết quả phân tích",
        "Processing errors": "Lỗi xử lý",
        "Contains events": "Có sự kiện",
        "Apply filters": "Áp dụng bộ lọc",
        "Reset": "Đặt lại",
        "All devices": "Tất cả thiết bị",
        "All vehicles": "Tất cả xe",
        "Any state": "Bất kỳ trạng thái",
        "Any result": "Bất kỳ kết quả",
        "Normal only": "Chỉ bình thường",
        "Anomalous only": "Chỉ bất thường",
        "Started": "Bắt đầu",
        "Uploaded": "Đã tải lên",
        "Events": "Sự kiện",
        "Anomalous": "Bất thường",
        "Open": "Mở",
        "Notes": "Ghi chú",
        "Re-run analysis": "Chạy lại phân tích",
        "Delete": "Xóa",
        "No uploaded sessions match these filters.": "Không có phiên tải lên nào khớp với bộ lọc.",
        "Application settings": "Cài đặt ứng dụng",
        "Manage local dashboard preferences.": "Quản lý tùy chọn cục bộ của bảng điều khiển.",
        "Save settings": "Lưu cài đặt",
        "All": "Tất cả",
        "30 seconds": "30 giây",
        "1 minute": "1 phút",
        "5 minutes": "5 phút",
        "Saved": "Đã lưu",
        "Technical inspection": "Kiểm tra kỹ thuật",
        "Session playback for uploaded ECU data.": "Phát lại phiên từ dữ liệu ECU đã tải lên.",
        "Uploaded session": "Phiên đã tải lên",
        "Jump to anomaly event": "Đi đến sự kiện bất thường",
        "Jump to timestamp": "Đi đến mốc thời gian",
        "Load session": "Tải phiên",
        "Speed": "Tốc độ",
        "Timeline": "Dòng thời gian",
        "Decoded parameters": "Tham số đã giải mã",
        "Selected sample": "Mẫu đã chọn",
        "Invalid only": "Chỉ không hợp lệ",
        "Changed only": "Chỉ đã thay đổi",
        "Reset min/max": "Đặt lại min/max",
        "Search parameters": "Tìm tham số",
        "Parameter": "Tham số",
        "Value": "Giá trị",
        "Unit": "Đơn vị",
        "Minimum": "Nhỏ nhất",
        "Maximum": "Lớn nhất",
        "Raw field": "Trường thô",
        "Decode": "Giải mã",
        "Validity": "Tính hợp lệ",
        "No session loaded.": "Chưa tải phiên.",
        "Raw frame inspector": "Trình xem frame thô",
        "Frame data": "Dữ liệu frame",
        "Copy raw frame": "Sao chép frame thô",
        "Download sample": "Tải mẫu",
        "Hex": "Hex",
        "Timestamp": "Thời gian",
        "Sequence": "Thứ tự",
        "Frame length": "Độ dài frame",
        "Checksum": "Checksum",
        "Parsed field": "Trường đã tách",
        "No parsed raw fields available.": "Không có trường thô đã tách.",
        "Session header": "Thông tin phiên",
        "Download CSV": "Tải CSV",
        "Download JSON": "Tải JSON",
        "Start": "Bắt đầu",
        "End": "Kết thúc",
        "Sample count": "Số mẫu",
        "Sampling frequency": "Tần số lấy mẫu",
        "Upload time": "Thời gian tải lên",
        "Processing status": "Trạng thái xử lý",
        "Model version": "Phiên bản mô hình",
        "Capture status": "Trạng thái thu thập",
        "Capture completed normally": "Phiên hoàn tất bình thường",
        "Capture interrupted": "Phiên bị gián đoạn",
        "Capture termination unknown": "Không rõ trạng thái kết thúc",
        "Termination reason": "Lý do kết thúc",
        "Normal stop": "Dừng bình thường",
        "Unclean runtime shutdown": "Lần chạy trước kết thúc không bình thường",
        "Unknown": "Không rõ",
        "This capture ended normally.": "Phiên thu thập đã kết thúc bình thường.",
        "This capture was interrupted because the previous runtime ended uncleanly. The displayed data is the portion that was saved successfully.": "Phiên thu thập bị gián đoạn do lần chạy trước kết thúc không bình thường. Dữ liệu hiển thị là phần đã được lưu thành công.",
        "No capture termination metadata was supplied for this historical session.": "Phiên lịch sử này không có metadata về trạng thái kết thúc.",
        "User notes": "Ghi chú người dùng",
        "Save notes": "Lưu ghi chú",
        "Post-ride guidance": "Khuyến nghị sau chuyến đi",
        "Maintenance findings": "Khuyến nghị bảo dưỡng",
        "Technical evidence": "Dữ liệu kỹ thuật",
        "Code": "Mã",
        "Direction": "Xu hướng",
        "Session median": "Trung vị phiên",
        "Session max": "Giá trị lớn nhất của phiên",
        "Baseline": "Đường cơ sở",
        "Baseline median": "Trung vị đường cơ sở",
        "Baseline p95": "P95 đường cơ sở",
        "Deviation": "Mức lệch",
        "Analysis summary": "Tóm tắt phân tích",
        "Statistical summary": "Tóm tắt thống kê",
        "Anomaly timeline": "Dòng thời gian bất thường",
        "Intervals over time": "Các khoảng theo thời gian",
        "Anomaly timeline summary": "Tóm tắt dòng thời gian bất thường",
        "Anomalous intervals": "Khoảng bất thường",
        "Anomaly ratio": "Tỷ lệ bất thường",
        "Loading grouped anomaly intervals...": "Đang tải các khoảng bất thường đã nhóm...",
        "Anomaly timeline. Scroll horizontally to inspect time ranges.": "Dòng thời gian bất thường. Cuộn ngang để xem các khoảng thời gian.",
        "Interval details": "Chi tiết khoảng thời gian",
        "No anomaly interval selected.": "Chưa chọn khoảng bất thường.",
        "Choose an anomaly interval": "Chọn một khoảng bất thường",
        "No grouped anomaly events were detected in available telemetry.": "Không phát hiện sự kiện bất thường được nhóm trong dữ liệu hiện có.",
        "Event details": "Chi tiết sự kiện",
        "Select an event to focus the charts and inspect cautious diagnostic guidance.": "Chọn một sự kiện để tập trung biểu đồ và xem gợi ý chẩn đoán thận trọng.",
        "Synchronized historical charts": "Biểu đồ lịch sử đồng bộ",
        "Session timeline": "Dòng thời gian phiên",
        "Diagnostic telemetry": "Dữ liệu chẩn đoán",
        "Telemetry charts": "Biểu đồ dữ liệu",
        "View signal behavior across the full ride.": "Xem diễn biến các tín hiệu trong toàn bộ phiên chạy.",
        "Show charts": "Hiển thị biểu đồ",
        "Collapse charts": "Thu gọn biểu đồ",
        "Loading charts...": "Đang tải biểu đồ...",
        "Retry charts": "Thử lại",
        "Unable to load chart data.": "Không thể tải dữ liệu biểu đồ.",
        "No chartable telemetry is available for this session.": "Không có dữ liệu biểu đồ cho phiên này.",
        "Chart signal toggles": "Chọn tín hiệu biểu đồ",
        "Reset zoom": "Đặt lại zoom",
        "Raw and decoded data": "Dữ liệu thô và đã giải mã",
        "Latest bounded records": "Bản ghi giới hạn mới nhất",
        "Showing at most 30 records to keep large sessions readable.": "Hiển thị tối đa 30 bản ghi để các phiên lớn vẫn dễ đọc.",
        "Raw frame": "Frame thô",
        "Missing samples": "Mẫu bị thiếu",
        "Invalid frames": "Frame không hợp lệ",
        "Checksum failures": "Lỗi checksum",
        "Anomalous samples": "Mẫu bất thường",
        "No telemetry records were stored for this session.": "Không có bản ghi telemetry nào được lưu cho phiên này.",
        "No decoded numeric ECU parameters are available for this session.": "Không có tham số ECU dạng số đã giải mã cho phiên này.",
        "Loading session samples...": "Đang tải mẫu của phiên...",
        "Select at least one signal to display.": "Chọn ít nhất một tín hiệu để hiển thị.",
        "Anomaly score": "Điểm bất thường",
        "Elapsed time from session start": "Thời gian từ lúc bắt đầu phiên",
        "Model and analysis status": "Trạng thái mô hình và phân tích",
        "Inspect anomaly results across uploaded sessions and confirm whether the external ML API is reachable.": "Xem kết quả bất thường qua các phiên đã tải lên và kiểm tra API ML bên ngoài.",
        "Model status": "Trạng thái mô hình",
        "ML API available": "API ML khả dụng",
        "ML API unavailable": "API ML không khả dụng",
        "Stored analysis metadata": "Metadata phân tích đã lưu",
        "Available": "Khả dụng",
        "Stored": "Đã lưu",
        "Unavailable": "Không khả dụng",
        "ML API": "API ML",
        "Analyzer connection": "Kết nối analyzer",
        "Configured URL": "URL đã cấu hình",
        "Stored metadata": "Metadata đã lưu",
        "Available from latest completed analysis": "Có từ lần phân tích hoàn tất gần nhất",
        "Loaded model": "Mô hình đã nạp",
        "Training data": "Dữ liệu huấn luyện",
        "Feature schema": "Lược đồ đặc trưng",
        "Telemetry schema": "Lược đồ telemetry",
        "Decoder": "Bộ giải mã",
        "Threshold": "Ngưỡng",
        "Latest stored run": "Lần chạy đã lưu mới nhất",
        "Last successful analysis": "Lần phân tích thành công gần nhất",
        "Average latency": "Độ trễ trung bình",
        "Failed analyses": "Phân tích thất bại",
        "Result interpretation": "Diễn giải kết quả",
        "How to read these results": "Cách đọc kết quả",
        "Analysis result table": "Bảng kết quả phân tích",
        "Analysis date": "Ngày phân tích",
        "Result": "Kết quả",
        "Max score": "Điểm cao nhất",
        "Mean score": "Điểm trung bình",
        "Anomalous %": "% bất thường",
        "Re-analysis": "Phân tích lại",
        "Re-run": "Chạy lại",
        "No uploaded sessions are available for analysis.": "Không có phiên đã tải lên để phân tích.",
        "ECU reader management": "Quản lý đầu đọc ECU",
        "Registered readers, synchronization state, and assigned vehicles.": "Đầu đọc đã đăng ký, trạng thái đồng bộ và xe được gán.",
        "Pair new device": "Ghép thiết bị mới",
        "Generate pairing code": "Tạo mã ghép nối",
        "Register manually": "Đăng ký thủ công",
        "Device ID": "ID thiết bị",
        "Device name": "Tên thiết bị",
        "Vehicle name": "Tên xe",
        "ECU type": "Loại ECU",
        "Firmware": "Firmware",
        "Hardware": "Phần cứng",
        "Create device token": "Tạo token thiết bị",
        "Active pairing codes": "Mã ghép nối đang hoạt động",
        "No active pairing codes.": "Không có mã ghép nối đang hoạt động.",
        "Revoke": "Thu hồi",
        "Assigned vehicle": "Xe được gán",
        "Last uploaded session": "Phiên tải lên gần nhất",
        "Configuration": "Cấu hình",
        "Pending uploads": "Tải lên đang chờ",
        "Error state": "Trạng thái lỗi",
        "Save": "Lưu",
        "Open Session History": "Mở lịch sử phiên",
        "Rotate token": "Đổi token",
        "Disable": "Tắt",
        "Recent sessions": "Phiên gần đây",
        "No sessions yet.": "Chưa có phiên.",
        "Enabled": "Đã bật",
        "Disabled": "Đã tắt",
        "Normal": "Bình thường",
        "Limited data": "Dữ liệu hạn chế",
        "Minor anomaly": "Bất thường nhẹ",
        "Requires attention": "Cần chú ý",
        "High anomaly": "Bất thường cao",
        "Analysis unavailable": "Không có phân tích",
        "Processing": "Đang xử lý",
        "Captured": "Đã ghi nhận",
        "Analysis pending": "Đang chờ phân tích",
        "Analyzed": "Đã phân tích",
        "Analysis failed": "Phân tích thất bại",
        "Invalid data": "Dữ liệu không hợp lệ",
        "Capture failed": "Ghi nhận thất bại",
        "Offline": "Ngoại tuyến",
        "Synced": "Đã đồng bộ",
        "Inactive": "Không hoạt động",
        "None detected in available decoded parameters": "Không phát hiện trong các tham số đã giải mã hiện có",
        "Isolation Forest-style anomaly detection and local screening identify unusual data patterns. They do not prove that a component has failed.": "Phát hiện bất thường theo Isolation Forest và sàng lọc cục bộ chỉ nhận diện các mẫu dữ liệu khác thường. Chúng không chứng minh một bộ phận đã hỏng.",
        "Not configured": "Chưa cấu hình",
        "Not reported": "Chưa báo cáo",
        "Requested": "Đã yêu cầu",
        "Queued": "Đã xếp hàng",
        "RPM": "RPM",
        "TPS": "TPS",
        "TPS voltage": "Điện áp TPS",
        "TPS raw": "TPS thô",
        "TPS raw candidate": "Ứng viên TPS thô",
        "ECT": "ECT",
        "IAT": "IAT",
        "Battery": "Ắc quy",
        "Battery voltage": "Điện áp ắc quy",
        "Engine temperature": "Nhiệt độ động cơ",
        "Intake air temperature": "Nhiệt độ khí nạp",
        "Injector duration": "Thời lượng kim phun",
        "Ignition timing": "Góc đánh lửa",
        "MAP raw": "MAP thô",
        "Monitor": "Theo dõi",
        "Minor": "Theo dõi",
        "Attention": "Đáng chú ý",
        "High": "Cao",
        "high": "cao",
        "low": "thấp",
        "stable": "ổn định",
        "established": "đã thiết lập",
        "learning": "đang xây dựng",
        "insufficient_data": "chưa đủ dữ liệu",
        "Live Preview": "Xem dữ liệu trực tiếp",
        "Temporary canonical telemetry · Not saved to ride history": "Telemetry chuẩn tạm thời · Không lưu vào lịch sử phiên",
        "NO DATA": "CHƯA CÓ DỮ LIỆU",
        "Remote control": "Điều khiển từ xa",
        "Live Mode": "Chế độ trực tiếp",
        "Live Mode:": "Chế độ trực tiếp:",
        "Checking ECU Reader control availability.": "Đang kiểm tra khả năng điều khiển đầu đọc ECU.",
        "Enable Live Mode": "Bật chế độ trực tiếp",
        "Disable Live Mode": "Tắt chế độ trực tiếp",
        "UNAVAILABLE": "KHÔNG KHẢ DỤNG",
        "ON": "BẬT",
        "OFF": "TẮT",
        "ENABLING...": "ĐANG BẬT...",
        "DISABLING...": "ĐANG TẮT...",
        "DISCONNECTED": "ĐÃ NGẮT KẾT NỐI",
        "LIVE": "TRỰC TIẾP",
        "STALE": "DỮ LIỆU CŨ",
        "YES": "CÓ",
        "NO": "KHÔNG",
        "PASS": "ĐẠT",
        "FAIL": "KHÔNG ĐẠT",
        "Live status unavailable": "Không có trạng thái trực tiếp",
        "Live Mode unavailable - ECU Reader offline.": "Chế độ trực tiếp không khả dụng vì đầu đọc ECU đang ngoại tuyến.",
        "Live Mode control is unavailable.": "Không thể điều khiển chế độ trực tiếp.",
        "Live Mode command failed.": "Lệnh chế độ trực tiếp không thành công.",
        "ECU Reader is offline for Live Mode control": "Đầu đọc ECU đang ngoại tuyến nên không thể điều khiển chế độ trực tiếp",
        "Live Preview cache is unavailable": "Bộ nhớ đệm dữ liệu trực tiếp không khả dụng",
        "Last sample": "Mẫu gần nhất",
        "Canonical Telemetry": "Telemetry chuẩn",
        "Candidate signals remain marked until validated.": "Tín hiệu ứng viên vẫn được đánh dấu cho đến khi được xác thực.",
        "Fuel cut": "Ngắt nhiên liệu",
        "Injector time": "Thời gian kim phun",
        "Sample Metadata": "Thông tin mẫu",
        "Session ID": "ID phiên",
        "Device time": "Thời gian thiết bị",
        "Server received": "Máy chủ nhận",
        "Profile": "Hồ sơ",
        "Frame valid": "Frame hợp lệ",
        "Injector raw": "Kim phun thô",
        "Raw length": "Độ dài dữ liệu thô",
        "Hex bytes for byte and bit inspection.": "Dữ liệu hex để kiểm tra byte và bit.",
        "Copy frame": "Sao chép frame",
    };

    const en = {
        "Điện áp hệ thống": "System voltage",
        "Nhiệt độ động cơ": "Engine temperature",
        "Nhiệt độ khí nạp": "Intake air temperature",
        "Điện áp hệ thống có khác biệt so với mức hoạt động thông thường của xe. Nên tiếp tục theo dõi trong các chuyến đi tiếp theo.": "System voltage differed from the vehicle's usual operating pattern. Continue monitoring it on future rides.",
        "Điện áp hệ thống có dấu hiệu thấp hoặc không ổn định. Nếu tình trạng tiếp tục xuất hiện, nên kiểm tra ắc quy, đầu cực và hệ thống sạc.": "System voltage appeared low or unstable. If it continues, inspect the battery, terminals, and charging system.",
        "Nhiệt độ động cơ trong chuyến đi cao hơn mức thường thấy của xe. Nên theo dõi thêm trong các chuyến đi tiếp theo.": "Engine temperature was higher than the vehicle's usual pattern during this ride. Continue monitoring it on future rides.",
        "Nhiệt độ động cơ cao hơn đáng kể so với mức thông thường. Nếu tình trạng lặp lại, nên kiểm tra mức và tình trạng nước làm mát, cùng khả năng tản nhiệt của hệ thống.": "Engine temperature was notably higher than usual. If it repeats, inspect coolant level and condition, plus the cooling system's ability to dissipate heat.",
        "Hệ thống ghi nhận nhiệt độ động cơ cao bất thường trong chuyến đi. Nên kiểm tra hệ thống làm mát trước khi tiếp tục sử dụng xe thường xuyên.": "The system recorded unusually high engine temperature during this ride. Inspect the cooling system before continuing frequent use.",
        "Nhiệt độ khí nạp khác đáng kể so với hành vi thường thấy của xe. Nên theo dõi thêm trong các chuyến đi tiếp theo.": "Intake air temperature differed notably from the vehicle's usual pattern. Continue monitoring it on future rides.",
        "Nhiệt độ khí nạp có dấu hiệu bất thường kéo dài hoặc lặp lại. Nên kiểm tra đường nạp, cảm biến IAT và các kết nối liên quan nếu hiện tượng tiếp tục xuất hiện.": "Intake air temperature appeared persistently or repeatedly unusual. If it continues, inspect the intake path, IAT sensor, and related connections.",
        "Bình thường": "Normal",
        "Theo dõi": "Monitor",
        "Đáng chú ý": "Attention",
        "Cao": "High",
        "Chưa đủ dữ liệu": "Not enough data yet",
        "DriveSafe có đủ lịch sử của xe để so sánh với hành vi thông thường.": "DriveSafe has enough vehicle history to compare with usual behavior.",
        "Đường cơ sở đang được xây dựng. DriveSafe chưa nên kết luận mạnh về mức lệch cá nhân hóa.": "The baseline is still being built. DriveSafe should not draw strong conclusions about personalized deviation yet.",
        "Đường cơ sở đang được xây dựng. DriveSafe chưa có đủ lịch sử của xe để đánh giá mức độ lệch so với hoạt động thông thường.": "The baseline is still being built. DriveSafe does not yet have enough vehicle history to assess deviation from usual operation.",
        "Nhiệt độ động cơ cao hơn mức thông thường của xe.": "Engine temperature was higher than the vehicle's usual baseline.",
        "Điện áp hệ thống cho thấy xu hướng thấp trong dữ liệu sau chuyến đi gần đây.": "System voltage showed a low pattern in recent post-ride data.",
        "Điện áp hệ thống cho thấy xu hướng cao trong dữ liệu sau chuyến đi gần đây.": "System voltage showed a high pattern in recent post-ride data.",
        "Điện áp hệ thống cho thấy xu hướng khác thường.": "System voltage showed an unusual pattern.",
        "Nhiệt độ khí nạp cao hơn mức thông thường của xe.": "Intake temperature was higher than the vehicle's usual baseline.",
        "Hãy hoàn tất thêm vài chuyến đi để bắt đầu xây dựng mức hoạt động thông thường của xe.": "Complete a few rides to begin building the vehicle's normal operating baseline.",
        "Chưa có chuyến đi nào được phân tích": "No analyzed rides yet",
        "DriveSafe ghi nhận các khuyến nghị bảo dưỡng sau chuyến đi gần đây cần xem lại.": "DriveSafe found recent post-ride maintenance findings worth reviewing.",
        "DriveSafe ghi nhận một lệch nhẹ đáng để theo dõi trong các chuyến đi tiếp theo.": "DriveSafe detected a minor deviation worth monitoring in future rides.",
        "Chưa ghi nhận khuyến nghị bảo dưỡng đáng chú ý trong các chuyến đi đã phân tích gần đây.": "No notable maintenance findings were detected in the recent analyzed rides.",
    };

    const viSignalNames = {
        "battery": "Ắc quy",
        "battery voltage": "Điện áp ắc quy",
        "engine temperature": "Nhiệt độ động cơ",
        "intake air temperature": "Nhiệt độ khí nạp",
        "rpm": "RPM",
        "tps": "TPS",
        "ect": "ECT",
        "iat": "IAT",
    };

    const viTitles = {
        "Overview · ECU Master": "Tổng quan · ECU Master",
        "Sessions · ECU Master": "Phiên · ECU Master",
        "AI Analysis · ECU Master": "Phân tích AI · ECU Master",
        "Tool · ECU Master": "Công cụ · ECU Master",
        "Devices · ECU Master": "Thiết bị · ECU Master",
        "Settings · ECU Master": "Cài đặt · ECU Master",
        "ECU Dashboard": "Bảng điều khiển ECU",
    };

    function normalizeLanguage(language) {
        return supportedLanguages.has(language) ? language : "en";
    }

    function savedLanguage() {
        const direct = localStorage.getItem(languageKey);
        if (direct) return normalizeLanguage(direct);
        try {
            const settings = JSON.parse(localStorage.getItem(settingsKey) || "{}");
            if (settings.language) return normalizeLanguage(settings.language);
        } catch (error) {
            return "vi";
        }
        return "vi";
    }

    function writeSettingsLanguage(language) {
        try {
            const settings = JSON.parse(localStorage.getItem(settingsKey) || "{}");
            settings.language = language;
            localStorage.setItem(settingsKey, JSON.stringify(settings));
        } catch (error) {
            localStorage.setItem(settingsKey, JSON.stringify({ language }));
        }
    }

    function translateExact(text, language) {
        const dictionary = language === "vi" ? vi : en;
        if (dictionary[text]) return dictionary[text];
        if (language !== "vi") {
            let englishMatch = text.match(/^Dựa trên (\d+) chuyến đi đã phân tích gần nhất$/);
            if (englishMatch) return `Based on the ${englishMatch[1]} most recent analyzed ${englishMatch[1] === "1" ? "ride" : "rides"}`;
            englishMatch = text.match(/^(.+) cho thấy xu hướng (cao|thấp)\.$/);
            if (englishMatch) return `${englishMatch[1]} showed a ${englishMatch[2] === "cao" ? "high" : "low"} pattern.`;
            englishMatch = text.match(/^(.+) cho thấy xu hướng khác thường sau chuyến đi\.$/);
            if (englishMatch) return `${englishMatch[1]} showed an unusual post-ride pattern.`;
            return text;
        }
        let match = text.match(/^Latest diagnosis: (.+)$/);
        if (match) return `Chẩn đoán mới nhất: ${translateExact(match[1], "vi")}`;
        match = text.match(/^Primary signals requiring attention: (.+)\.$/);
        if (match) return `Tín hiệu chính cần chú ý: ${translateSignalNames(match[1])}.`;
        match = text.match(/^Last sync (.+)$/);
        if (match) return `Đồng bộ gần nhất ${match[1]}`;
        match = text.match(/^Expires (.+) UTC · (.+) attempts$/);
        if (match) return `Hết hạn ${match[1]} UTC · ${match[2]} lần thử`;
        match = text.match(/^Min (.+)$/);
        if (match) return `Nhỏ nhất ${match[1]}`;
        match = text.match(/^Max (.+)$/);
        if (match) return `Lớn nhất ${match[1]}`;
        match = text.match(/^Mean (.+)$/);
        if (match) return `Trung bình ${match[1]}`;
        match = text.match(/^Std (.+)$/);
        if (match) return `Độ lệch chuẩn ${match[1]}`;
        match = text.match(/^Elapsed (.+)$/);
        if (match) return `Đã chạy ${match[1]}`;
        match = text.match(/^Charts are downsampled to (.+) of (.+) records\.$/);
        if (match) return `Biểu đồ được giảm mẫu còn ${match[1]} trên ${match[2]} bản ghi.`;
        match = text.match(/^Observed (high|low|stable) pattern in post-ride (.+) data\.$/);
        if (match) return `Ghi nhận xu hướng ${translateExact(match[1], "vi")} trong dữ liệu ${translateSignalNames(match[2])} sau chuyến đi.`;
        match = text.match(/^(.+) anomaly$/);
        if (match) return `Bất thường ${translateSignalNames(match[1])}`;
        match = text.match(/^Unusual (.+) behavior was detected in this time range\. Inspect the related signal path, connectors, and riding context before drawing a mechanical conclusion\.$/);
        if (match) return `Ghi nhận hành vi ${translateSignalNames(match[1])} khác thường trong khoảng thời gian này. Hãy kiểm tra đường tín hiệu liên quan, đầu nối và bối cảnh vận hành trước khi đưa ra kết luận cơ khí.`;
        match = text.match(/^Main unusual signals: (.+)$/);
        if (match) return `Tín hiệu khác thường chính: ${translateSignalNames(match[1])}`;
        match = text.match(/^(.+) · (.+) · max score (.+) · (.+) abnormal samples$/);
        if (match) return `${translateExact(match[1], "vi")} · ${match[2]} · điểm cao nhất ${match[3]} · ${match[4]} mẫu bất thường`;
        match = text.match(/^(\d+) grouped anomaly intervals fall within this time segment\.$/);
        if (match) return `Có ${match[1]} khoảng bất thường đã nhóm trong đoạn thời gian này. Bạn có thể chọn từng khoảng để xem chi tiết.`;
        match = text.match(/^(\d+) grouped anomaly intervals? from (.+)\.$/);
        if (match) return `${match[1]} khoảng bất thường đã nhóm từ ${match[2]}.`;
        match = text.match(/^(\d\d:\d\d(?::\d\d)?-\d\d:\d\d(?::\d\d)?) \| (Monitor|Minor|Attention|High)$/);
        if (match) return `${match[1]} | ${translateExact(match[2], "vi")}`;
        match = text.match(/^Boot (.+) is reporting Live Mode on\.$/);
        if (match) return `Lần khởi động ${match[1]} đang báo chế độ trực tiếp đã bật.`;
        match = text.match(/^Boot (.+) is ready for Live Mode control\.$/);
        if (match) return `Lần khởi động ${match[1]} sẵn sàng điều khiển chế độ trực tiếp.`;
        match = text.match(/^Enable command is waiting for ECU Reader acknowledgement\.$/);
        if (match) return "Lệnh bật đang chờ đầu đọc ECU xác nhận.";
        match = text.match(/^Disable command is waiting for ECU Reader acknowledgement\.$/);
        if (match) return "Lệnh tắt đang chờ đầu đọc ECU xác nhận.";
        match = text.match(/^(\d+)s ago$/);
        if (match) return `${match[1]} giây trước`;
        match = text.match(/^(\d+) bytes$/);
        if (match) return `${match[1]} byte`;
        match = text.match(/^Normal data from (.+)\.$/);
        if (match) return `Dữ liệu bình thường từ ${match[1]}.`;
        return text;
    }

    function translateSignalNames(value) {
        return String(value).split(/([,/])/).map((part) => {
            if (part === "," || part === "/") return part;
            const leading = part.match(/^\s*/)[0];
            const trailing = part.match(/\s*$/)[0];
            const signal = part.trim();
            return `${leading}${viSignalNames[signal.toLowerCase()] || translateExact(signal, "vi")}${trailing}`;
        }).join("");
    }

    function replacePreservingWhitespace(original, translated) {
        const leading = original.match(/^\s*/)[0];
        const trailing = original.match(/\s*$/)[0];
        return `${leading}${translated}${trailing}`;
    }

    function shouldSkipTextNode(node) {
        const parent = node.parentElement;
        return !parent || Boolean(parent.closest("script,style,code,pre,textarea"));
    }

    function replaceTextNode(node, replacement) {
        if (node.nodeValue === replacement) return;
        translatedTextNodes.add(node);
        node.nodeValue = replacement;
    }

    function translateTextNode(node, language) {
        if (shouldSkipTextNode(node)) return;
        if (!originalText.has(node)) originalText.set(node, node.nodeValue);
        const source = originalText.get(node);
        const trimmed = source.trim();
        if (!trimmed) {
            replaceTextNode(node, source);
            return;
        }
        const translated = translateExact(trimmed, language);
        const replacement = replacePreservingWhitespace(source, translated);
        replaceTextNode(node, replacement);
    }

    function translateElementAttributes(element, language) {
        if (element.matches("script,style,code,pre,textarea")) return;
        const attrs = ["placeholder", "title", "aria-label"];
        let stored = originalAttributes.get(element);
        if (!stored) {
            stored = {};
            originalAttributes.set(element, stored);
        }
        attrs.forEach((attr) => {
            if (!element.hasAttribute(attr)) return;
            if (!(attr in stored)) stored[attr] = element.getAttribute(attr);
            const source = stored[attr];
            const translated = translateExact(source, language);
            if (element.getAttribute(attr) !== translated) element.setAttribute(attr, translated);
        });
    }

    function translateNode(node, language) {
        if (node.nodeType === Node.TEXT_NODE) {
            translateTextNode(node, language);
            return;
        }
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        translateElementAttributes(node, language);
        node.querySelectorAll("[placeholder],[title],[aria-label]").forEach((element) => translateElementAttributes(element, language));
        const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
        while (walker.nextNode()) translateTextNode(walker.currentNode, language);
    }

    function applyLanguage(language) {
        applying = true;
        document.documentElement.lang = language;
        document.title = language === "vi" ? (viTitles[originalTitle] || originalTitle) : originalTitle;
        translateNode(document.body, language);
        document.querySelectorAll("select[name='language']").forEach((select) => {
            select.value = language;
        });
        applying = false;
    }

    function setLanguage(language) {
        const normalized = normalizeLanguage(language);
        localStorage.setItem(languageKey, normalized);
        writeSettingsLanguage(normalized);
        applyLanguage(normalized);
        window.dispatchEvent(new CustomEvent("drisafe:languagechange", { detail: { language: normalized } }));
    }

    document.addEventListener("change", (event) => {
        const target = event.target;
        if (target && target.matches("select[name='language']")) {
            setLanguage(target.value);
        }
    });

    const observer = new MutationObserver((mutations) => {
        if (applying) return;
        if (savedLanguage() === "en") {
            mutations.forEach((mutation) => {
                if (mutation.type === "characterData") translatedTextNodes.delete(mutation.target);
            });
            return;
        }
        applying = true;
        mutations.forEach((mutation) => {
            if (mutation.type === "characterData") {
                if (translatedTextNodes.delete(mutation.target)) return;
                originalText.delete(mutation.target);
                translateTextNode(mutation.target, "vi");
                return;
            }
            mutation.addedNodes.forEach((node) => translateNode(node, "vi"));
        });
        applying = false;
    });

    applyLanguage(savedLanguage());
    window.drisafeI18n = { setLanguage, applyLanguage };
    translatedTextNodes = new WeakSet();
    observer.observe(document.body, { childList: true, characterData: true, subtree: true });
})();
