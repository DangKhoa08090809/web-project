# L9637D UART Contract

## Hardware Contract

| Item | Value | Evidence | Confidence |
| --- | --- | --- | --- |
| ESP target | ESP32-C3 | `sdkconfig.defaults`, `README.md` | EXACT_FROM_CODE |
| Board | Seeed Studio XIAO ESP32-C3 | `README.md` | EXACT_FROM_CODE |
| UART peripheral | UART1 | `CONFIG_DRISAFE_KLINE_UART=1`, `app_main.cpp` static assert | EXACT_FROM_CODE |
| TX GPIO | GPIO21 / XIAO D6 | `CONFIG_DRISAFE_KLINE_TX_GPIO=21`, `app_main.cpp` static assert | EXACT_FROM_CODE |
| RX GPIO | GPIO20 / XIAO D7 | `CONFIG_DRISAFE_KLINE_RX_GPIO=20`, `app_main.cpp` static assert | EXACT_FROM_CODE |
| Baud | 10400 | `main/ecu/uart_settings.hpp` | EXACT_FROM_CODE |
| Data bits | 8 | `uart_config_t.data_bits = UART_DATA_8_BITS` | EXACT_FROM_CODE |
| Parity | None | `UART_PARITY_DISABLE` | EXACT_FROM_CODE |
| Stop bits | 1 | `UART_STOP_BITS_1` | EXACT_FROM_CODE |
| Flow control | Disabled | `UART_HW_FLOWCTRL_DISABLE` | EXACT_FROM_CODE |
| UART source clock | `UART_SCLK_DEFAULT` | `main/ecu/ecu_reader.cpp` | EXACT_FROM_CODE |
| RX buffer | 1024 bytes | `main/ecu/uart_settings.hpp` | EXACT_FROM_CODE |
| TX buffer | 0 bytes | `main/ecu/uart_settings.hpp` | EXACT_FROM_CODE |
| UART event queue | 0, disabled | `main/ecu/uart_settings.hpp` | EXACT_FROM_CODE |
| UART inversion | Not configured | No `uart_set_line_inverse` or equivalent in source | EXACT_FROM_CODE |
| RTS/CTS pins | `UART_PIN_NO_CHANGE` | `uart_set_pin` call | EXACT_FROM_CODE |
| K-Line transceiver | External L9637D or equivalent expected | README and task context; no chip-specific GPIO code | DERIVED |
| L9637D enable/wakeup GPIO | None controlled by firmware | No enable/wakeup GPIO path in source | EXACT_FROM_CODE |

## Diagram

```text
Honda ECU
   |
 K-Line
   |
L9637D or external K-Line transceiver
   |
 TX/RX UART logic
   |
ESP32-C3 UART1
  TX GPIO21
  RX GPIO20
```

## Electrical Assumptions Proven by Repo

- The firmware must not connect motorcycle K-Line directly to ESP32-C3 GPIO; an external K-Line transceiver is required.
- The only K-Line wake/init action controlled by firmware is driving TX GPIO low/high before reinstalling the UART driver.
- No repository code proves L9637D pin numbers, EN pin wiring, VS/K pullups, or wake pin wiring.
