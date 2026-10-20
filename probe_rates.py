"""探测板卡 ALSA 采集硬件支持的采样率列表。
用法: python3 probe_rates.py
"""
import subprocess

# plughw:0,0 = rockchip rk809 codec 的 capture 子设备
hw = "plughw:0,0"
rates = [8000, 11025, 16000, 22050, 32000, 44100, 48000]

print("device =", hw)
print("rate    ok")
ok = []
for r in rates:
    p = subprocess.run(
        ["arecord", "-D", hw, "-f", "S16_LE", "-r", str(r), "-c", "1", "-t", "raw", "-d", "0",
         "-q", "-o", "/dev/null"],
        capture_output=True, text=True,
    )
    out = (p.stdout + p.stderr).lower()
    # arecord 能成功打开会打印 "Recording raw..." 或类似；失败会打印 audio open error / Invalid
    good = ("recording raw" in out) or (p.returncode == 0 and "audio open error" not in out)
    if good:
        ok.append(r)
    print(f"{r:<7} {'OK' if good else 'NO'}")

print("\n支持的采样率 =", ok)
