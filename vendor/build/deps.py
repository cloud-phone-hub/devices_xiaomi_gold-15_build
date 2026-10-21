#!/usr/bin/env python3
"""note13-5G-15 vendor 重打包的编译依赖准备（deps）。

当前职责：为 Remote KeyMint HAL 交叉编译准备**链接期**运行库目录。这些 .so 仅作为
编译时 -L/链接输入（决定 DT_NEEDED/SONAME 与链接期符号检查），运行时仍由 note13
guest 自身的 /vendor/lib64（lineage virtio 底座）+ /system/lib64 提供。

来源必须是 note13 自己的 lineage-22.0 super（而不是借用 pixel6 镜像提取件）：
  * vendor 侧 4 个：lineage super 的 vendor_a.img 解包产物
    <lineage>/unpacked/vendor/lib64
    （libc++.so libbase.so libcrypto.so libkeymaster_messages.so）；
  * system 侧 2 个：lineage super 的 system_a.img（ext4）内 /system/lib64
    （liblog.so libbinder_ndk.so）。
组装到 <out>/dist/vendor-compile-dep/，幂等：目标缺哪个抄哪个，已存在即跳过。

用法：
  python3 build/vendor/build/deps.py            # 准备并打印目录
  python3 build/vendor/build/deps.py --check    # 只校验不准备
"""

import argparse
import os
import shutil
import subprocess
import sys

# buildenv 位于 build/scripts；独立执行本模块时自行插入（vendor_builder 调用时已存在）
DEVICE_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(DEVICE_ROOT, "build", "scripts"))

from buildenv import (  # noqa: E402
    DIST_DIR, LINEAGE_OUT_DIR, LINEAGE_SUPER_DIR,
)
from debugfs import DEBUGFS, path_exists  # noqa: E402

# 编译链接所需的运行库（顺序无关，仅用于缺失检查）
# libnativewindow.so：media（codec2）引擎链接 Buffer parcelable 的 native_handle 符号需要
RUNTIME_LIBS = [
    "libc++.so", "libbase.so", "libbinder_ndk.so", "libcrypto.so",
    "libkeymaster_messages.so", "liblog.so", "libnativewindow.so",
]
# vendor 侧库挂载路径（lineage unpacked /vendor/lib64）
VENDOR_LIB64 = os.path.join(LINEAGE_OUT_DIR, "unpacked", "vendor", "lib64")
# system 侧库源镜像：lineage super 的 system_a.img（ext4），内为 /system/lib64
SYSTEM_A_IMG = os.path.join(LINEAGE_SUPER_DIR, "system_a.img")
SYSTEM_LIBS = ("liblog.so", "libbinder_ndk.so", "libnativewindow.so")
# 组装目标目录（与 pixel6 的 vendor-compile-dep 同名便于对照）
COMPILE_DEP_DIR = os.path.join(DIST_DIR, "vendor-compile-dep")


def _dump_system_lib(name):
    """system_a.img 内 /system/lib64/<name> -> 目标目录（debugfs dump）。"""
    dst = os.path.join(COMPILE_DEP_DIR, name)
    if os.path.isfile(dst):
        return
    if not os.path.isfile(SYSTEM_A_IMG):
        print(f"[err] 依赖研磨: 缺 lineage system_a.img：{SYSTEM_A_IMG}", file=sys.stderr)
        sys.exit(1)
    if not path_exists(SYSTEM_A_IMG, f"/system/lib64/{name}"):
        print(f"[err] 依赖研磨: system_a.img 内找不到 /system/lib64/{name}",
              file=sys.stderr)
        sys.exit(1)
    res = subprocess.run([DEBUGFS, "-R", f"dump -p /system/lib64/{name} {dst}",
                          SYSTEM_A_IMG],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # debugfs 改 dump 产物属主失败是环境噪音（非致命）
    if res.returncode != 0 or not os.path.isfile(dst) or os.path.getsize(dst) == 0:
        print(f"[err] 依赖研磨: dump /system/lib64/{name} 失败", file=sys.stderr)
        sys.exit(1)
    print(f"[ok] 依赖研磨: {name} <- lineage super system_a.img /system/lib64")


def _copy_vendor_lib(name):
    """unpacked /vendor/lib64/<name> -> 目标目录。"""
    dst = os.path.join(COMPILE_DEP_DIR, name)
    if os.path.isfile(dst):
        return
    src = os.path.join(VENDOR_LIB64, name)
    if not os.path.isfile(src):
        print(f"[err] 依赖研磨: note13 lineage vendor 缺 {name}：{src}", file=sys.stderr)
        sys.exit(1)
    shutil.copy2(src, dst)
    print(f"[ok] 依赖研磨: {name} <- lineage super unpacked/vendor/lib64")


def prepare_runtime_libs(dry_run=False):
    """准备链路编译所需运行库目录，返回目录路径。

    幂等：目标目录已含全部 6 个库则直接返回；否则按来源补齐。
    由 vendor_builder 在 source 子构建前调用，也可由 build.py 独立兜底。
    """
    os.makedirs(COMPILE_DEP_DIR, exist_ok=True)
    missing = [lib for lib in RUNTIME_LIBS
               if not os.path.isfile(os.path.join(COMPILE_DEP_DIR, lib))]
    if not missing:
        return COMPILE_DEP_DIR
    if dry_run:
        print(f"[..] (dry) 将补齐运行库: {', '.join(missing)} -> {COMPILE_DEP_DIR}")
        return COMPILE_DEP_DIR
    print(f"[..] 依赖研磨: 补齐 {len(missing)} 个链接期运行库 "
          f"（来源: lineage-22.0 super）")
    for lib in RUNTIME_LIBS:
        if lib in SYSTEM_LIBS:
            _dump_system_lib(lib)
        else:
            _copy_vendor_lib(lib)
    return COMPILE_DEP_DIR


def main():
    ap = argparse.ArgumentParser(description="note13 vendor 编译依赖准备")
    ap.add_argument("--check", action="store_true",
                    help="只校验不准备（缺即报错退出）")
    args = ap.parse_args()

    if args.check:
        missing = [lib for lib in RUNTIME_LIBS
                   if not os.path.isfile(os.path.join(COMPILE_DEP_DIR, lib))]
        if missing:
            print(f"[err] 运行库缺失: {', '.join(missing)}（先跑 deps.py）",
                  file=sys.stderr)
            sys.exit(1)
        print(f"[ok] 运行库齐全: {COMPILE_DEP_DIR}")
        return 0

    prepare_runtime_libs(dry_run=False)
    print(f"[ok] 运行库目录: {COMPILE_DEP_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())