// BenchBuddy current meter firmware
// ---------------------------------------------------------------------------
// Board : any ESP32 (tested layout: Wemos D1 R32). I2C on SDA = IO21, SCL = IO22.
// Sensor: INA226 or INA219 breakout at 0x40..0x4F (auto-detected).
// Link  : USB serial at 921600 baud, framed binary (see benchbuddy/core/meter.py).
//
// Frame:  0xB5 0x42 | type | len (u16 LE) | payload | ck_a ck_b   (8-bit Fletcher over type..payload)
//   HELLO   (0x01) "fw=1;chip=INA226;addr=0x40;mode=NORMAL;period_us=664;cfg=0x4097"
//   SAMPLES (0x02) t0_us u32, n u8, n x { dt_us u16, shunt_raw i16, bus_raw u16 }
//   LOG     (0x03) plain text
// Commands (text lines from the PC): HELLO, START, STOP, MODE NORMAL, MODE FAST, FORCE INA219
//
// Detection is read-only. INA226: TI manufacturer ID 0x5449 *and* die ID 0x226x (other TI
// monitors such as the INA260 share the manufacturer ID). INA219: it has no ID register, so it
// is only recognised by its power-on config (0x399F) or one of ours; an INA219 configured some
// other way needs the explicit "FORCE INA219" command, which then writes its config.
//
// Modes
//   NORMAL  shunt + bus every cycle.   INA226: 332 us + 332 us (~1.5 kHz).  INA219: 12-bit, 532 + 532 us (~940 Hz)
//   FAST    shunt only, bus sampled once on entry.  INA226: 140 us conversions.  INA219: 10-bit, 148 us.
//           Rate is then limited by the 400 kHz I2C read (~6-7 kHz).
//
// Raw register values are sent as-is; the PC applies LSBs, shunt resistance and zero offset.
// SPDX-License-Identifier: Apache-2.0

#include <Arduino.h>
#include <Wire.h>

static const uint32_t BAUD = 921600;
static const int PIN_SDA = 21;
static const int PIN_SCL = 22;
static const uint32_t I2C_HZ = 400000;          // INA226 / INA219 fast-mode limit
static const uint8_t BATCH = 32;                 // samples per frame
static const uint32_t FLUSH_US = 20000;          // send a partial batch after 20 ms

enum ChipType : uint8_t { CHIP_NONE, CHIP_INA219, CHIP_INA226 };
static ChipType chip = CHIP_NONE;
static uint8_t addr = 0;
static bool streaming = false;
static bool fastMode = false;
static uint16_t cfgWord = 0;
static uint32_t periodUs = 1000;
static uint16_t busHeld = 0;                     // FAST mode: bus voltage read on entry

// ----------------------------------------------------------------- framing
static void sendFrame(uint8_t type, const uint8_t *payload, uint16_t len) {
  uint8_t head[5] = {0xB5, 0x42, type, (uint8_t)(len & 0xFF), (uint8_t)(len >> 8)};
  uint8_t a = 0, b = 0;
  for (int k = 2; k < 5; k++) { a += head[k]; b += a; }
  for (uint16_t k = 0; k < len; k++) { a += payload[k]; b += a; }
  Serial.write(head, 5);
  if (len) Serial.write(payload, len);
  uint8_t ck[2] = {a, b};
  Serial.write(ck, 2);
}

static void sendText(uint8_t type, const char *text) {
  sendFrame(type, (const uint8_t *)text, (uint16_t)strlen(text));
}

// --------------------------------------------------------------------- i2c
static bool writeReg(uint8_t reg, uint16_t val) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  Wire.write((uint8_t)(val >> 8));
  Wire.write((uint8_t)(val & 0xFF));
  return Wire.endTransmission() == 0;
}

static bool setPointer(uint8_t reg) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  return Wire.endTransmission() == 0;
}

// Reads 2 bytes from whatever register the pointer is on (the INA keeps it between reads).
static bool readCurrent(uint16_t &val) {
  if (Wire.requestFrom((int)addr, 2) != 2) return false;
  uint8_t hi = Wire.read();
  uint8_t lo = Wire.read();
  val = ((uint16_t)hi << 8) | lo;
  return true;
}

static bool readReg(uint8_t reg, uint16_t &val) {
  return setPointer(reg) && readCurrent(val);
}

static bool probe(uint8_t a) {
  Wire.beginTransmission(a);
  return Wire.endTransmission() == 0;
}

// ------------------------------------------------------------- chip setup
static void detectChip() {
  chip = CHIP_NONE;
  for (uint8_t a = 0x40; a <= 0x4F; a++) {
    if (!probe(a)) continue;
    addr = a;
    uint16_t id = 0, die = 0;
    if (readReg(0xFE, id) && id == 0x5449) {           // a TI current monitor...
      if (readReg(0xFF, die) && (die & 0xFFF0) == 0x2260) {
        chip = CHIP_INA226;                             // ...and specifically an INA226
        return;
      }
      char msg[96];
      snprintf(msg, sizeof(msg), "TI device at 0x%02X is not an INA226 (die ID 0x%04X): ignored", a, die);
      sendText(0x03, msg);
      continue;
    }
    // INA219 has no ID register. Recognise it read-only by its config word: the power-on
    // default (0x399F) or one of ours (after an ESP32 reset the INA keeps our setting).
    // No blind reset write: a PCA9685 servo driver also lives at 0x40.
    uint16_t cfg = 0;
    if (readReg(0x00, cfg) && (cfg == 0x399F || cfg == 0x398D)) {
      chip = CHIP_INA219;
      return;
    }
  }
}

static void applyMode() {
  if (chip == CHIP_INA226) {
    // bit14 reserved(1) | AVG=1 | VBUSCT | VSHCT | MODE
    cfgWord = fastMode ? 0x4005 : 0x4097;               // FAST: 140 us shunt-only; NORMAL: 332+332 us both
    periodUs = fastMode ? 150 : 664;
  } else if (chip == CHIP_INA219) {
    // BRNG=32V | PG=/8 (+-320 mV) | BADC | SADC | MODE
    cfgWord = fastMode ? 0x398D : 0x399F;               // FAST: 10-bit 148 us shunt-only; NORMAL: 12-bit both
    periodUs = fastMode ? 160 : 1064;
  } else {
    return;
  }
  if (fastMode) {                                       // grab the bus voltage before going shunt-only
    writeReg(0x00, chip == CHIP_INA226 ? 0x4097 : 0x399F);
    delay(3);
    readReg(0x02, busHeld);
  }
  writeReg(0x00, cfgWord);
  delay(2);
  setPointer(0x01);
}

static void sendHello() {
  char buf[160];
  const char *name = chip == CHIP_INA226 ? "INA226" : chip == CHIP_INA219 ? "INA219" : "NONE";
  snprintf(buf, sizeof(buf), "fw=1;chip=%s;addr=0x%02X;mode=%s;period_us=%lu;cfg=0x%04X;i2c=%lu",
           name, addr, fastMode ? "FAST" : "NORMAL", (unsigned long)periodUs, cfgWord,
           (unsigned long)I2C_HZ);
  sendText(0x01, buf);
  if (chip == CHIP_NONE) {
    sendText(0x03, "No INA226/INA219 answered at 0x40-0x4F on SDA=IO21 SCL=IO22. Check wiring and 3V3/GND.");
  }
}

// ---------------------------------------------------------------- sampling
static uint8_t frame[5 + BATCH * 6];
static uint8_t count = 0;
static uint32_t lastSampleUs = 0;
static uint32_t batchStartUs = 0;
static uint32_t nextUs = 0;

static void put16(uint8_t *p, uint16_t v) { p[0] = v & 0xFF; p[1] = v >> 8; }

static void flushBatch() {
  if (!count) return;
  frame[4] = count;
  sendFrame(0x02, frame, 5 + count * 6);
  count = 0;
}

static void takeSample() {
  uint32_t now = micros();
  uint16_t shunt = 0, bus = busHeld;
  bool ok;
  if (fastMode) {
    ok = readCurrent(shunt);                            // pointer already parked on 0x01
  } else {
    ok = readReg(0x01, shunt) && readReg(0x02, bus);
  }
  if (!ok) return;
  if (count == 0) {
    batchStartUs = now;
    frame[0] = now & 0xFF; frame[1] = (now >> 8) & 0xFF; frame[2] = (now >> 16) & 0xFF; frame[3] = now >> 24;
    lastSampleUs = now;
  }
  uint8_t *p = frame + 5 + count * 6;
  uint32_t dt = now - lastSampleUs;
  put16(p, dt > 0xFFFF ? 0xFFFF : (uint16_t)dt);
  put16(p + 2, shunt);
  put16(p + 4, bus);
  lastSampleUs = now;
  if (++count >= BATCH) flushBatch();
}

// ---------------------------------------------------------------- commands
static char line[40];
static uint8_t lineLen = 0;

static void handleCommand(const char *cmd) {
  if (!strcmp(cmd, "START")) {
    streaming = true;
    count = 0;
    nextUs = micros();
  } else if (!strcmp(cmd, "STOP")) {
    flushBatch();
    streaming = false;
  } else if (!strcmp(cmd, "HELLO")) {
    if (chip == CHIP_NONE) { detectChip(); applyMode(); }
    sendHello();
  } else if (!strcmp(cmd, "FORCE INA219")) {
    // explicit opt-in: take the first device that answers and treat it as an INA219
    for (uint8_t a = 0x40; a <= 0x4F; a++) {
      if (probe(a)) {
        addr = a;
        chip = CHIP_INA219;
        applyMode();
        char msg[80];
        snprintf(msg, sizeof(msg), "Forced INA219 at 0x%02X: make sure that's really what's wired", a);
        sendText(0x03, msg);
        break;
      }
    }
    sendHello();
  } else if (!strcmp(cmd, "MODE FAST") || !strcmp(cmd, "MODE NORMAL")) {
    flushBatch();
    fastMode = !strcmp(cmd, "MODE FAST");
    applyMode();
    sendHello();
    nextUs = micros();
  } else {
    sendText(0x03, "unknown command");
  }
}

static void pollSerial() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      line[lineLen] = 0;
      for (uint8_t k = 0; k < lineLen; k++) line[k] = toupper(line[k]);
      if (lineLen) handleCommand(line);
      lineLen = 0;
    } else if (lineLen < sizeof(line) - 1) {
      line[lineLen++] = c;
    }
  }
}

// ------------------------------------------------------------- arduino api
void setup() {
#if defined(ESP32)
  Serial.setTxBufferSize(4096);
#endif
  Serial.begin(BAUD);
  Wire.begin(PIN_SDA, PIN_SCL);
  Wire.setClock(I2C_HZ);
  delay(50);
  detectChip();
  applyMode();
  sendHello();
}

void loop() {
  pollSerial();
  if (!streaming || chip == CHIP_NONE) return;
  uint32_t now = micros();
  if ((int32_t)(now - nextUs) >= 0) {
    nextUs += periodUs;
    if ((int32_t)(now - nextUs) > (int32_t)(4 * periodUs)) nextUs = now + periodUs;   // fell behind: resync
    takeSample();
  }
  if (count && (uint32_t)(now - batchStartUs) > FLUSH_US) flushBatch();
}
