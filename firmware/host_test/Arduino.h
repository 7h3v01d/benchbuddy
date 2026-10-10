// Minimal Arduino shim so the sketch builds and runs on a PC for protocol tests.
#pragma once
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cctype>
#include <string>

extern uint64_t g_now_us;                       // simulated clock, advanced by the I2C model and loop
inline uint32_t micros() { return (uint32_t)g_now_us; }
inline uint32_t millis() { return (uint32_t)(g_now_us / 1000); }
inline void delay(uint32_t ms) { g_now_us += (uint64_t)ms * 1000; }

struct HostSerial {
  std::string rx;                               // bytes "from the PC"
  FILE *out = stdout;
  void begin(uint32_t) {}
  void setTxBufferSize(size_t) {}
  int available() { return (int)rx.size(); }
  int read() { if (rx.empty()) return -1; int c = (unsigned char)rx[0]; rx.erase(0, 1); return c; }
  size_t write(const uint8_t *b, size_t n) { fwrite(b, 1, n, out); return n; }
};
extern HostSerial Serial;
