/*
 * mech_driver.c - 机械机构驱动层实现
 */
#include "mech_driver.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <time.h>

#if defined(__linux__)
#include <fcntl.h>
#include <unistd.h>
#include <termios.h>
#include <sys/file.h>          /* flock */
#endif

/* ── 串口波特率映射 ── */
#if defined(__linux__)
static int baud_to_speed(int baud)
{
    switch (baud) {
#ifdef B921600
        case 921600: return B921600;
#endif
#ifdef B460800
        case 460800: return B460800;
#endif
#ifdef B230400
        case 230400: return B230400;
#endif
        case 115200: return B115200;
        case 57600:  return B57600;
        default:     return B115200;
    }
}

static int serial_open(const char *dev, int baud)
{
    int fd = open(dev, O_RDWR | O_NOCTTY | O_NONBLOCK);
    if (fd < 0) {
        fprintf(stderr, "[mech] 打开 %s 失败: %s\n", dev, strerror(errno));
        return -1;
    }

    struct termios tio;
    if (tcgetattr(fd, &tio) != 0) {
        fprintf(stderr, "[mech] tcgetattr 失败: %s\n", strerror(errno));
        close(fd);
        return -1;
    }
    cfmakeraw(&tio);                       /* 8N1 原始模式, 不做任何字符处理 */
    cfsetispeed(&tio, baud_to_speed(baud));
    cfsetospeed(&tio, baud_to_speed(baud));
    tio.c_cflag |= (CLOCAL | CREAD);
    tio.c_cflag &= (unsigned)~CRTSCTS;     /* 关硬件流控 */
    tio.c_cc[VMIN]  = 0;
    tio.c_cc[VTIME] = 0;
    tcflush(fd, TCIOFLUSH);
    if (tcsetattr(fd, TCSANOW, &tio) != 0) {
        fprintf(stderr, "[mech] tcsetattr 失败: %s\n", strerror(errno));
        close(fd);
        return -1;
    }
    return fd;
}
#endif /* __linux__ */

/* ════════════════════════════════════════════════ */

int ga_mech_open(ga_mech_t *m, const char *dev, int baud)
{
    if (!m) return -1;
    memset(m, 0, sizeof(*m));
    m->fd = -1;
    ga_mech_cfg_default(&m->cfg);

    if (!dev)  dev  = GA_MECH_DEFAULT_DEV;
    if (baud <= 0) baud = GA_MECH_DEFAULT_BAUD;

    /* 和弦通道初值 = 90(中位), 拨弦通道初值 = 各弦 point_a */
    for (int i = 0; i < GA_MECH_CHORD_CH; i++) m->chord12[i] = 90;
    for (int i = 0; i < GA_MECH_STRUM_CH; i++) {
        int v = (int)m->cfg.strum_angles[i][0];
        if (v < (int)GA_STRUM_ANGLE_MIN) v = (int)GA_STRUM_ANGLE_MIN;
        if (v > (int)GA_STRUM_ANGLE_MAX) v = (int)GA_STRUM_ANGLE_MAX;
        m->strum6[i] = v;
    }

#if defined(__linux__)
    int fd = serial_open(dev, baud);
    if (fd < 0) {
        fprintf(stderr, "[mech] 离线模式(fd=-1): 仍可构造帧, 但不会驱动机构\n");
        return 0;                          /* 离线可用, 不致命 */
    }

    /* 串口排他: 与档3主程序 / servo_panel.py 用同一把文件锁协调 */
    if (flock(fd, LOCK_EX | LOCK_NB) == 0) {
        m->lock_held = 1;
    } else {
        fprintf(stderr, "[mech] 警告: %s 已被其他进程锁定(档3主程序在跑?)。"
                        "为避免把帧写花, 已放弃驱动。\n", dev);
        close(fd);
        return 0;                          /* 离线模式, 不乱写 */
    }

    m->fd = fd;
    printf("[mech] 已打开 %s @ %d (已加排他锁)\n", dev, baud);
#else
    (void)dev; (void)baud;
    printf("[mech] 非 Linux 平台: 离线模式, 只构造帧不发送\n");
#endif
    return 0;
}

void ga_mech_close(ga_mech_t *m)
{
    if (!m) return;
#if defined(__linux__)
    if (m->fd >= 0) {
        if (m->lock_held) (void)flock(m->fd, LOCK_UN);
        close(m->fd);
    }
#endif
    m->fd = -1;
    m->lock_held = 0;
}

void ga_mech_set_cfg(ga_mech_t *m, const ga_mech_cfg_t *cfg)
{
    if (m && cfg) m->cfg = *cfg;
}

/* ════════════════════════════════════════════════
 * CSV 载入: 兼容《拨云》servo_config.csv
 *   null_offset, v0..v11
 *   press_offset_<弦1-6>_<品1-4>, v
 *   strum_angles_<1-6>, a, b
 *   home_chord_angle, v
 * 只认这几类键, 其余忽略(注释行/未知键不影响)。
 * ════════════════════════════════════════════════ */
static void trim_eol(char *s)
{
    size_t n = strlen(s);
    while (n > 0 && (s[n-1] == '\n' || s[n-1] == '\r' || s[n-1] == ' ')) s[--n] = '\0';
}

int ga_mech_load_csv(ga_mech_t *m, const char *csv_path)
{
    if (!m || !csv_path) return -1;
    FILE *fp = fopen(csv_path, "r");
    if (!fp) return -1;

    char line[1024];
    while (fgets(line, sizeof(line), fp)) {
        trim_eol(line);
        if (line[0] == '\0' || line[0] == '#') continue;

        char *save = NULL;
        char *key = strtok_r(line, ",", &save);
        if (!key) continue;

        if (strcmp(key, "null_offset") == 0) {
            for (int i = 0; i < GA_MECH_CHORD_CH; i++) {
                char *v = strtok_r(NULL, ",", &save);
                if (!v) break;
                m->cfg.null_offset[i] = (float)atof(v);
            }
        } else if (strncmp(key, "press_offset_", 13) == 0) {
            /* press_offset_<s>_<f> */
            int s = 0, f = 0;
            if (sscanf(key + 13, "%d_%d", &s, &f) == 2 && s >= 1 && s <= 6 && f >= 1 && f <= 4) {
                char *v = strtok_r(NULL, ",", &save);
                if (v) m->cfg.press_offsets[s-1][f-1] = (float)atof(v);
            }
        } else if (strncmp(key, "strum_angles_", 13) == 0) {
            int i = atoi(key + 13);
            if (i >= 1 && i <= 6) {
                char *a = strtok_r(NULL, ",", &save);
                char *b = strtok_r(NULL, ",", &save);
                if (a) m->cfg.strum_angles[i-1][0] = (float)atof(a);
                if (b) m->cfg.strum_angles[i-1][1] = (float)atof(b);
            }
        } else if (strcmp(key, "home_chord_angle") == 0) {
            char *v = strtok_r(NULL, ",", &save);
            if (v) m->cfg.home_chord_angle = (float)atof(v);
        } else if (strcmp(key, "home_strum_offset") == 0) {
            char *v = strtok_r(NULL, ",", &save);
            if (v) m->cfg.home_strum_offset = (float)atof(v);
        }
    }
    fclose(fp);

    /* 载入后, 同步一次拨弦通道初值 */
    for (int i = 0; i < GA_MECH_STRUM_CH; i++) {
        int v = (int)m->cfg.strum_angles[i][0];
        if (v < (int)GA_STRUM_ANGLE_MIN) v = (int)GA_STRUM_ANGLE_MIN;
        if (v > (int)GA_STRUM_ANGLE_MAX) v = (int)GA_STRUM_ANGLE_MAX;
        m->strum6[i] = v;
    }
    return 0;
}

/* ════════════════════════════════════════════════ */

int ga_mech_send(ga_mech_t *m, const uint8_t *frame, size_t len)
{
    if (!m || !frame || len == 0) return -1;

    memcpy(m->last_frame, frame, len);
    m->last_frame_len = len;
    m->tx_frames++;

#if defined(__linux__)
    if (m->fd >= 0) {
        size_t off = 0;
        struct timespec ts0, ts;
        clock_gettime(CLOCK_MONOTONIC, &ts0);
        while (off < len) {
            ssize_t w = write(m->fd, frame + off, len - off);
            if (w > 0) {
                off += (size_t)w;
                continue;
            }
            if (w < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                struct timespec sl = { 0, 1000000 };   /* 1ms */
                nanosleep(&sl, NULL);
                clock_gettime(CLOCK_MONOTONIC, &ts);
                double el = (ts.tv_sec - ts0.tv_sec) + (ts.tv_nsec - ts0.tv_nsec) / 1e9;
                if (el > 0.5) break;                   /* 超时保护, 不阻塞主循环 */
                continue;
            }
            break;
        }
        return (int)off;
    }
#endif
    return (int)len;    /* 离线: 视为"已发", 便于测试断言 */
}

int ga_mech_set_chord(ga_mech_t *m, const int frets[GA_MECH_STRINGS])
{
    if (!m) return -1;

    int batch18[GA_MECH_TOTAL_CH];
    ga_mech_batch18(&m->cfg, frets, m->strum6, batch18);

    /* 同步和弦通道追踪状态(拨弦保持不变) */
    for (int i = 0; i < GA_MECH_CHORD_CH; i++) {
        m->chord12[i] = batch18[i];
    }

    uint8_t frame[GA_FRAME_MAX_TOTAL];
    size_t n = ga_make_servo_batch(batch18, frame, sizeof(frame));
    if (n == 0) return -1;
    return ga_mech_send(m, frame, n);
}

int ga_mech_strum(ga_mech_t *m, const int mask[GA_MECH_STRUM_CH])
{
    if (!m) return -1;

    int s6[GA_MECH_STRUM_CH];
    ga_strum_angles(&m->cfg, mask, m->flip, s6);

    /* 拨了的弦 -> 更新追踪; 其余保持 */
    int batch18[GA_MECH_TOTAL_CH];
    for (int i = 0; i < GA_MECH_CHORD_CH; i++) batch18[i] = m->chord12[i];
    for (int i = 0; i < GA_MECH_STRUM_CH; i++) {
        if (s6[i] > 0) m->strum6[i] = s6[i];
        batch18[GA_MECH_CHORD_CH + i] = m->strum6[i];
    }

    uint8_t frame[GA_FRAME_MAX_TOTAL];
    size_t n = ga_make_servo_batch(batch18, frame, sizeof(frame));
    if (n == 0) return -1;
    return ga_mech_send(m, frame, n);
}

int ga_mech_enable(ga_mech_t *m)
{
    if (!m) return -1;
    uint8_t frame[GA_FRAME_MAX_TOTAL];
    size_t n = ga_make_servo_ctrl(GA_SERVO_CTRL_ENABLE, frame, sizeof(frame));
    if (n == 0) return -1;
    int r = ga_mech_send(m, frame, n);
    m->enabled = 1;
    return r;
}

int ga_mech_disable(ga_mech_t *m)
{
    if (!m) return -1;
    uint8_t frame[GA_FRAME_MAX_TOTAL];
    size_t n = ga_make_servo_ctrl(GA_SERVO_CTRL_DISABLE, frame, sizeof(frame));
    if (n == 0) return -1;
    int r = ga_mech_send(m, frame, n);
    m->enabled = 0;
    return r;
}

int ga_mech_reset_idle(ga_mech_t *m)
{
    if (!m) return -1;
    uint8_t frame[GA_FRAME_MAX_TOTAL];
    size_t n = ga_make_servo_ctrl(GA_SERVO_CTRL_RESET_AND_IDLE, frame, sizeof(frame));
    if (n == 0) return -1;
    m->enabled = 0;
    return ga_mech_send(m, frame, n);
}

int ga_mech_home(ga_mech_t *m)
{
    if (!m) return -1;

    /* 和弦 12 路 -> home_chord_angle 平角(与档3 esp32_home_servos 同语义:
     * 12 路统一一个角度, 不加 null_offset); 拨弦 -> 各弦 point_a。 */
    int home = (int)(m->cfg.home_chord_angle + 0.5f);
    if (home < 0) home = 0;
    if (home > 180) home = 180;

    int batch18[GA_MECH_TOTAL_CH];
    for (int i = 0; i < GA_MECH_CHORD_CH; i++) batch18[i] = home;
    for (int i = 0; i < GA_MECH_STRUM_CH; i++) {
        int v = (int)m->cfg.strum_angles[i][0];
        if (v < (int)GA_STRUM_ANGLE_MIN) v = (int)GA_STRUM_ANGLE_MIN;
        if (v > (int)GA_STRUM_ANGLE_MAX) v = (int)GA_STRUM_ANGLE_MAX;
        batch18[GA_MECH_CHORD_CH + i] = v;
        m->strum6[i] = v;
    }
    for (int i = 0; i < GA_MECH_CHORD_CH; i++) m->chord12[i] = home;

    uint8_t frame[GA_FRAME_MAX_TOTAL];
    size_t n = ga_make_servo_batch(batch18, frame, sizeof(frame));
    if (n == 0) return -1;
    return ga_mech_send(m, frame, n);
}

int ga_mech_set_mode(ga_mech_t *m, ga_esp_mode_t mode)
{
    if (!m) return -1;
    uint8_t frame[GA_FRAME_MAX_TOTAL];
    size_t n = ga_make_mode_switch(mode, frame, sizeof(frame));
    if (n == 0) return -1;
    return ga_mech_send(m, frame, n);
}

int ga_mech_save_cfg(ga_mech_t *m)
{
    if (!m) return -1;
    uint8_t frame[GA_FRAME_MAX_TOTAL];
    size_t n = ga_make_config_save(frame, sizeof(frame));
    if (n == 0) return -1;
    return ga_mech_send(m, frame, n);
}
