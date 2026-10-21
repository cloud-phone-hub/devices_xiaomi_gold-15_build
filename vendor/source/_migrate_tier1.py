#!/usr/bin/env python3
"""一次性迁移脚本：把 note13-5G-15/build/vendor/source 的 Tier1 HAL 模块改造为
参考 gold-15 的薄集成层（委托 hal/android15 引擎构建 + 落外置 cfg + 对齐 gold 命名）。

对每个模块：整目录用 gold 版覆盖（含 vendor 资产：rc/vintf/perm/cfg/policy，
名称已对齐、设备值与本机制品一致），再改写 build.py：
  1. prepare_abi：去掉 gold 专有 device_deps.VENDOR_COMPILE_DEP_DIR dry 占位分支，
     改为把 note13 设备 deps 研磨出的**单目录**（out/dist/vendor-compile-dep）归一为列表；
  2. 注释/消息里的设备标识 gold→note13（纯 cosmetic，代码路径不含 gold 字面量）；
  3. 装机面 VINTF/PERM 列表按 note13（lineage 基座缺 manifest，需自行注入）回填。
keymint / gpu / daemon / media / media.v412 不在本脚本处理范围。
"""
import os
import re
import shutil

REPO = "/Volumes/work/cloud-phone-hub"
GOLD = os.path.join(REPO, "devices/xiaomi/gold-15/build/vendor/source")
NOTE = os.path.join(REPO, "devices/xiaomi/note13-5G-15/build/vendor/source")

# 模块 -> 需要回填的装机列表（note13 lineage 基座缺这些 manifest/perm，须自行注入）
MODULES = {
    "android.hardware.audio":   {},
    "android.hardware.board":   {},
    "android.hardware.camera":  {"VINTF_NAMES": ["android.hardware.camera-service.xml"]},
    "android.hardware.gnss":    {"VINTF_NAMES": ["android.hardware.gnss-service.xml"]},
    "android.hardware.health":  {"VINTF_NAMES": ["android.hardware.health-service.xml"]},
    "android.hardware.light":   {"VINTF_NAMES": ["android.hardware.lights-service.xml"]},
    "android.hardware.radio":   {"VINTF_NAMES": ["android.hardware.radio-service.xml"]},
    "android.hardware.sensors": {"VINTF_NAMES": ["android.hardware.sensors-service.xml"]},
    "android.hardware.thermal": {"VINTF_NAMES": ["android.hardware.thermal-service.xml"]},
    "android.hardware.vibrator":{"VINTF_NAMES": ["android.hardware.vibrator-service.xml"]},
    "android.hardware.wifi":    {"VINTF_NAMES": ["android.hardware.wifi-service.xml"]},
}

PLACEHOLDER_RE = re.compile(
    r"    if dry_run and not os\.path\.isdir\(device_deps\.VENDOR_COMPILE_DEP_DIR\):\n"
    r"(?:.*\n)*?"
    r"    return device_deps\.prepare_runtime_libs\(dry_run\)\n"
)
PREPARE_NEW = (
    "    d = device_deps.prepare_runtime_libs(dry_run)\n"
    "    return [d] if isinstance(d, str) else d\n"
)


def transform(txt, lists):
    # 1) prepare_abi：note13 单目录归一为列表（去除 gold 专有占位分支）
    txt2, n = PLACEHOLDER_RE.subn(PREPARE_NEW, txt)
    if n == 0 and "VENDOR_COMPILE_DEP_DIR" in txt:
        raise SystemExit("prepare_abi 占位分支未匹配，需人工核对")
    txt = txt2
    # 2) 设备值来源措辞（cosmetic）
    txt = txt.replace("（vendor-/system-compile-deps 两个 lib64）", "（单目录 vendor-compile-dep）")
    txt = txt.replace("（vendor-/system-compile-dep 两个 lib64）", "（单目录 vendor-compile-dep）")
    txt = txt.replace("研磨 gold 超级镜像", "研磨 note13 lineage super")
    txt = txt.replace("研磨 gold 官方镜像", "研磨 note13 lineage super")
    # 3) 回填装机列表（仅当 gold 里是空列表时）
    for key, val in lists.items():
        empty = f"{key} = []"
        if empty in txt:
            txt = txt.replace(empty, f"{key} = {val!r}")
    # 4) 设备标识 gold→note13（代码路径无 gold 字面量，安全）
    txt = txt.replace("gold-15", "note13-5G-15").replace("gold", "note13")
    return txt


def main():
    for mod, lists in MODULES.items():
        gsrc = os.path.join(GOLD, mod)
        nsrc = os.path.join(NOTE, mod)
        assert os.path.isdir(gsrc), gsrc
        # 整目录覆盖
        if os.path.isdir(nsrc):
            shutil.rmtree(nsrc)
        shutil.copytree(gsrc, nsrc)
        # 清掉复制来的 __pycache__
        for root, dirs, _files in os.walk(nsrc):
            for d in list(dirs):
                if d == "__pycache__":
                    shutil.rmtree(os.path.join(root, d))
                    dirs.remove(d)
        # 改写 build.py
        bp = os.path.join(nsrc, "build.py")
        with open(bp, encoding="utf-8") as f:
            txt = f.read()
        txt = transform(txt, lists)
        with open(bp, "w", encoding="utf-8") as f:
            f.write(txt)
        print(f"[ok] {mod} 已改造（装机列表回填：{lists or '无'}）")


if __name__ == "__main__":
    main()
