/*
 * ============================================================
 * 无障碍吉他 · L2 阶段固件：BLE-MIDI 六弦控制器
 * ============================================================
 *
 * 【这一版要解决什么】
 *   把装置变成一台 MIDI 控制器：她点一根"弦"，iPad / 手机 / 电脑上的
 *   音源就响一个音，宿主同时可以放伴奏。她自己依然是唯一决定弹什么的人。
 *
 * 【零采购】——不需要电磁铁、不需要滑轨、不需要 TTP223
 *   用的是 ESP32 自带的电容触摸功能（touchRead），
 *   接 6 根导线（末端贴铜箔胶带，或直接捏线头）就能用。
 *
 * 【接线】只用一块 ESP32 开发板
 *   ┌ 弦序 ── GPIO ── ESP32 触摸通道
 *   │ 6弦E ── GPIO4  ── T0
 *   │ 5弦A ── GPIO32 ── T9
 *   │ 4弦D ── GPIO33 ── T8
 *   │ 3弦G ── GPIO27 ── T7
 *   │ 2弦B ── GPIO14 ── T6
 *   │ 1弦e ── GPIO13 ── T4
 *   └（避开 GPIO12 / GPIO15 —— 那两个是 strapping 引脚，接东西会开不了机）
 *
 * 【依赖】Arduino IDE 库管理器安装：
 *   1. ESP32 board support（开发板管理器装 "esp32" by Espressif）
 *      ⚠️ 建议装 2.0.17。3.x 里 touchRead() 被标记废弃，若编译报 touch 相关
 *         错误，就在开发板管理器里把 esp32 退回 2.0.17。
 *   2. "BLE-MIDI" by lathoub（搜 BLE-MIDI 即可，会自动带上 ArduinoBLE 依赖）
 *
 * 【烧录】开发板选 ESP32 Dev Module，波特率 115200
 *
 * 【验证步骤 —— 不需要任何硬件配件】
 *   1. 烧录后串口应打印 6 个触摸通道的基线值
 *   2. 手机装一个 BLE-MIDI 监视 App（iOS 免费：MIDI Wrench / Bluetooth MIDI Monitor；
 *      安卓：MIDI BLE Connect）
 *   3. 在 App 里连接设备 "无障碍吉他"（或你改的名字）
 *   4. 用手指捏住某根线头 —— App 上应该跳出 Note On / Note Off，音名正确
 *   5. 再用 iPad 的 GarageBand 连一次：捏线头就出声，这就是全部魔法
 *
 * 【音高校验用】串口发 't' → 从 6 弦到 1 弦依次发 6 个音（E2 A2 D3 G3 B3 E4）
 *
 * 【与真机的衔接】等压弦机构做好后，把 pressString() / releaseString()
 *   里加一句伺服或电磁铁的调用即可，MIDI 这一层完全不用改。
 * ============================================================
 */

#include <BLEMidi.h>

// ===================== 配置 =====================
const char* DEVICE_NAME = "无障碍吉他";

const uint8_t NUM_STR = 6;

// ESP32 内置电容触摸引脚（避开 strapping 的 GPIO12/15）
// 顺序：6弦E → 1弦e
const uint8_t TOUCH_PIN[NUM_STR] = {4, 32, 33, 27, 14, 13};
const char*   STR_NAME[NUM_STR]  = {"6弦E", "5弦A", "4弦D", "3弦G", "2弦B", "1弦e"};

// 标准调弦 EADGBE 的空弦 MIDI 音高
// 6弦 E2 = 40 / 5弦 A2 = 45 / 4弦 D3 = 50 / 3弦 G3 = 55 / 2弦 B3 = 59 / 1弦 E4 = 64
const uint8_t OPEN_PITCH[NUM_STR] = {40, 45, 50, 55, 59, 64};

// ===================== 参数 =====================
// 触摸判定：读数降到「基线 × TOUCH_RATIO」以下算按住
// 基线在开机时自动标定，所以不需要事先知道具体读数
const float TOUCH_RATIO   = 0.55;
const uint8_t  DEBOUNCE_MS = 25;    // 状态稳定时间
const uint16_t CAL_SAMPLES = 40;    // 基线采样次数

const uint8_t  MIDI_CHANNEL = 0;
const uint8_t  FIXED_VELOCITY = 100;   // 固定力度（她拨弦的力度装置测不到）

// 力度感应用触摸深度近似（读数掉得越深 = 按得越实）。
// 精度有限，想稳定就设 false。
const bool USE_TOUCH_VELOCITY = false;

// ===================== 状态 =====================
uint16_t baseline[NUM_STR]     = {0};
uint16_t thresholdVal[NUM_STR] = {0};

bool     isDown[NUM_STR]    = {false};   // 防抖后的稳定状态
bool     lastRaw[NUM_STR]   = {false};
uint32_t rawChangeTs[NUM_STR] = {0};

uint8_t  activeNote[NUM_STR] = {0};      // 该弦当前发出的音高
bool     noteHeld[NUM_STR]   = {false};  // 是否已发过 NoteOn 未发 NoteOff

uint8_t  currentFret = 0;                // 当前把位（0 = 空弦，1~12 = 第 n 品）
uint32_t noteCount   = 0;                // 本次上电发出的音数（串口统计用）

// ===================== 触摸 =====================
void calibrateTouch() {
  Serial.println("\n[标定] 正在采集触摸基线 —— 此刻不要碰任何导线…");
  delay(600);
  for (uint8_t i = 0; i < NUM_STR; i++) {
    uint32_t sum = 0;
    for (uint16_t k = 0; k < CAL_SAMPLES; k++) {
      sum += touchRead(TOUCH_PIN[i]);
      delay(5);
    }
    baseline[i]     = sum / CAL_SAMPLES;
    thresholdVal[i] = (uint16_t)(baseline[i] * TOUCH_RATIO);
    Serial.printf("  %s GPIO%d  基线=%u  触发阈值=%u\n",
                  STR_NAME[i], TOUCH_PIN[i], baseline[i], thresholdVal[i]);
  }
  Serial.println("[标定] 完成。手指捏线头测试，或串口发 't' 听音阶。\n");
}

// 返回该弦是否处于「按住」状态
bool readTouched(uint8_t i) {
  return touchRead(TOUCH_PIN[i]) < thresholdVal[i];
}

// 触摸深度 → 力度（非线性，掉得越深越大）
uint8_t velocityFromDepth(uint8_t i) {
  if (!USE_TOUCH_VELOCITY) return FIXED_VELOCITY;
  uint16_t v = touchRead(TOUCH_PIN[i]);
  if (v >= thresholdVal[i]) return 40;
  // thresholdVal → 40 力度；读数接近 0 → 127 力度
  long vel = 40L + (long)(thresholdVal[i] - v) * 87L / (long)thresholdVal[i];
  if (vel > 127) vel = 127;
  return (uint8_t)vel;
}

// ===================== MIDI =====================
// MIDI 音高 → 音名（调试打印用）
const char* pitchName(uint8_t n) {
  static const char* names[12] = {"C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"};
  static char buf[8];
  snprintf(buf, sizeof(buf), "%s%d", names[n % 12], (n / 12) - 1);
  return buf;
}

void sendNoteOn(uint8_t str, uint8_t note, uint8_t vel) {
  if (!BLEMidiServer.isConnected()) {
    Serial.printf("[MIDI] 未连接，丢弃 NoteOn %s note=%u\n", STR_NAME[str], note);
    return;
  }
  BLEMidiServer.noteOn(MIDI_CHANNEL, note, vel);
  Serial.printf("[MIDI] ON   %s  把位%d  音高=%u (%s)  vel=%u\n",
                STR_NAME[str], currentFret, note, pitchName(note), vel);
}

void sendNoteOff(uint8_t str, uint8_t note) {
  if (!BLEMidiServer.isConnected()) return;
  BLEMidiServer.noteOff(MIDI_CHANNEL, note, 0);
  Serial.printf("[MIDI] OFF  %s  音高=%u (%s)\n", STR_NAME[str], note, pitchName(note));
}

// 压弦动作 —— 现在为空（纯控制器模式）；接上执行机构后在这里下发
void pressString(uint8_t s)   { (void)s; /* TODO: 电磁铁/舵机 压下第 s 弦 */ }
void releaseString(uint8_t s) { (void)s; /* TODO: 松开第 s 弦 */ }

// ===================== 事件处理 =====================
void onStringDown(uint8_t s) {
  uint8_t note = OPEN_PITCH[s] + currentFret;
  if (note > 127) return;
  pressString(s);
  activeNote[s] = note;
  noteHeld[s]   = true;
  noteCount++;
  sendNoteOn(s, note, velocityFromDepth(s));
}

void onStringUp(uint8_t s) {
  releaseString(s);
  if (noteHeld[s]) {
    sendNoteOff(s, activeNote[s]);
    noteHeld[s] = false;
  }
}

// ===================== 串口 =====================
void printStatus() {
  Serial.println("---- 状态 ----");
  Serial.printf("设备名   : %s\n", DEVICE_NAME);
  Serial.printf("BLE 连接 : %s\n", BLEMidiServer.isConnected() ? "已连接" : "未连接");
  Serial.printf("当前把位 : %d\n", currentFret);
  Serial.printf("本次发过 : %lu 个音\n", (unsigned long)noteCount);
  Serial.println("指令：t=听音阶  [ / ]=减/加把位  0~9=直接设把位  s=状态");
}

// 依次发 6 个空弦音（不依赖硬件，验证 BLE 通不通）
void playScaleTest() {
  Serial.println("[自检] 依次发 6 个空弦音…");
  for (uint8_t s = 0; s < NUM_STR; s++) {
    sendNoteOn(s, OPEN_PITCH[s] + currentFret, FIXED_VELOCITY);
    delay(350);
    sendNoteOff(s, OPEN_PITCH[s] + currentFret);
    delay(80);
  }
  Serial.println("[自检] 结束");
}

void handleSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 't') {
      playScaleTest();
    } else if (c == '[') {
      if (currentFret > 0) currentFret--;
      Serial.printf("[把位] %d\n", currentFret);
    } else if (c == ']') {
      if (currentFret < 12) currentFret++;
      Serial.printf("[把位] %d\n", currentFret);
    } else if (c == 's') {
      printStatus();
    } else if (c >= '0' && c <= '9') {
      currentFret = c - '0';
      Serial.printf("[把位] %d\n", currentFret);
    }
  }
}

// ===================== 主程序 =====================
void setup() {
  Serial.begin(115200);
  delay(300);

  Serial.println("\n=== 无障碍吉他 · L2 BLE-MIDI 控制器 ===");

  BLEMidiServer.begin(DEVICE_NAME);
  BLEMidiServer.enableDebugging();   // 想安静就把这行删掉
  Serial.printf("蓝牙设备已启动：%s（在手机 / iPad 的 MIDI App 里连接它）\n", DEVICE_NAME);

  calibrateTouch();
  printStatus();
}

void loop() {
  handleSerial();

  uint32_t now = millis();

  for (uint8_t s = 0; s < NUM_STR; s++) {
    bool raw = readTouched(s);

    if (raw != lastRaw[s]) {          // 电平变化，重记防抖计时
      rawChangeTs[s] = now;
      lastRaw[s]     = raw;
    } else if (now - rawChangeTs[s] > DEBOUNCE_MS && isDown[s] != raw) {
      isDown[s] = raw;                // 稳定后确认状态翻转
      if (raw) onStringDown(s);
      else     onStringUp(s);
    }
  }
}
