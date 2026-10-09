#!/usr/bin/env python3
"""改写 vendor 镜像内指定 build.prop 的属性（容器内执行，晚于 cp -a / 注入）。

为什么需要：官方 gold 的 `/odm/etc/build.prop`（在 vendor 分区内——`/odm` 是
`/vendor/odm` 的软链）把设备声明为 **32+64 混合**：
    ro.odm.product.cpu.abilist=arm64-v8a,armeabi-v7a,armeabi
    ro.odm.product.cpu.abilist32=armeabi-v7a,armeabi
而云机是 **arm64-only** 底包（lineage-22.0 …virtio_arm64only），该声明带来两个后果，
均已实测：

  1) `on init && property:ro.product.cpu.abilist32=*` 的 **32 位 boringssl 自检**被执行
     （/system/etc/init/hw/init.boringssl.zygote64_32.rc），而 /system/bin/boringssl_self_test32
     在云机 Exec format error；该 service 带 `reboot_on_failure` → init 主动重启
     （reboot reason `boringssl-self-check-failed`）→ 起机死循环。
     实测：out/core/daemon/runtime/gold-15/3/qemu.log
       init: cannot execv('/system/bin/boringssl_self_test32'): Exec format error
       init: Service boringssl_self_test32 has 'reboot_on_failure' option and failed,
             shutting down system. → reboot: Restarting system with command ...
  2) 32 位 abilist 会让 framework 认为本机存在 zygote_secondary（云机没有）。

故把该文件的三行 abilist 声明改成纯 64 位——与 note13 同口径（见 note13
build/doc/evidence/issue-zygote-secondary-missing/solution.md）。

对外：
  TARGET / SETS                  目标文件（/vendor 内相对路径）与改写表
  write_workspace(work_dir)      落盘容器要用的脚本
  script()                       容器脚本片段（由编排层拼装）
"""

import os

from base_fs import C_DST_DIR, C_WORKDIR

# 目标文件：vendor 镜像内的 odm build.prop（init 会以 /odm/etc/build.prop 加载它）
TARGET = "odm/etc/build.prop"

# 改写表：值 = 目标值（空串表示置空）。官方三行的 64 位值本身就对，一并显式固化口径。
SETS = {
    "ro.odm.product.cpu.abilist": "arm64-v8a",
    "ro.odm.product.cpu.abilist32": "",
    "ro.odm.product.cpu.abilist64": "arm64-v8a",
}

PY_NAME = "apply_props.py"

# Remote KeyMint 起机硬依赖：guest HAL（remote_keymaster.cpp:87-91）读
# `ro.vendor.boot_security_patch` 后 std::stoi，**属性缺失即抛 std::invalid_argument
# → SIGABRT**（实测 libc++abi: stoi: no conversion）。官方 gold 的 /vendor/build.prop
# 只有 ro.vendor.build.security_patch（=2026-02-01），没有 boot 那一条。
# 处理：在 /vendor/build.prop 内，以 build.security_patch 的值为准补一条 boot
# security_patch（去掉分隔符后 stoi 得到 20260201）。
BOOT_PATCH_KEY = "ro.vendor.boot_security_patch"
PATCH_SOURCE_KEY = "ro.vendor.build.security_patch"
PATCH_PROP = "build.prop"

# —— /vendor/build.prop 的 ro.zygote 由 64_32 改纯 64（起机硬依赖）——
# 官方 gold /vendor/build.prop 第 77 行 `ro.zygote=zygote64_32`：init 据此
# `import /system/etc/init/hw/init.zygote64_32.rc` → 定义并启动 32 位
# `zygote_secondary`（/system/bin/app_process32）。本环境（Apple Silicon + HVF）
# 无法执行 32 位 ARM → `Exec format error`（zygote_secondary 退出 127），init 随即
# 把同一进程组的主 `zygote` 一起 SIGKILL 并重启（实测 123 次），`system_server`
# 从未启动 → 引导死循环。改 zygote64 后 init 只 `import init.zygote64.rc`
# （纯 64，官方 gold /system 内已有），不再定义 zygote_secondary。
# 注：ramdisk `system/etc/ramdisk/build.prop` 的同名注入无效（该文件不被 init
# 处理，实测无 ro.product.bootimage.* 日志），故改在此文件。
VENDOR_SETS = {"ro.zygote": "zygote64"}

_APPLY_PY = r'''#!/usr/bin/env python3
import os
import sys

TARGET = %(target)r
SETS = %(sets)r
ROOT = %(root)r
BOOT_PATCH_KEY = %(boot_key)r
PATCH_SOURCE_KEY = %(src_key)r
PATCH_PROP = %(patch_prop)r
VENDOR_SETS = %(vendor_sets)r

path = os.path.join(ROOT, TARGET)
if not os.path.isfile(path):
    print("[err] 目标 prop 不存在: /vendor/" + TARGET)
    sys.exit(1)

lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
seen = set()
out = []
for line in lines:
    s = line.strip()
    key = s.split("=", 1)[0] if (s and not s.startswith("#") and "=" in s) else None
    if key in SETS:
        seen.add(key)
        out.append(f"{key}={SETS[key]}")
    else:
        out.append(line)
with open(path, "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")

for k in sorted(seen):
    print(f"[ok] /vendor/{TARGET}: {k}={SETS[k]}")
missing = sorted(set(SETS) - seen)
if missing:
    print("[err] 目标文件缺以下键（改写表已过时？）: " + ", ".join(missing))
    sys.exit(1)

# 兜底：/vendor/build.prop 补 ro.vendor.boot_security_patch（Remote KeyMint stoi 硬依赖）
bpath = os.path.join(ROOT, PATCH_PROP)
try:
    blines = open(bpath, encoding="utf-8", errors="replace").read().splitlines()
except OSError as e:
    print("[err] 读不到 /vendor/" + PATCH_PROP + "：" + str(e))
    sys.exit(1)

src_val = None
for line in blines:
    s = line.strip()
    if s.startswith(PATCH_SOURCE_KEY + "="):
        src_val = s.split("=", 1)[1].strip()
if not src_val:
    print("[err] /vendor/" + PATCH_PROP + " 缺 " + PATCH_SOURCE_KEY
          + "，无法派生 boot security_patch")
    sys.exit(1)

have = any(l.strip().startswith(BOOT_PATCH_KEY + "=") for l in blines)
if have:
    print("[..] /vendor/" + PATCH_PROP + " 已含 " + BOOT_PATCH_KEY + "，跳过")
else:
    blines.append(BOOT_PATCH_KEY + "=" + src_val)
    print("[ok] /vendor/" + PATCH_PROP + ": " + BOOT_PATCH_KEY + "=" + src_val
          + "（由 " + PATCH_SOURCE_KEY + " 派生，Remote KeyMint 起机必需）")

# 一并改写 /vendor/build.prop 的 VENDOR_SETS（如 ro.zygote=zygote64_32 -> zygote64）
vseen = set()
vout = []
for line in blines:
    s = line.strip()
    k = s.split("=", 1)[0] if (s and not s.startswith("#") and "=" in s) else None
    if k in VENDOR_SETS:
        vseen.add(k)
        vout.append(k + "=" + VENDOR_SETS[k])
    else:
        vout.append(line)
blines = vout
for k in sorted(vseen):
    print("[ok] /vendor/" + PATCH_PROP + ": " + k + "=" + VENDOR_SETS[k])
vmissing = sorted(set(VENDOR_SETS) - vseen)
if vmissing:
    print("[err] /vendor/" + PATCH_PROP + " 缺以下键（改写表已过时？）: " + ", ".join(vmissing))
    sys.exit(1)

with open(bpath, "w", encoding="utf-8") as f:
    f.write("\n".join(blines) + "\n")
''' % {"target": TARGET, "sets": SETS, "root": C_DST_DIR,
       "boot_key": BOOT_PATCH_KEY, "src_key": PATCH_SOURCE_KEY,
       "patch_prop": PATCH_PROP, "vendor_sets": VENDOR_SETS}


def write_workspace(work_dir):
    """把改写脚本落盘到工作目录（由编排层挂进容器 /workdir 后执行）。"""
    os.makedirs(work_dir, exist_ok=True)
    with open(os.path.join(work_dir, PY_NAME), "w", encoding="utf-8") as f:
        f.write(_APPLY_PY)


def script():
    return f"python3 {C_WORKDIR}/{PY_NAME}\n"