#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
菜单系统模块 — 四级菜单树定义与歌曲发现工具

MENU_ITEMS 定义了完整的菜单层级结构:
  root → wifi_menu / music_menu / ai_settings / servo_calibration / ...
  music_menu → 动态歌曲列表 → 动态图片源列表 → 演奏函数

工具函数:
  get_music_folders()  — 扫描 歌曲列表/ 目录下的所有歌曲文件夹
  get_song_sources()   — 获取指定歌曲下的所有图片源文件夹
"""

import os
from typing import Dict, List


def get_music_folders() -> List[str]:
    """获取 歌曲列表 目录下所有歌曲文件夹名"""
    try:
        # 基于本文件位置计算项目根目录，消除 CWD 依赖
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(project_root, "歌曲列表")
        if not os.path.exists(path):
            # 回退：尝试 data/songs 目录
            path = os.path.join(project_root, "data", "songs")
        if not os.path.exists(path):
            return []
        items = [d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))]
        items.sort()
        return items
    except Exception:
        return []


def get_song_sources(song_name: str) -> List[str]:
    """获取歌曲目录下所有图片源文件夹名"""
    try:
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        song_dir = os.path.join(project_root, "歌曲列表", song_name)
        if not os.path.isdir(song_dir):
            song_dir = os.path.join(project_root, "data", "songs", song_name)
        if not os.path.isdir(song_dir):
            return []
        sources = sorted([
            d for d in os.listdir(song_dir)
            if os.path.isdir(os.path.join(song_dir, d)) and d.startswith('图片源')
        ])
        return sources
    except Exception:
        return []


# ──────────── 菜单定义 ────────────
MENU_ITEMS: Dict[str, dict] = {
    "root": {
        "title": "设置界面",
        "items": [
            # ★ 无障碍改造核心：她的演奏（触摸条失效时的手动兜底触发入口）
            {"name": "她的演奏", "type": "function", "func": "her_performance_ui"},
            # ★ 两种演奏模式运行时切换（全盘 / 人机配合），不重启程序
            {"name": "演奏模式", "type": "function", "func": "performance_mode_ui"},
            # ★ 弹完当场看反馈：这一场打了什么 / 哪里卡壳 / 怎么办（板子本地算）
            {"name": "演奏小结", "type": "function", "func": "session_summary_ui"},
            {"name": "WiFi连接", "type": "menu", "target": "wifi_menu"},
            {"name": "离线歌曲列表", "type": "menu", "target": "music_menu"},
            {"name": "AI语音设置", "type": "menu", "target": "ai_settings"},
            # ★ 直达入口：装机时最常用，不必再进「舵机校准设置」里翻（用 UP/SET/DOWN 操作）
            {"name": "舵机调整", "type": "function", "func": "servo_home_adjust_ui"},
            {"name": "舵机校准设置", "type": "menu", "target": "servo_calibration"},
            {"name": "电源管理与监控", "type": "function", "func": "power_monitor_ui"},
            {"name": "设备信息", "type": "function", "func": "device_info"},
        ],
    },
    "wifi_menu": {
        "title": "WiFi连接",
        "items": [
            {"name": "已连接WIFI", "type": "function", "func": "show_connected_wifi"},
            {"name": "扫描附近WiFi", "type": "function", "func": "scan_wifi"},
        ],
    },
    "music_menu": {"title": "离线歌曲列表", "items": []},
    "ai_settings": {
        "title": "AI语音设置",
        "items": [
            {"name": "唤醒词设置", "type": "function", "func": "set_wake_word"},
            {"name": "唤醒方式设置", "type": "function", "func": "set_wake_method"},
            {"name": "ASR引擎选择", "type": "function", "func": "set_asr_engine"},
            {"name": "TTS引擎选择", "type": "function", "func": "set_tts_engine"},
            {"name": "麦克风增益", "type": "function", "func": "set_mic_volume"},
            {"name": "扬声器音量", "type": "function", "func": "set_speaker_volume"},
        ],
    },
    "servo_calibration": {
        "title": "舵机校准设置",
        "items": [
            # ★ 装机用：一键把全部舵机收回收缩位（和弦角度可调 / 拨弦 point_a）
            {"name": "一键归位(装琴)", "type": "function", "func": "servo_home_ui"},
            # ★ 装机用：边调边动，把和弦收缩角调到齿条最贴合，SET 存下来长期生效
            {"name": "装机角度微调", "type": "function", "func": "servo_home_adjust_ui"},
            # ★ 装机用：松开保持力矩，手能推齿轮微调
            {"name": "释放力矩(可手推)", "type": "function", "func": "servo_release_ui"},
            {"name": "拨弦舵机自动校准", "type": "function", "func": "strum_servo_calibration"},
            {"name": "和弦舵机自动校准", "type": "function", "func": "chord_servo_calibration"},
            {"name": "舵机测试程序", "type": "function", "func": "servo_test_program"},
        ],
    },
}