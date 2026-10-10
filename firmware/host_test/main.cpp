// Runs the real sketch against the fake bus. Usage: host_meter <226|219|0|9685> > out.bin
#include "Arduino.h"
#include "Wire.h"
#include <cstdlib>

uint64_t g_now_us = 0;
int g_chip = 226;
HostSerial Serial;
HostWire Wire;

void setup();
void loop();

static void run_for(double seconds) {
  uint64_t end = g_now_us + (uint64_t)(seconds * 1e6);
  while (g_now_us < end) { loop(); g_now_us += 2; }    // ~2 us per idle loop pass
}

int main(int argc, char **argv) {
  g_chip = argc > 1 ? atoi(argv[1]) : 226;
  setup();
  if (g_chip == 2191) { Serial.rx += "FORCE INA219\n"; run_for(0.01); }
  Serial.rx += "start\n"; run_for(0.5);                 // lower case on purpose: firmware upper-cases
  Serial.rx += "MODE FAST\n"; run_for(0.3);
  Serial.rx += "STOP\nHELLO\nBOGUS\n"; run_for(0.05);
  fprintf(stderr, "decoy_writes=%d\n", Wire.writes_to_decoy);
  return 0;
}
