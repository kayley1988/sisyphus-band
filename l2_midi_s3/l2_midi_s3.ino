/*
 * ============================================================
 * 无障碍吉他 · L2 固件（ESP32-S3 版）：BLE-MIDI 六弦控制器
 * ============================================================
 *
 * 【与 WROOM 版的区别】
 *   1. 触摸引脚全部换成 S3 的电容触摸通道（T1~T14 = GPIO1~GPIO14）
 *   2. 新增「静音」触摸区（硬规则④：她随时能停）
 *   3. 每通道独立灵敏度系数（铜箔大小不一样，统一阈值会误触）
 *
 * 【零采购清单】你手上已有 S3 开发板，这条路上只需要再补：
 *   · 铜箔胶带 1 卷（或任何金属薄片）
 *   · 杜邦线 7 根（公对母）
 *   合计不到 ¥10。喇叭 / 麦克风 / 屏幕这一版都还用不到，后面几步才接。
 *
 * 【接线 —— 7 个触摸点】
 *   弦/功能        GPIO    S3 触摸通道
 *   6弦E  (最粗)   GPIO1   T1
 *   5弦A           GPIO2   T2
 *   4弦D           GPIO4   T4
 *   3弦G           GPIO5   T5
 *   2弦B           GPIO6   T6
 *   1弦e  (最细)   GPIO7   T7
 *   静音（全停）   GPIO8   T8
 *
 *   ⚠️ GPIO3 不能用（S3 的 strapping 引脚，接了会开不了机）
 *   ⚠️ GPIO0 / 45 / 46 同理别碰
 *
 *   做法：杜邦线母头套在排针上，另一头焊/贴一块 2~3cm 见方的铜箔胶带，
 *        铜箔贴在绝缘板上排成一列 —— 这就是她的七个"琴弦键"。
 *        先接 1 根线就能测，不用一次全接。
 *
 * 【依赖】
 *   · Arduino IDE → 开发板管理器装 "esp32" by Espressif
 *     ⚠️ 请装 2.0.x（推荐 2.0.17）。3.x 把 touchRead() 废弃了，会编译报错。
 *   · 库管理器装 "BLE-MIDI" by lathoub（会自动带上依赖）
 *
 * 【烧录】
 *   · 开发板：ESP32S3 Dev Module
 *   · PSRAM：OPI PSRAM（N16R8 板子选这个；如果是 N8R2 选 QSPI）
 *   · Flash Size：16MB
 *   · 端口选好直接烧
 *
 * 【验证 —— 不需要任何配件，先跑通再说】
 *   1. 烧录后打开串口监视器（115200），应打印 7 个通道的基线值
 *   2. 手指按住某块铜箔 → 串口出现 "[MIDI] ON ... 音高=.." 
 *   3. 手机装 MIDI Wrench（iOS）/ MIDI BLE Connect（安卓）→ 连接"无障碍吉他"
 *   4. 再按铜箔 → App 上跳出正确的音名（E2 A2 D3 G3 B3 E4）
 *   5. 用 iPad GarageBand 连一次，按铜箔就出声 —— L2 就成了
 *   6. 串口发 't' 会依次发 6 个空弦音（不接任何外设就能验证蓝牙通不通）
 *
 * 【后续三步（这一版还没做）】
 *   第 2 步：接喇叭（I2S + MAX98357 功放）→ 装置自己出声，不依赖 iPad
 *   第 3 步：接麦克风（I2S + INMP441）→ 检测她拨弦的瞬态，让 NoteOn
 *            由"右手拨弦"触发，而不是由触摸触发（哲学上最干净的解）
 *   第 4 步：接屏幕 → 显示当前音名 / 把位 / 当前模式
 * ============================================================
 */

#include <BLEMidi.h>

// ===================== 配置 =====================
const char* DEVICE_NAME = "无障碍吉他";

const uint8_t NUM_STR  = 6;
const uint8_t MUTE_PIN = 8;
const uint8_t NUM_TOUCH = NUM_STR + 1;   // 6 弦 + 1 静音

// ESP32-S3 电容触摸引脚（T1~T14 = GPIO1~GPIO14）
// 顺序：6弦E → 1弦e，最后一个是静音
const uint8_t TOUCH_PIN[NUM_TOUCH] = {1, 2, 4, 5, 6, 7, 8};
const char*   TOUCH_NAME[NUM_TOUCH] = {"6弦E", "5弦A", "4弦D", "3弦G", "2弦B", "1弦e", "静音"};

// 标准调弦 EADGBE 的空弦 MIDI 音高
// 6弦 E2=40 / 5弦 A2=45 / 4弦 D3=50 / 3弦 G3=55 / 2弦 B3=59 / 1弦 E4=64
const uint8_t OPEN_PITCH[NUM_STR] = {40, 45, 50, 55, 59, 64};

// ===================== 参数 =====================
// 判定：读数降到「基线 × ratio」以下算按住。
// ratio 每个通道独立——铜箔越大，触摸时掉得越多，ratio 可以更接近 1。
// 先用 0.55 跑，哪个通道误触就把它调小（比如 0.4），哪个不灵就调大（0.7）
float TOUCH_RATIO[NUM_TOUCH] = {0.55, 0.55, 0.55, 0.55, 0.55, 0.55, 0.55};

const uint8_t  DEBOUNCE_MS   = 25;
const uint16_t CAL_SAMPLES   = 40;
const uint8_t  MIDI_CHANNEL  = 0;
const uint8_t  FIXED_VELOCITY = 100;

// 力度用触摸深度近似（默认关，稳定优先）
const bool USE_TOUCH_VELOCITY = false;

// ===================== 状态 =====================
uint16_t baseline[NUM_TOUCH]     = {0};
uint16_t thresholdVal[NUM_TOUCH] = {0};

bool     isDown[NUM_TOUCH]     = {false};
bool     lastRaw[NUM_TOUCH]    = {false};
uint32_t rawChangeTs[NUM_TOUCH] = {0};

uint8_t  activeNote[NUM_STR] = {0};
bool     noteHeld[NUM_STR]   = {false};

uint8_t  currentFret = 0;
uint32_t noteCount   = 0;
bool     muted       = false;

// ===================== 触摸 =====================
void calibrateTouch() {
  Serial.println("\n[标定] 采集触摸基线 —— 此刻不要碰任何铜箔…");
  delay(800);
  for (uint8_t i = 0; i < NUM_TOUCH; i++) {
    uint32_t sum = 0;
    for (uint16_t k = 0; k < CAL_SAMPLES; k++) {
      sum += touchRead(TOUCH_PIN[i]);
      delay(5);
    }
    baseline[i]     = sum / CAL_SAMPLES;
    thresholdVal[i] = (uint16_t)(baseline[i] * TOUCH_RATIO[i]);
    Serial.printf("  %-6s GPIO%-2d  基线=%4u  阈值=%4u\n",
                  TOUCH_NAME[i], TOUCH_PIN[i], baseline[i], thresholdVal[i]);
  }
  Serial.println("[标定] 完成。手指按住铜箔测试，或串口发 't' 听音阶。\n");
}

bool readTouched(uint8_t i) {
  return touchRead(TOUCH_PIN[i]) < thresholdVal[i];
}

uint8_t velocityFromDepth(uint8_t i) {
  if (!USE_TOUCH_VELOCITY) return FIXED_VELOCITY;
  uint16_t v = touchRead(TOUCH_PIN[i]);
  if (v >= thresholdVal[i]) return 40;
  long vel = 40L + (long)(thresholdVal[i] - v) * 87L / (long)thresholdVal[i];
  return (uint8_t)(vel > 127 ? 127 : vel);
}

// ===================== MIDI =====================
const char* pitchName(uint8_t n) {
  static const char* names[12] = {"C","C#","D","D#","E","F","F#","G","G#","A","A#","B"};
  static char buf[8];
  snprintf(buf, sizeof(buf), "%s%d", names[n % 12], (n / 12) - 1);
  return buf;
}

void sendNoteOn(uint8_t str, uint8_t note, uint8_t vel) {
  if (!BLEMidiServer.isConnected()) {
    Serial.printf("[MIDI] 未连接，丢弃 NoteOn %s note=%u\n", TOUCH_NAME[str], note);
    return;
  }
  BLEMidiServer.noteOn(MIDI_CHANNEL, note, vel);
  Serial.printf("[MIDI] ON   %-5s 把位%d 音高=%3u (%s) vel=%u\n",
                TOUCH_NAME[str], currentFret, note, pitchName(note), vel);
}

void sendNoteOff(uint8_t str, uint8_t note) {
  if (!BLEMidiServer.isConnected()) return;
  BLEMidiServer.noteOff(MIDI_CHANNEL, note, 0);
  Serial.printf("[MIDI] OFF  %-5s 音高=%3u (%s)\n", TOUCH_NAME[str], note, pitchName(note));
}

// 压弦动作 —— 纯控制器模式下为空；接执行机构后在这里下发
void pressString(uint8_t s)   { (void)s; }
void releaseString(uint8_t s) { (void)s; }

// ===================== 事件 =====================
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

void muteAll() {
  for (uint8_t s = 0; s < NUM_STR; s++) {
    if (noteHeld[s]) { sendNoteOff(s, activeNote[s]); noteHeld[s] = false; }
    releaseString(s);
  }
  Serial.println("[静音] 全部松开");
}

// ===================== 串口 =====================
void printStatus() {
  Serial.println("---- 状态 ----");
  Serial.printf("设备名   : %s\n", DEVICE_NAME);
  Serial.printf("BLE 连接 : %s\n", BLEMidiServer.isConnected() ? "已连接" : "未连接");
  Serial.printf("当前把位 : %d\n", currentFret);
  Serial.printf("本次发过 : %lu 个音\n", (unsigned long)noteCount);
  Serial.println("指令：t=音阶  [ ]=减/加把位  0~9=设把位  m=静音  s=状态  b=重标定");
}

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
    if (c == 't')      playScaleTest();
    else if (c == '[') { if (currentFret > 0)  currentFret--; Serial.printf("[把位] %d\n", currentFret); }
    else if (c == ']') { if (currentFret < 12) currentFret++; Serial.printf("[把位] %d\n", currentFret); }
    else if (c == 'm') muteAll();
    else if (c == 's') printStatus();
    else if (c == 'b') calibrateTouch();
    else if (c >= '0' && c <= '9') { currentFret = c - '0'; Serial.printf("[把位] %d\n", currentFret); }
  }
}

// ===================== 主程序 =====================
void setup() {
  Serial.begin(115200);
  delay(300);

  Serial.println("\n=== 无障碍吉他 · L2（ESP32-S3）===");
  Serial.printf("芯片: %s  核心: %d  PSRAM: %s\n",
                ESP.getChipModel(), ESP.getCoreVersion(), psramFound() ? "有" : "无");

  BLEMidiServer.begin(DEVICE_NAME);
  BLEMidiServer.enableDebugging();
  Serial.printf("蓝牙已启动：%s（在手机 / iPad 的 MIDI App 里连接）\n", DEVICE_NAME);

  calibrateTouch();
  printStatus();
}

void loop() {
  handleSerial();

  uint32_t now = millis();

  for (uint8_t i = 0; i < NUM_TOUCH; i++) {
    bool raw = readTouched(i);

    if (raw != lastRaw[i]) {
      rawChangeTs[i] = now;
      lastRaw[i]     = raw;
    } else if (now - rawChangeTs[i] > DEBOUNCE_MS && isDown[i] != raw) {
      isDown[i] = raw;

      if (i == NUM_STR) {              // 静音区
        if (raw) { muted = true;  muteAll(); }
        else       muted = false;
      } else if (raw) {                // 弦按下
        if (!muted) onStringDown(i);
      } else {                         // 弦松开
        onStringUp(i);
      }
    }
  }
}
