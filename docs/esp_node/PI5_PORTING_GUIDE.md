# Pi5 Porting Guide

This is a portability classification, not a Pi5 design.

## Component Classification

| Component | Classification | Reason |
| --- | --- | --- |
| Honda slow-init pulse values | PORT | Protocol/hardware behavior required before UART init |
| Wakeup bytes `FE 04 72 8C` | PORT | ECU init behavior |
| Init bytes `72 05 00 F0 99` | PORT | ECU init behavior |
| Table 0x17 request `72 05 71 17 01` | PORT | ECU protocol |
| 10400 8N1 UART settings | PORT | K-Line transport contract |
| 24-byte frame validation | PORT | ECU response contract |
| Header `02 18 71 17` | PORT | Active frame identity |
| Sum-zero 24-byte checksum | PORT | Wire checksum contract |
| Five-FF backend compatibility conversion | REFERENCE ONLY | Needed only if Pi5 emits legacy backend frames |
| 29-byte checksum `sum % 256 == 251` | REFERENCE ONLY | Compatibility representation, not wire frame |
| ESP-IDF UART driver calls | REIMPLEMENT | ESP-specific |
| ESP GPIO pulse implementation | REIMPLEMENT | ESP-specific; behavior must be reproduced |
| FreeRTOS tasks and delays | DO NOT PORT | ESP runtime model |
| Raw binary session file format | REFERENCE ONLY | Useful if preserving ESP storage compatibility |
| Wi-Fi provisioning/AP portal | DO NOT PORT | Product/platform-specific |
| Cloud upload JSON envelope | REFERENCE ONLY | Pi may use a separate sync flow, but this is current ESP contract |
| Live Mode Cloud control | REFERENCE ONLY | Do not conflate with Pi5 native local live view |
| Timing constants | VALIDATE ON PI | Linux scheduling and UART behavior differ |
| Retry/backoff policy | REFERENCE ONLY | Product behavior, not K-Line protocol |

## Linux Timing Risk Inventory

| Operation | Timing requirement | ESP implementation | Pi risk |
| --- | ---: | --- | --- |
| TX low slow-init pulse | 70 ms | GPIO output low plus FreeRTOS delay | Linux GPIO jitter can alter pulse width |
| TX high settle before UART | 120 ms | GPIO output high plus delay | Linux scheduling jitter likely tolerable but must be measured |
| UART install after GPIO pulse | 25 ms delay | Configure UART after pulse | Pi serial driver transition timing must be tested |
| Wakeup frame TX completion | 100 ms max wait | `uart_wait_tx_done` | Need equivalent drain/flush on Linux serial |
| Wakeup-to-init gap | 60 ms | FreeRTOS delay | Validate on hardware |
| Init frame TX completion | 100 ms max wait | `uart_wait_tx_done` | Need serial drain semantics |
| Init-to-poll gap | 60 ms plus next loop scheduling | FreeRTOS delay then main loop | Validate on hardware |
| Request TX completion | 100 ms max wait | Flush input, write, wait TX done | Need reliable serial write/drain |
| Response deadline | 180 ms total | `esp_timer_get_time` deadline and 1..10 ms read ticks | Linux read timeout behavior must be matched |
| Poll interval | 250 ms | `vTaskDelayUntil` | Implementation convenience; not proven wire requirement |
| Session close timeout | 10000 ms | Monotonic lifecycle timing | Low Pi risk |

## Do Not Infer

- Do not conclude an extra MCU is required from this audit alone.
- Do not add K-Line commands or decoders during the Pi port.
- Do not treat the five `FF` bytes as ECU wire bytes unless hardware capture proves it.
- Do not use Analyzer legacy 29-byte runtime behavior as the Pi UART contract.
