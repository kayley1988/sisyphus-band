/*
 * 无障碍吉他 · 档3 舵机自动压弦固件
 * 硬件：ESP32-WROOM + PCA9685(16路) + TTP223触摸×13 + SG90/MG90S舵机×6
 * 模式（由 MODE2_PIN 开关选大模式）：
 *   A. 和弦模式（7 触摸区）：
 *      ① 即按即响（手停压弦，手离回位） ② 点按锁定（轻触压下保持，再触松开）
 *   B. 单音模式（6 触摸区，一弦一区）：碰哪根压哪根，多根可同时按，
 *      配合右手拨弦即可演奏单音/琶音/旋律（音高由桥板所在品位决定）
 *   ③ 串口自动演奏（保险模式，串口发 A 进入 / T 退出）：
 *      - 'D' 播放内置演示曲（Em-C-G-D 循环2遍，无需电脑持续控制）
 *      - '0'~'6' 立即压对应和弦，'r' 全部松开，'x' 停止播放
 *      - 配套 serial_player.py 可按歌曲时间轴自动发送指令
 *      借鉴 TheRiffler (MIT) 的串口控制思路，谱面解析在电脑端完成
 *
 * 烧录：Arduino IDE → 开发板选 ESP32 Dev Module；
 *       库管理器安装 "Adafruit PWM Servo Driver Library"
 *       （依赖 Adafruit BusIO，会自动带上）
 */

#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>

Adafruit_PWMServoDriver pwm = Adafruit_PWMServoDriver(0x40);

// ===================== 硬件配置 =====================
const uint8_t NUM_CHORD = 7;   // 和弦触摸区数量
const uint8_t NUM_STR   = 6;   // 舵机数量（=弦数）

// TTP223 OUT 引脚，顺序对应和弦表
// （G7 用 GPIO4，不要用 GPIO12——那是启动 strapping 引脚，被拉高会无法开机）
const uint8_t TOUCH_PIN[NUM_CHORD] = {32, 33, 25, 26, 27, 14, 4};
// 单音模式触摸区：一弦一区，顺序 {6弦E,5弦A,4弦D,3弦G,2弦B,1弦e}
const uint8_t STRING_PIN[NUM_STR] = {16, 17, 18, 19, 23, 5};
// 模式拨动开关1：接地=即按即响，悬空/接3.3V=点按锁定（仅和弦模式有效）
const uint8_t MODE_PIN = 13;
// 模式拨动开关2：接地=和弦模式，悬空/接3.3V=单音模式
const uint8_t MODE2_PIN = 15;

// PCA9685 通道 → 弦（1弦=e 最细 → 6弦=E 最粗）
// 通道0=6弦E, 1=5弦A, 2=4弦D, 3=3弦G, 4=2弦B, 5=1弦e
const uint8_t STR_SERVO_CH[NUM_STR] = {0, 1, 2, 3, 4, 5};

// ===================== 舵机标定 =====================
// 每个舵机三个值，装好后逐个标定（见指南"第6步"）：
//   PRESS_US  : 压实位脉宽（弦被压到品丝再深约0.3mm）
//   RELEASE_US: 释放位脉宽（爪尖离弦1~2mm，不碰弦）
//   ENABLE_DETACH: 到位后断电防抖（SG90抖动时开；若弦把舵机拉离位则关掉改用机械限位）
struct ServoCal {
  uint16_t press_us;
  uint16_t release_us;
  bool     detach_after;
};

ServoCal cal[NUM_STR] = {
  //  先给安全初值，装上琴后必标！
  {1700, 1300, false},  // 6弦E
  {1700, 1300, false},  // 5弦A
  {1700, 1300, false},  // 4弦D
  {1700, 1300, false},  // 3弦G
  {1700, 1300, false},  // 2弦B
  {1700, 1300, false},  // 1弦e
};

// ===================== 和弦映射表 =====================
// 行=和弦（顺序同 TOUCH_PIN），列=弦 {E,A,D,G,B,e}，1=该弦被舵机压下
// 注意：每个舵机固定压在自己安装位置的品位上，
//       弦-品冲突的解决见实施指南"关键决策"一节。
const uint8_t CHORD_TABLE[NUM_CHORD][NUM_STR] = {
  // E  A  D  G  B  e
  {  0, 1, 1, 0, 0, 0 },  // Em  (022000)
  {  0, 1, 1, 1, 1, 0 },  // Am  (x02210)
  {  0, 1, 1, 0, 1, 0 },  // C   (x32010)
  {  0, 0, 1, 1, 1, 1 },  // D   (xx0232)
  {  1, 1, 0, 0, 0, 1 },  // G   (320003)
  {  1, 1, 0, 0, 1, 1 },  // G7  (320001)
  {  0, 1, 1, 1, 0, 0 },  // E   (022100)
};
const char* CHORD_NAME[NUM_CHORD] = {"Em", "Am", "C", "D", "G", "G7", "E"};

// ===================== 行为参数 =====================
const uint16_t DEBOUNCE_MS   = 30;    // 触摸防抖
const uint16_t PRESS_GAP_MS  = 80;    // 多舵机错峰间隔（防电源跌压）
const uint16_t SETTLE_MS     = 300;   // 到位等待后再考虑 detach
bool momentaryMode = true;            // true=即按即响 false=点按锁定

// ===================== 运行状态 =====================
bool pressed[NUM_STR]  = {false};     // 舵机当前是否压下
bool debounced[NUM_CHORD] = {false};  // 防抖后的触摸稳定状态
bool lastTouch[NUM_CHORD] = {false};  // 上次触摸原始电平（防抖计时用）
uint32_t touchTs[NUM_CHORD] = {0};    // 防抖时间戳

// 单音模式状态（独立于和弦模式的防抖组）
bool strMode      = false;            // 当前是否单音模式
bool debouncedS[NUM_STR] = {false};
bool lastTouchS[NUM_STR] = {false};
uint32_t touchTsS[NUM_STR] = {0};

// ---------- 底层 ----------
void servoWrite(uint8_t s, bool press) {
  pwm.writeMicroseconds(STR_SERVO_CH[s], press ? cal[s].press_us : cal[s].release_us);
  pressed[s] = press;
  if (!press && cal[s].detach_after) {
    delay(SETTLE_MS);
    pwm.setPWM(STR_SERVO_CH[s], 0, 0);  // 断脉冲 = 舵机失电
    pressed[s] = false;
  }
}

void applyChord(int idx) {
  if (idx < 0) return;  // 无有效和弦
  // ① 先放旧：压着但新和弦不需要的，先全部回位（错峰防跌压）
  for (uint8_t s = 0; s < NUM_STR; s++)
    if (pressed[s] && !CHORD_TABLE[idx][s]) servoWrite(s, false);
  delay(PRESS_GAP_MS);
  // ② 再压新：逐个间隔 PRESS_GAP_MS
  for (uint8_t s = 0; s < NUM_STR; s++)
    if (CHORD_TABLE[idx][s] && !pressed[s]) {
      servoWrite(s, true);
      delay(PRESS_GAP_MS);
    }
  Serial.print("[和弦] "); Serial.println(CHORD_NAME[idx]);
}

void releaseAll() {
  for (uint8_t s = 0; s < NUM_STR; s++)
    if (pressed[s]) servoWrite(s, false);
}

// ---------- 触摸读取（含防抖 + 按压沿检测） ----------
// 返回：本帧"有效触摸事件"的和弦编号，无则 -1
// 即按即响：手指停着就持续返回；锁定模式：只在"稳定按下沿"（松→触）触发一次，
//          再触一次同一和弦会再次返回 → 由主循环切换为"取消锁定"
int readTouchEdge() {
  int hit = -1;
  uint32_t now = millis();
  for (uint8_t i = 0; i < NUM_CHORD; i++) {
    bool raw = digitalRead(TOUCH_PIN[i]);  // TTP223 默认高电平有效
    if (raw != lastTouch[i]) {
      touchTs[i] = now;                    // 电平变化，重记防抖时间
      lastTouch[i] = raw;
    } else if (now - touchTs[i] > DEBOUNCE_MS && debounced[i] != raw) {
      debounced[i] = raw;                  // 稳定 30ms 后状态确认翻转
      if (momentaryMode) {
        if (raw) hit = i;                  // 按住期间每帧都报（配合 anyTouchStable 用）
      } else if (raw) {
        hit = i;                           // 锁定：每个"新的按压沿"报一次（含再触取消）
      }
    }
  }
  return hit;
}

bool anyTouchStable() {
  uint32_t now = millis();
  for (uint8_t i = 0; i < NUM_CHORD; i++)
    if (digitalRead(TOUCH_PIN[i]) && now - touchTs[i] > DEBOUNCE_MS) return true;
  return false;
}

// ---------- 上电自检 ----------
void selfTest() {
  Serial.println("自检：逐舵机 压下-回位 扫一遍…");
  for (uint8_t s = 0; s < NUM_STR; s++) {
    servoWrite(s, true);  delay(400);
    servoWrite(s, false); delay(200);
  }
  Serial.println("自检完成。");
}

// ---------- 单音模式 ----------
// 每根弦一个触摸区，独立即按即响：碰着=压下，离开=回位，互不影响
// 音高由桥板所在的品位决定（配合 v3 电动换把可演奏完整旋律）
void handleStringMode() {
  static bool strPressed[NUM_STR] = {false};
  uint32_t now = millis();
  for (uint8_t s = 0; s < NUM_STR; s++) {
    bool raw = digitalRead(STRING_PIN[s]);
    if (raw != lastTouchS[s]) {
      touchTsS[s] = now;
      lastTouchS[s] = raw;
    } else if (now - touchTsS[s] > DEBOUNCE_MS && debouncedS[s] != raw) {
      debouncedS[s] = raw;
      if (raw != strPressed[s]) {
        servoWrite(s, raw);            // 碰着压、离开松
        strPressed[s] = raw;
        Serial.print("[单音] "); Serial.print(s + 1); Serial.println(raw ? "弦 压下" : "弦 松开");
      }
    }
  }
}

// ===================== 串口自动演奏（保险模式） =====================
// 现场触摸万一失灵的备份：内置演示曲脱机可播；或电脑端 serial_player.py 按时间轴发指令
bool autoMode = false;

// 内置演示曲事件表：at_ms=相对开始时刻，chord=-1 表示全部松开
// 和弦编号对应 CHORD_NAME：0=Em 1=Am 2=C 3=D 4=G 5=G7 6=E
struct SongEvent { uint16_t at_ms; int8_t chord; };
const SongEvent DEMO_SONG[] = {
  {    0,  0 },  // Em
  { 1500,  2 },  // C
  { 3000,  4 },  // G
  { 4500,  3 },  // D
  { 6000,  0 },  // Em（第二遍）
  { 7500,  2 },  // C
  { 9000,  4 },  // G
  {10500,  3 },  // D
  {12000, -1 },  // 收尾：全部松开
};
const uint8_t DEMO_LEN = sizeof(DEMO_SONG) / sizeof(DEMO_SONG[0]);
bool     playing   = false;
uint32_t playStart = 0;
uint8_t  playIdx   = 0;

void startDemo() {
  releaseAll();
  playing   = true;
  playStart = millis();
  playIdx   = 0;
  Serial.println("[自动演奏] 内置演示曲开始（Em-C-G-D ×2）");
}

void stopDemo() {
  if (!playing) return;
  playing = false;
  releaseAll();
  Serial.println("[自动演奏] 已停止");
}

// 非阻塞时间轴调度：loop() 每圈调用，到点就触发事件
void tickPlayback() {
  if (!playing) return;
  uint32_t t = millis() - playStart;
  while (playIdx < DEMO_LEN && DEMO_SONG[playIdx].at_ms <= t) {
    int8_t c = DEMO_SONG[playIdx].chord;
    if (c < 0) releaseAll();
    else       applyChord(c);
    playIdx++;
  }
  if (playIdx >= DEMO_LEN) {
    playing = false;
    Serial.println("[自动演奏] 演示曲结束");
  }
}

// 串口单字符指令（115200，指令间无需换行）
void handleSerial() {
  while (Serial.available()) {
    char cmd = Serial.read();
    switch (cmd) {
      case 'A':  // 进入自动演奏模式
        autoMode = true; playing = false; releaseAll();
        Serial.println("[模式] 串口自动演奏（D=演示曲 0~6=和弦 r=松开 x=停止 T=回触摸）");
        break;
      case 'T':  // 退回触摸模式
        autoMode = false; playing = false; releaseAll();
        Serial.println("[模式] 触摸模式");
        break;
      case 'S':  // 和弦/单音模式切换（也可用开关2）
        strMode = !strMode; playing = false; releaseAll();
        Serial.println(strMode ? "[模式] 单音（一弦一区）" : "[模式] 和弦");
        break;
      case 'D':
        if (autoMode) startDemo();
        break;
      case 'x':
        stopDemo();
        break;
      case 'r':
        if (autoMode && !playing) releaseAll();
        break;
      default:
        // '0'~'6'：立即切换到对应和弦（播放中忽略，避免打架）
        if (autoMode && !playing && cmd >= '0' && cmd <= '6')
          applyChord(cmd - '0');
        break;
    }
  }
}

// ---------- 主程序 ----------
void setup() {
  Serial.begin(115200);
  pinMode(MODE_PIN, INPUT_PULLUP);
  pinMode(MODE2_PIN, INPUT_PULLUP);
  momentaryMode = (digitalRead(MODE_PIN) == LOW);  // 开关接地=即按即响
  strMode = (digitalRead(MODE2_PIN) != LOW);       // 悬空=单音模式
  for (uint8_t i = 0; i < NUM_CHORD; i++) pinMode(TOUCH_PIN[i], INPUT);
  for (uint8_t s = 0; s < NUM_STR; s++) pinMode(STRING_PIN[s], INPUT);

  Wire.begin(21, 22);           // ESP32 默认 SDA=21 SCL=22
  pwm.begin();
  pwm.setOscillatorFrequency(27000000);  // 板载晶振，频率更准
  pwm.setPWMFreq(50);           // 舵机 50Hz

  delay(500);
  Serial.println("\n=== 无障碍吉他 档3 固件 ===");
  Serial.println(strMode ? "模式：单音（一弦一区）"
                         : (momentaryMode ? "模式：即按即响" : "模式：点按锁定"));
  Serial.println("保险模式：串口发 A 进入自动演奏（D=内置演示曲）");
  selfTest();
}

void loop() {
  static int curChord = -1;

  handleSerial();          // 串口指令随时可进（切模式/自动演奏）
  if (autoMode) {          // 自动演奏模式下不响应触摸
    tickPlayback();
    return;
  }

  if (strMode) {           // 单音模式：一弦一区，独立压放
    handleStringMode();
    return;
  }

  int hit = readTouchEdge();

  if (momentaryMode) {
    // 即按即响：有稳定触摸 → 切到该和弦；一个都没有 → 全部回位
    if (hit >= 0) {
      if (curChord != hit) { curChord = hit; applyChord(curChord); }
    } else if (curChord != -1 && !anyTouchStable()) {
      releaseAll();
      curChord = -1;
    }
  } else {
    // 点按锁定：每个"稳定按压沿"触发一次；再触同一和弦 → 取消锁定回位
    if (hit >= 0) {
      int chord = hit;
      if (curChord == chord) {  // 同一和弦再次触摸 → 切换为取消
        latched[chord] = false;
        releaseAll();
        curChord = -1;
        Serial.print("[解锁] "); Serial.println(CHORD_NAME[chord]);
        delay(400);  // 防止手还没离开就重复触发
      } else {
        curChord = chord;
        applyChord(chord);
        delay(400);  // 手离开前不响应新输入
      }
    }
  }
}
