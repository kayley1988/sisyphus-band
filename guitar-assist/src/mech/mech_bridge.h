/*
 * mech_bridge.h - 把机构驱动挂到 UI 应用(ui_app)的回调上
 *
 * ui_app 刻意不直接碰硬件, 只回调:
 *     on_chord_changed(ctx, name, fingering)   // 选/换和弦
 *     on_confirm(ctx)                          // ENT 重压
 *     on_brightness(ctx, percent)              // 背光
 * 本桥接层把这三个回调接到 ga_mech_* 上。ctx 用 ga_mech_bridge_t*。
 */
#ifndef GA_MECH_BRIDGE_H
#define GA_MECH_BRIDGE_H

#include "ui_app.h"
#include "mech_driver.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    ga_mech_t *mech;
    ui_app_t  *app;
    int        last_ok;      /* 最近一次驱动是否成功(发帧成功=1) */
} ga_mech_bridge_t;

/* 初始化并挂载回调。 */
void ga_mech_bridge_init(ga_mech_bridge_t *b, ga_mech_t *mech, ui_app_t *app);

#ifdef __cplusplus
}
#endif
#endif /* GA_MECH_BRIDGE_H */
