cmake_minimum_required(VERSION 3.10)
project(guitar_assist C)

set(CMAKE_C_STANDARD 11)
# 注意: 必须用 gnu11 而不是 c11。
# ALSA 的 asoundlib.h 依赖 POSIX 扩展, 用 -std=c11 会关闭 _POSIX_C_SOURCE,
# 导致 "redefinition of struct timespec" 编译错误。
set(CMAKE_C_EXTENSIONS ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()
set(CMAKE_C_FLAGS_RELEASE "-O2 -ftree-vectorize")
add_compile_options(-Wall -Wextra)

include_directories(
    ${CMAKE_SOURCE_DIR}/include
    ${CMAKE_SOURCE_DIR}/src/dsp
    ${CMAKE_SOURCE_DIR}/src/audio
    ${CMAKE_SOURCE_DIR}/src/chord
    ${CMAKE_SOURCE_DIR}/src/feedback
    ${CMAKE_SOURCE_DIR}/src/ui
    ${CMAKE_SOURCE_DIR}/tools
)

# ---------- 核心算法库: PC 和板子共用, 无外部依赖 ----------
add_library(ga_core STATIC
    src/dsp/yin.c
    src/dsp/pitch_map.c
    src/chord/chord_db.c
    src/chord/chroma.c
    src/chord/chord_recog.c
)
target_link_libraries(ga_core m)

# ---------- 无障碍反馈层 ----------
# 注意: 这个库在 GA_HAVE_ALSA 下有对 libasound 的【链接依赖】,
# 必须用 target_link_libraries(PUBLIC) 传递出去, 否则任何链接它的
# 可执行文件都会报 snd_pcm_* undefined reference。
add_library(ga_feedback STATIC src/feedback/feedback.c)
target_link_libraries(ga_feedback PUBLIC ga_core m)

# ---------- 界面层(不依赖 LVGL, 自绘) ----------
# 拆成两个库:
#   ga_ui      纯绘制原语 + 字库, 无系统依赖, PC 和板子都能编
#   ga_ui_screen 界面状态机 + 页面布局(依赖 ga_core 拿和弦数据结构)
add_library(ga_ui STATIC src/ui/ui_draw.c src/ui/input.c)
add_library(ga_ui_screen STATIC src/ui/ui_screen.c src/ui/ui_app.c)
target_link_libraries(ga_ui_screen PUBLIC ga_ui ga_core)

# ---------- 屏幕驱动(仅 Linux 板端: spidev + GPIO) ----------
# 说明: st7789.c / display_drv.c 之前【没有被任何目标引用】(等价于从未编译),
#       所以那块 240x240 的屏从来没亮过。接上它们, 屏才真的能亮。
#       这两个文件依赖 linux/spi/spidev.h 与 linux/gpio.h, 只能在 Linux 上编,
#       因此单独成一个库, 不污染 ui_preview / ui_demo 这些纯 PC 工具。
add_library(ga_display STATIC
    src/ui/display_drv.c
    src/ui/st7789.c
)
target_link_libraries(ga_display PUBLIC m)

find_package(Threads REQUIRED)

# ---------- 机械机构驱动层(与档3《拨云》ESP32 硬件对接) ----------
#   ga_mech        : 帧编解码 + 角度计算 + 串口, 无 UI 依赖, PC 可编可测
#   ga_mech_bridge : 把 ga_mech 挂到 ui_app 的三个回调上(依赖 ga_ui_screen)
# 依据: 档3《拨云》的 frame_codec.py / servo_controller.py,
#       一致性由 scripts/verify_boyun_parity.py + scripts/check_c_consistency.py 保证。
add_library(ga_mech STATIC
    src/mech/frame_codec.c
    src/mech/chord_angles.c
    src/mech/mech_driver.c
)
target_link_libraries(ga_mech PUBLIC m)

add_library(ga_mech_bridge STATIC src/mech/mech_bridge.c)
target_link_libraries(ga_mech_bridge PUBLIC ga_mech ga_ui_screen)

# ---------- ALSA 采集 ----------
find_library(ASOUND_LIB asound)
if(ASOUND_LIB)
    add_library(ga_audio STATIC src/audio/alsa_capture.c)
    target_link_libraries(ga_audio PUBLIC ga_core ${ASOUND_LIB})
    # PUBLIC: 让依赖 ga_audio / ga_feedback 的目标也编译到 GA_HAVE_ALSA
    target_compile_definitions(ga_audio PUBLIC GA_HAVE_ALSA)
    target_compile_definitions(ga_feedback PUBLIC GA_HAVE_ALSA)
    # 关键: 把 libasound 的链接依赖也传给 ga_feedback 的使用者
    target_link_libraries(ga_feedback PUBLIC ${ASOUND_LIB})
    set(GA_HAVE_ALSA TRUE)
    message(STATUS "ALSA 已启用: ${ASOUND_LIB}")
else()
    set(GA_HAVE_ALSA FALSE)
    message(WARNING
        "未找到 libasound, 实时采集功能不可用。\n"
        "  PC:   sudo apt install libasound2-dev\n"
        "  交叉: 提供 sysroot 或 -DALSA_LIBRARY=... -DALSA_INCLUDE_DIR=...")
endif()

# ---------- 主程序: 交互式调音 + 和弦助手 ----------
add_executable(guitar_assist
    src/main.c
    tools/wav_io.c
)
target_link_libraries(guitar_assist ga_feedback)
if(GA_HAVE_ALSA)
    target_link_libraries(guitar_assist ga_audio)
endif()

# ---------- 板端主程序(带屏): 屏 + 按键 + 音频 + 机构, 一个进程跑起来 ----------
# 与上面的 guitar_assist 的分工:
#   guitar_assist     命令行版 —— 挂串口终端用, 不需要屏(调试/无屏环境)
#   guitar_assist_ui  带屏版   —— 接上 1.54" ST7789, 按键操作, 驱动舵机
# 音频采集单独一个线程(ga_capture_read 是阻塞的), 主线程保持帧率渲染。
add_executable(guitar_assist_ui src/ui_main.c)
target_link_libraries(guitar_assist_ui
    ga_mech_bridge      # 已含 ga_mech + ga_ui_screen(PUBLIC)
    ga_display
    ga_core
    Threads::Threads
)
if(GA_HAVE_ALSA)
    target_link_libraries(guitar_assist_ui ga_audio)
    target_compile_definitions(guitar_assist_ui PRIVATE GA_HAVE_ALSA)
endif()

# ---------- 命令行调音器(轻量, 适合快速验证) ----------
add_executable(tuner_demo
    src/audio/tuner_demo.c
    tools/wav_io.c
)
target_link_libraries(tuner_demo ga_core)
if(GA_HAVE_ALSA)
    target_link_libraries(tuner_demo ga_audio)
endif()

# ---------- 测试与工具 ----------
add_executable(test_yin    tools/test_yin.c)
target_link_libraries(test_yin ga_core)

add_executable(test_chord  tools/test_chord.c)
target_link_libraries(test_chord ga_core)

add_executable(test_chroma tools/test_chroma.c)
target_link_libraries(test_chroma ga_core)

add_executable(test_recog  tools/test_recog.c)
target_link_libraries(test_recog ga_core)

add_executable(test_feedback tools/test_feedback.c)
target_link_libraries(test_feedback ga_feedback)

add_executable(dev_scan tools/dev_scan.c)
if(GA_HAVE_ALSA)
    target_link_libraries(dev_scan ga_audio)
else()
    target_link_libraries(dev_scan ga_core)
endif()

# ---------- 界面预览(PC 端把界面存成 PPM 图片, 便于离线看样式) ----------
add_executable(ui_preview tools/ui_preview.c)
target_link_libraries(ui_preview ga_ui_screen)

# ---------- 交互链验证(模拟完整按键流程, 输出每步画面) ----------
# 链接 ga_mech_bridge: 回调不再只是打印, 而是真的走到机构驱动层(离线也构造帧)
add_executable(ui_demo tools/ui_demo.c)
target_link_libraries(ui_demo ga_mech_bridge)

# ---------- 机构驱动自检(板端第一步; 不需要屏/触摸键) ----------
add_executable(mech_selftest tools/mech_selftest.c)
target_link_libraries(mech_selftest ga_mech)

enable_testing()
add_test(NAME yin_accuracy   COMMAND test_yin)
add_test(NAME chord_db       COMMAND test_chord)
add_test(NAME chroma_extract COMMAND test_chroma)
add_test(NAME chord_recog    COMMAND test_recog)
add_test(NAME feedback_logic COMMAND test_feedback)
add_test(NAME ui_render      COMMAND ui_preview ${CMAKE_BINARY_DIR}/ui_preview_out)
add_test(NAME ui_interact    COMMAND ui_demo ${CMAKE_BINARY_DIR}/ui_demo_out)
add_test(NAME mech_driver    COMMAND mech_selftest)
# 板端主程序的参数表自检: 不需要屏/按键/串口, 只验证二进制能起来、帮助完整
add_test(NAME ui_main_help   COMMAND guitar_assist_ui --help)

message(STATUS "==== 无障碍吉他助手 配置 ====")
message(STATUS "  构建类型    : ${CMAKE_BUILD_TYPE}")
message(STATUS "  目标处理器  : ${CMAKE_SYSTEM_PROCESSOR}")
message(STATUS "  ALSA        : ${GA_HAVE_ALSA}")
message(STATUS "  板端主程序  : guitar_assist_ui (屏 + 按键 + 机构)")
message(STATUS "==============================")
