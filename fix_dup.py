# 清理补丁留下的重复注释行
p = "/root/guitarist_app/src/guitarist/oled_renderer.py"
s = open(p, encoding="utf-8").read()
dup = (
    "        # ── 状态：AI 不可用（软件/硬件条件缺失） ──\n"
    "        # ── 状态：AI 不可用（软件/硬件条件缺失） ──\n"
)
one = "        # ── 状态：AI 不可用（软件/硬件条件缺失） ──\n"
if dup in s:
    s = s.replace(dup, one, 1)
    open(p, "w", encoding="utf-8").write(s)
    print("dup comment removed")
else:
    print("no dup found")
