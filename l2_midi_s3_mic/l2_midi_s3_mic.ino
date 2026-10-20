/*
 * ============================================================
 * 无障碍吉他 · L2 固件（ESP32-S3 + 麦克风版）
 *   —— 触摸选弦 · 拨弦发声 ——
 * ============================================================
 *
 * 【这一版解决了什么】
 *   上一版：她点触摸 = 出声。问题是「出声」跟右手拨弦脱钩了。
 *   这一版：她按着触摸区（选定哪根弦）→ 右手拨弦 → 麦克风听到拨弦的
 *           瞬态，才发 NoteOn。
 *
 *   于是「她没拨，一个音都不响」不再是固件里的一句判断，
 *   而是物理事实：麦克风没听到拨弦声，NoteOn 代码根本不执行。
 *
 *   附带白捡一个：力度（velocity）由拨弦的强度决定 —— 强弱真的归她。
 *
 * 【防自激（重要）】
 *   装置贴着琴、功放就在旁边，麦克风一定会听到功放的声音。
 *   防法：只有「有触摸武装」的时候，拨弦瞬态才发 NoteOn。
 *   功放在响、她手没按 → 麦克风听到了也不发一个音。
 *
 * 【两种触发模式】
 *   模式 1（触摸触发）  手指按铜箔 → 立刻出声。没有麦克风时用这个。
 *   模式 2（拨弦触发）  手指按铜箔 = 选弦（武装）→ 右手拨弦 → 出声。
 *                       松手 = 该弦 NoteOff。同时武装多根 = 扫出一个和弦。
 *   开机自动判断麦克风在不在，自动选模式，也可串口手动切。
 *
 * ============================================================
 * 【接线 A —— 7 个触摸点（必需）】
 *   弦/功能        GPIO    S3 触摸通道
 *   6弦E  (最粗)   GPIO1   T1
 *   5弦A           GPIO2   T2
 *   4弦D           GPIO4   T4
 *   3弦G           GPIO5   T5
 *   2弦B           GPIO6   T6
 *   1弦e  (最细)   GPIO7   T7
 *   静音（全停）   GPIO8   T8
 *
 *   ⚠️ GPIO0 / 3 / 45 / 46 是 strapping 引脚，接了开不了机。
 *   ⚠️ GPIO19 / 20 是 USB D-/D+，接 USB 时别用。
 *   ⚠️ GPIO26~37 被 Flash / PSRAM 占用，别用。
 *
 * 【接线 B —— I2S 麦克风 INMP441（模式 2 用）】
 *   INMP441        ESP32-S3
 *   VDD   ──────── 3.3V      （千万别接 5V，会烧）
 *   GND   ──────── GND
 *   SCK   ──────── GPIO15    （位时钟 BCLK）
 *   WS    ──────── GPIO16    （帧时钟 LRCLK）
 *   SD    ──────── GPIO17    （数据输出）
 *   L/R   ──────── GND       （必须接，悬空会随机丢帧 —— 选左声道）
 *
 *   ⚠️ 如果板子上的麦克风已经焊好了，请照板子的实际引脚改下面
 *      MIC_SCK / MIC_WS / MIC_SD 三个 #define，别硬接。
 *   ⚠️ 麦克风不要对着功放喇叭装，尽量贴近琴弦、远离喇叭。
 *
 * 【接线 C —— 喇叭 MAX98357A（第 2 步，本版未启用）】
 *   VIN→5V  GND→GND  BCLK→任意空闲GPIO  LRC→任意空闲GPIO
 *   DIN→任意空闲GPIO  GAIN→GND  SD→3.3V（不拉高不出声）
 *
 * ============================================================
 * 【依赖】
 *   · Arduino IDE → 开发板管理器装 "esp32" by Espressif
 *     ⚠️ 必须 2.0.x（推荐 2.0.17）。3.x 把 touchRead() 废弃了，编译报错。
 *   · 库管理器装 "BLE-MIDI" by lathoub（自动带依赖）
 *
 * 【烧录】
 *   开发板 ESP32S3 Dev Module / PSRAM = OPI PSRAM（N16R8）
 *   Flash Size 16MB / 端口选对
 *
 * 【验证】
 *   1. 烧录后开串口（115200），会打印触摸基线 + 麦克风探测结果
 *   2. 串口发 'g' → 看麦克风电平。不说话时 rms 应是个小数字（底噪），
 *      拨一下弦 → peak 应明显跳起来。跳不起来就是增益/接线问题。
 *   3. 手指按住 6弦E 铜箔（串口显示 armed），拨一下 6 弦 →
 *      串口出现 "[拨弦] 触发 1 弦 峰值=.. 力度=.."
 *   4. 手机装 MIDI Wrench（iOS）/ MIDI BLE Connect（安卓）→ 连 "无障碍吉他"
 *   5. 松手 → NoteOff。全程不按铜箔只拨弦 → 串口应打印"无弦武装，不发"
 *
 * 【串口指令】
 *   t=音阶自检   1=触摸触发模式   2=拨弦触发模式
 *   [ ]=减/加把位   0~9=直设把位   m=静音   s=状态   b=重标定触摸
 *   g=打印麦克风电平（连着打印，再按 g 停止）   c=重标定麦克风噪声底
 *   + - =拨弦灵敏度门槛加/减（数值越小越灵敏）
 * ============================================================
 */

#include <BLEMidi.h>
#include "driver/i2s.h"

// ===================== 设备 =====================
const char* DEVICE_NAME  = "无障碍吉他";

const uint8_t NUM_STR   = 6;
const uint8_t NUM_TOUCH = NUM_STR + 1;          // 6 弦 + 1 静音

const uint8_t TOUCH_PIN[NUM_TOUCH]  = {1, 2, 4, 5, 6, 7, 8};
const char*   TOUCH_NAME[NUM_TOUCH] = {"6弦E", "5弦A", "4弦D", "3弦G", "2弦B", "1弦e", "静音"};
const uint8_t MUTE_IDX              = NUM_STR;  // 静音区下标

// 标准调弦 EADGBE：E2=40 A2=45 D3=50 G3=55 B3=59 E4=64
const uint8_t OPEN_PITCH[NUM_STR] = {40, 45, 50, 55, 59, 64};

// ===================== 麦克风接线（按实际板子改这里）=====================
#define MIC_SCK 15      // BCLK 位时钟
#define MIC_WS  16      // LRCLK 帧时钟
#define MIC_SD  17      // 数据输入
#define MIC_PORT I2S_NUM_0

// ===================== 可调参数 =====================
float   TOUCH_RATIO[NUM_TOUCH] = {0.55, 0.55, 0.55, 0.55, 0.55, 0.55, 0.55};

const uint8_t  DEBOUNCE_MS = 25;
const uint16_t CAL_SAMPLES = 40;

const uint8_t  MIDI_CHANNEL   = 0;
const uint8_t  FIXED_VELOCITY = 100;

// —— 拨弦检测参数 ——
const uint16_t BLOCK_SAMPLES = 64;      // 4ms @16kHz，响应够快
const uint16_t ABS_GATE_INIT = 260;     // 绝对门槛：低于它一律不算拨弦（抗底噪）
const float    ATTACK_K      = 3.0f;    // 相对噪声底的倍数
const uint16_t REFRACTORY_MS = 70;      // 两次触发最小间隔（防一次拨弦触发多次）
const uint32_t TRIG_HOLD_MS  = 200;     // 触发后冻结噪声底更新的时间
const uint16_t VEL_MIN_PEAK  = 1500;    // 力度映射下限
const uint16_t VEL_MAX_PEAK  = 9000;    // 力度映射上限
const uint8_t  VEL_MIN       = 50;
const uint8_t  VEL_MAX       = 127;

enum TrigMode { TRIG_TOUCH = 1, TRIG_STRUM = 2 };

// ===================== 状态 =====================
uint16_t baseline[NUM_TOUCH]     = {0};
uint16_t thresholdVal[NUM_TOUCH] = {0};

bool     isDown[NUM_TOUCH]      = {false};
bool     lastRaw[NUM_TOUCH]     = {false};
uint32_t rawChangeTs[NUM_TOUCH] = {0};

bool     armed[NUM_STR]     = {false};   // 触摸按着 = 选中这根弦（模式2）
bool     sounding[NUM_STR]  = {false};   // 当前正在响
uint8_t  activeNote[NUM_STR] = {0};

uint8_t  currentFret = 0;
uint32_t noteCount   = 0;
bool     muted       = false;

TrigMode trigMode = TRIG_STRUM;

bool     micPresent   = false;
uint32_t noiseFloor   = 60;
uint16_t absGate      = ABS_GATE_INIT;
uint32_t lastTrigTs   = 0;
uint32_t trigFreezeTs = 0;
bool     levelDebug   = false;
uint32_t lastDebugTs  = 0;

int16_t  i2sBuf[BLOCK_SAMPLES];
uint32_t strumCount = 0;

// ===================== 触摸 =====================
void calibrateTouch() {
  Serial.println("\n[标定] 采集触摸基线 —— 此刻不要碰任何铜箔…");
  delay(600);
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
  Serial.println("[标定] 完成。\n");
}

bool readTouched(uint8_t i) {
  return touchRead(TOUCH_PIN[i]) < thresholdVal[i];
}

// ===================== 麦克风（I2S）=====================
void initMic() {
  i2s_config_t cfg = {
    .mode                 = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate          = 16000,
    .bits_per_sample      = I2S_BITS_PER_SAMPLE_16BIT,
    .channel_format       = I2S_CHANNEL_FMT_ONLY_LEFT,   // INMP441 的 L/R 接 GND
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags     = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count        = 4,
    .dma_buf_len          = 256,
    .use_apll             = false,
    .tx_desc_auto_clear   = false,
    .fixed_mclk           = 0
  };
  i2s_pin_config_t pins = {
    .bck_io_num   = MIC_SCK,
    .ws_io_num    = MIC_WS,
    .data_out_num = I2S_PIN_NO_CHANGE,
    .data_in_num  = MIC_SD
  };
  esp_err_t e1 = i2s_driver_install(MIC_PORT, &cfg, 0, NULL);
  esp_err_t e2 = i2s_set_pin(MIC_PORT, &pins);
  if (e1 != ESP_OK || e2 != ESP_OK) {
    Serial.printf("[麦克风] I2S 初始化失败（%d / %d）—— 自动退回触摸触发模式\n", e1, e2);
    micPresent = false;
    trigMode   = TRIG_TOUCH;
    return;
  }

  // 探测：读 1 秒，全是 0 说明麦克风没接上
  Serial.println("[麦克风] 探测中（1 秒，请保持安静）…");
  uint32_t nonzero = 0, total = 0, sumAbs = 0;
  uint32_t until = millis() + 1000;
  while (millis() < until) {
    size_t n = 0;
    if (i2s_read(MIC_PORT, i2sBuf, sizeof(i2sBuf), &n, pdMS_TO_TICKS(20)) == ESP_OK && n > 0) {
      uint16_t cnt = n / 2;
      for (uint16_t k = 0; k < cnt; k++) {
        total++;
        int32_t v = i2sBuf[k] >> 8;          // INMP441 有效位在高位，压到 8bit 量级
        if (v != 0) nonzero++;
        sumAbs += (v < 0 ? -v : v);
      }
    }
  }
  if (total == 0 || nonzero * 100 < total) {
    Serial.println("[麦克风] 没读到有效数据 —— 检查接线，先用触摸触发模式");
    micPresent = false;
    trigMode   = TRIG_TOUCH;
  } else {
    noiseFloor = (sumAbs / total) + 20;
    Serial.printf("[麦克风] 正常。底噪≈%lu  拨弦门槛=%u\n",
                  (unsigned long)noiseFloor, absGate);
    micPresent = true;
    trigMode   = TRIG_STRUM;
  }
}

void recalibNoiseFloor() {
  Serial.println("[麦克风] 重标定噪声底（2 秒，请安静）…");
  uint32_t sum = 0, cnt = 0, until = millis() + 2000;
  while (millis() < until) {
    size_t n = 0;
    if (i2s_read(MIC_PORT, i2sBuf, sizeof(i2sBuf), &n, pdMS_TO_TICKS(20)) == ESP_OK && n > 0) {
      for (uint16_t k = 0; k < n / 2; k++) {
        int32_t v = i2sBuf[k] >> 8;
        sum += (v < 0 ? -v : v);
        cnt++;
      }
    }
  }
  noiseFloor = cnt ? (sum / cnt) + 20 : 60;
  Serial.printf("[麦克风] 新底噪≈%lu  当前拨弦门槛=%u\n",
                (unsigned long)noiseFloor, absGate);
}

// ===================== MIDI =====================
const char* pitchName(uint8_t n) {
  static const char* names[12] = {"C","C#","D","D#","E","F","F#","G","G#","A","A#","B"};
  static char buf[8];
  snprintf(buf, sizeof(buf), "%s%d", names[n % 12], (n / 12) - 1);
  return buf;
}

void sendNoteOn(uint8_t s, uint8_t note, uint8_t vel) {
  if (!BLEMidiServer.isConnected()) {
    Serial.printf("[MIDI] 未连接，丢弃 %s note=%u\n", TOUCH_NAME[s], note);
    return;
  }
  BLEMidiServer.noteOn(MIDI_CHANNEL, note, vel);
  Serial.printf("[MIDI] ON   %-5s 把位%d 音高=%3u (%s) vel=%u\n",
                TOUCH_NAME[s], currentFret, note, pitchName(note), vel);
}

void sendNoteOff(uint8_t s, uint8_t note) {
  if (!BLEMidiServer.isConnected()) return;
  BLEMidiServer.noteOff(MIDI_CHANNEL, note, 0);
  Serial.printf("[MIDI] OFF  %-5s 音高=%3u (%s)\n", TOUCH_NAME[s], note, pitchName(note));
}

// 压弦动作钩子 —— 纯控制器模式为空；接执行机构后只改这里
void pressString(uint8_t s)   { (void)s; }
void releaseString(uint8_t s) { (void)s; }

void stringOn(uint8_t s, uint8_t vel) {
  uint8_t note = OPEN_PITCH[s] + currentFret;
  if (note > 127) return;
  if (sounding[s]) { sendNoteOff(s, activeNote[s]); sounding[s] = false; }  // 重复拨弦 → 重新起音
  pressString(s);
  activeNote[s] = note;
  sounding[s]   = true;
  noteCount++;
  sendNoteOn(s, note, vel);
}

void stringOff(uint8_t s) {
  releaseString(s);
  if (sounding[s]) {
    sendNoteOff(s, activeNote[s]);
    sounding[s] = false;
  }
}

void muteAll() {
  for (uint8_t s = 0; s < NUM_STR; s++) {
    if (sounding[s]) { sendNoteOff(s, activeNote[s]); sounding[s] = false; }
    releaseString(s);
  }
  Serial.println("[静音] 全部松开");
}

// ===================== 拨弦瞬态检测 =====================
// 返回值：检测到一次拨弦 → true（并给出峰值，用来定力度）
bool detectStrum(uint16_t* outPeak) {
  if (!micPresent) return false;

  size_t n = 0;
  esp_err_t err = i2s_read(MIC_PORT, i2sBuf, sizeof(i2sBuf), &n, pdMS_TO_TICKS(20));
  if (err != ESP_OK || n == 0) return false;

  uint16_t cnt = n / 2;
  uint32_t sum = 0;
  uint16_t peak = 0;
  for (uint16_t k = 0; k < cnt; k++) {
    int32_t v = i2sBuf[k] >> 8;
    uint16_t a = (uint16_t)(v < 0 ? -v : v);
    sum += a;
    if (a > peak) peak = a;
  }
  uint32_t avg = sum / cnt;

  uint32_t now = millis();
  bool attacking = (now - lastTrigTs) < TRIG_HOLD_MS;

  if (levelDebug && now - lastDebugTs > 200) {
    lastDebugTs = now;
    Serial.printf("[电平] 平均=%4lu  峰值=%4u  底噪=%4lu  门槛=%u\n",
                  (unsigned long)avg, peak, (unsigned long)noiseFloor, absGate);
  }

  // 触发判定：够响 + 明显高于底噪 + 过了不应期
  if (avg > absGate &&
      (float)avg > (float)noiseFloor * ATTACK_K &&
      (now - lastTrigTs) > REFRACTORY_MS) {
    lastTrigTs   = now;
    trigFreezeTs = now;
    *outPeak     = peak;
    return true;
  }

  // 没在触发窗口里，慢速跟随底噪
  if (!attacking && (now - trigFreezeTs) > TRIG_HOLD_MS) {
    noiseFloor = (noiseFloor * 31 + avg) / 32;
    if (noiseFloor < 40) noiseFloor = 40;
  }
  return false;
}

uint8_t velocityFromPeak(uint16_t peak) {
  if (peak <= VEL_MIN_PEAK) return VEL_MIN;
  if (peak >= VEL_MAX_PEAK) return VEL_MAX;
  long v = VEL_MIN + (long)(peak - VEL_MIN_PEAK) * (VEL_MAX - VEL_MIN) /
                       (long)(VEL_MAX_PEAK - VEL_MIN_PEAK);
  return (uint8_t)v;
}

// 一次拨弦 → 让所有被"武装"的弦一起起音（选一根=单音，选多根=和弦）
void onStrum(uint16_t peak) {
  uint8_t hit = 0;
  uint8_t vel = velocityFromPeak(peak);
  for (uint8_t s = 0; s < NUM_STR; s++) {
    if (armed[s]) { stringOn(s, vel); hit++; }
  }
  if (hit == 0) {
    Serial.println("[拨弦] 检测到拨弦，但没有弦被选中 → 不发任何音（防自激）");
  } else {
    strumCount++;
    Serial.printf("[拨弦] 触发 %u 根弦  峰值=%u  力度=%u\n", hit, peak, vel);
  }
}

// ===================== 触摸事件 =====================
void onPadDown(uint8_t i) {
  if (i == MUTE_IDX) { muted = true; muteAll(); return; }
  if (muted) return;

  if (trigMode == TRIG_TOUCH) {
    stringOn(i, FIXED_VELOCITY);
  } else {
    armed[i] = true;
    Serial.printf("[选弦] %s 已选中（等右手拨弦）\n", TOUCH_NAME[i]);
  }
}

void onPadUp(uint8_t i) {
  if (i == MUTE_IDX) { muted = false; return; }

  if (trigMode == TRIG_TOUCH) {
    stringOff(i);
  } else {
    armed[i] = false;
    stringOff(i);          // 松手 → 该弦收音
  }
}

// ===================== 串口 =====================
void printStatus() {
  Serial.println("---- 状态 ----");
  Serial.printf("BLE      : %s\n", BLEMidiServer.isConnected() ? "已连接" : "未连接");
  Serial.printf("麦克风   : %s\n", micPresent ? "已接" : "未接");
  Serial.printf("触发模式 : %s\n", trigMode == TRIG_STRUM ? "2 拨弦触发" : "1 触摸触发");
  Serial.printf("当前把位 : %d\n", currentFret);
  Serial.printf("发过音数 : %lu   拨弦次数 : %lu\n",
                (unsigned long)noteCount, (unsigned long)strumCount);
  Serial.printf("噪声底   : %lu   拨弦门槛 : %u\n", (unsigned long)noiseFloor, absGate);
  Serial.print("已选中的弦: ");
  bool any = false;
  for (uint8_t s = 0; s < NUM_STR; s++) if (armed[s]) { Serial.printf("%s ", TOUCH_NAME[s]); any = true; }
  Serial.println(any ? "" : "（无）");
  Serial.println("指令：t=音阶 1=触摸模式 2=拨弦模式 [ ]=把位 0~9=设把位");
  Serial.println("      m=静音 s=状态 b=重标定触摸 g=电平监视 c=重标定麦克风 +- =门槛");
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
    if      (c == 't') playScaleTest();
    else if (c == '1') { trigMode = TRIG_TOUCH; Serial.println("[模式] 1 触摸触发"); }
    else if (c == '2') {
      if (!micPresent) Serial.println("[模式] 麦克风未接，切不过去");
      else { trigMode = TRIG_STRUM; Serial.println("[模式] 2 拨弦触发"); }
    }
    else if (c == '[') { if (currentFret > 0)  currentFret--; Serial.printf("[把位] %d\n", currentFret); }
    else if (c == ']') { if (currentFret < 12) currentFret++; Serial.printf("[把位] %d\n", currentFret); }
    else if (c == 'm') muteAll();
    else if (c == 's') printStatus();
    else if (c == 'b') calibrateTouch();
    else if (c == 'g') { levelDebug = !levelDebug; Serial.printf("[电平监视] %s\n", levelDebug ? "开" : "关"); }
    else if (c == 'c') recalibNoiseFloor();
    else if (c == '+') { absGate += 50; Serial.printf("[门槛] %u（更不灵敏）\n", absGate); }
    else if (c == '-') { absGate = (absGate > 60) ? absGate - 50 : 60; Serial.printf("[门槛] %u（更灵敏）\n", absGate); }
    else if (c >= '0' && c <= '9') { currentFret = c - '0'; Serial.printf("[把位] %d\n", currentFret); }
  }
}

// ===================== 主程序 =====================
void setup() {
  Serial.begin(115200);
  delay(300);

  Serial.println("\n=== 无障碍吉他 · L2（S3 + 麦克风）===");
  Serial.printf("芯片: %s  核心: %s  PSRAM: %s\n",
                ESP.getChipModel(), ESP.getCoreVersion(), psramFound() ? "有" : "无");

  BLEMidiServer.begin(DEVICE_NAME);
  Serial.printf("蓝牙已启动：%s\n", DEVICE_NAME);

  calibrateTouch();
  initMic();
  printStatus();
}

void loop() {
  handleSerial();

  // 1) 触摸（模式 2 下 = 选弦 / 释放）
  uint32_t now = millis();
  for (uint8_t i = 0; i < NUM_TOUCH; i++) {
    bool raw = readTouched(i);
    if (raw != lastRaw[i]) {
      rawChangeTs[i] = now;
      lastRaw[i]     = raw;
    } else if (now - rawChangeTs[i] > DEBOUNCE_MS && isDown[i] != raw) {
      isDown[i] = raw;
      if (raw) onPadDown(i); else onPadUp(i);
    }
  }

  // 2) 拨弦瞬态 → 触发发声
  uint16_t peak = 0;
  if (trigMode == TRIG_STRUM) {
    if (detectStrum(&peak)) onStrum(peak);
  } else if (micPresent) {
    detectStrum(&peak);        // 触摸模式下仍然跑，让噪声底保持跟随
  }
}
