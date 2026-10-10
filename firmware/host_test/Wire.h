// Fake I2C bus with one INA226 / INA219 / INA260 decoy / reconfigured INA219 / PCA9685 decoy.
#pragma once
#include "Arduino.h"
#include <cmath>
#include <vector>

extern uint64_t g_now_us;
extern int g_chip;            // 226, 219, 0 = none, 9685 = PCA9685, 260 = INA260, 2191 = INA219 (custom cfg)

struct FakeINA {
  uint16_t regs[256] = {0};
  uint8_t ptr = 0;
  int kind = 226;
  void init(int k) {
    kind = k;
    if (k == 226) { regs[0] = 0x4127; regs[0xFE] = 0x5449; regs[0xFF] = 0x2260; }
    if (k == 219) { regs[0] = 0x399F; }
    if (k == 9685) { regs[0] = 0x11; }
    if (k == 260) { regs[0] = 0x6127; regs[0xFE] = 0x5449; regs[0xFF] = 0x2270; }   // INA260: TI, not INA226
    if (k == 2191) { kind = 219; regs[0] = 0x019F; }                                   // INA219, custom config
  }
  // 50 mA idle with a 400 mA burst for 1 ms every 10 ms
  double current() const { double t = g_now_us * 1e-6; return fmod(t, 0.010) < 0.001 ? 0.400 : 0.050; }
  uint16_t read(uint8_t r) const {
    if (kind == 226 && r == 1) return (uint16_t)(int16_t)lround(current() * 0.1 / 2.5e-6);
    if (kind == 226 && r == 2) return (uint16_t)lround((3.3 - current() * 0.15) / 1.25e-3);
    if (kind == 219 && r == 1) return (uint16_t)(int16_t)lround(current() * 0.1 / 10e-6);
    if (kind == 219 && r == 2) return (uint16_t)(lround((3.3 - current() * 0.15) / 4e-3) << 3) | 2;
    return regs[r];
  }
};

struct HostWire {
  FakeINA dev;
  uint8_t txAddr = 0;
  std::vector<uint8_t> tx, rxbuf;
  size_t rxpos = 0;
  int writes_to_decoy = 0;
  bool present(uint8_t a) const { return g_chip != 0 && a == 0x40; }
  void cost(int bytes) { g_now_us += (uint64_t)(bytes * 9 * 1e6 / 400000.0 + 0.5); }
  void begin(int, int) { dev.init(g_chip); }
  void setClock(uint32_t) {}
  void beginTransmission(uint8_t a) { txAddr = a; tx.clear(); }
  size_t write(uint8_t b) { tx.push_back(b); return 1; }
  uint8_t endTransmission(bool = true) {
    cost(1 + (int)tx.size());
    if (!present(txAddr)) return 2;                       // NACK on address
    if (!tx.empty()) dev.ptr = tx[0];
    if (tx.size() == 3) {
      if (g_chip == 9685) writes_to_decoy++;
      dev.regs[tx[0]] = (uint16_t)(tx[1] << 8 | tx[2]);
    }
    return 0;
  }
  int requestFrom(int a, int n) {
    cost(1 + n);
    rxbuf.clear(); rxpos = 0;
    if (!present((uint8_t)a)) return 0;
    uint16_t v = dev.read(dev.ptr);
    rxbuf = {(uint8_t)(v >> 8), (uint8_t)(v & 0xFF)};
    return n;
  }
  int read() { return rxpos < rxbuf.size() ? rxbuf[rxpos++] : -1; }
};
extern HostWire Wire;
