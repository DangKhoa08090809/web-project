# Honda K-Line Init Sequence

Source path: `app_main -> EcuReader::begin -> reconnect_with_backoff -> honda_init_pulse -> wakeup_ecu`.

## Boot To First Valid Response

1. Boot initializes NVS, boot counter, storage, Wi-Fi workers, upload workers, and Live state.
2. `lifecycle.wait_for_engine()` sets the road logger lifecycle to waiting.
3. `ecu_reader.begin()` calls `reconnect_with_backoff()`.
4. `honda_init_pulse()` deletes any installed UART driver, configures TX GPIO as output, drives TX low, then high.
5. UART is configured and installed as UART1, GPIO21 TX, GPIO20 RX, 10400 8N1.
6. `wakeup_ecu()` flushes UART input, sends wakeup bytes, waits, sends init bytes, waits, then flushes input.
7. The main loop sends the first Table 0x17 request.
8. The first valid response is a frame read from a physical `0x02` start byte, with length byte `0x18`, header `02 18 71 17`, and sum-zero checksum.

## Exact Sequence

| Step | Operation | Value | Unit | Source | Confidence |
| ---: | --- | ---: | --- | --- | --- |
| 1 | Delete existing UART driver if installed | n/a | n/a | `EcuReader::honda_init_pulse` | EXACT_FROM_CODE |
| 2 | Reset TX GPIO and set output | GPIO21 | pin | `honda_init_pulse` | EXACT_FROM_CODE |
| 3 | Drive TX low | 0 | logic level | `honda_init_pulse` | EXACT_FROM_CODE |
| 4 | Hold TX low | 70 | ms | `kHondaSlowInitTxLowMs` | EXACT_FROM_CODE |
| 5 | Drive TX high | 1 | logic level | `honda_init_pulse` | EXACT_FROM_CODE |
| 6 | Hold TX high | 120 | ms | `kHondaSlowInitTxHighMs` | EXACT_FROM_CODE |
| 7 | Configure UART | 10400 8N1 | UART | `configure_uart` | EXACT_FROM_CODE |
| 8 | Delay after UART install | 25 | ms | `kHondaSlowInitPostUartDelayMs` | EXACT_FROM_CODE |
| 9 | Flush UART input | n/a | n/a | `wakeup_ecu` | EXACT_FROM_CODE |
| 10 | Send wakeup frame | `FE 04 72 8C` | bytes | `kHondaWakeupFrame` | EXACT_FROM_CODE |
| 11 | Wait TX done | 100 max | ms | `uart_wait_tx_done` | EXACT_FROM_CODE |
| 12 | Delay after wakeup frame | 60 | ms | `kHondaWakeupPostDelayMs` | EXACT_FROM_CODE |
| 13 | Send init frame | `72 05 00 F0 99` | bytes | `kHondaInitFrame` | EXACT_FROM_CODE |
| 14 | Wait TX done | 100 max | ms | `uart_wait_tx_done` | EXACT_FROM_CODE |
| 15 | Delay after init frame | 60 | ms | `kHondaInitPostDelayMs` | EXACT_FROM_CODE |
| 16 | Flush UART input | n/a | n/a | `wakeup_ecu` | EXACT_FROM_CODE |
| 17 | Send Table 0x17 request | `72 05 71 17 01` | bytes | `send_table17_request` | EXACT_FROM_CODE |
| 18 | Wait for response | 180000 | us | `kFrameTimeoutUs` | EXACT_FROM_CODE |

## Notes For Pi5

- The TX low/high pulse is GPIO bit-banging, not UART break.
- The code does not name ISO slow-init or fast-init standards. Do not derive extra timing from a standard unless hardware tests prove it is needed.
- No fast-init path exists in active code or host tests.
