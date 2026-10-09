#!/usr/bin/env python3
"""gold-15 vendor 重打包的编译依赖准备（deps）。

职责：为 Remote KeyMint HAL 交叉编译准备**链接期**运行库目录。这些 .so 仅作为
编译时 -L/链接输入（决定 DT_NEEDED/SONAME 与链接期符号检查），运行时仍由 gold-15
guest 自身的官方 /vendor/lib64 + /system/lib64 提供。

来源必须是**本设备官方镜像**（不是 lineage，也不是 note13 的提取件）：
  * vendor 侧 4 个：官方 vendor_a.img（EROFS）内 /lib64
    （libc++.so libbase.so libcrypto.so libkeymaster_messages.so）；
  * system 侧 2 个：官方 system_a.img（EROFS）内 /system/lib64
    （liblog.so libbinder_ndk.so）。
两者都是 EROFS，宿主无 erofs 工具，统一在 builder 容器里用 dump.erofs --cat 提取。
组装到 <out>/dist/vendor-compile-dep/，幂等：目标缺哪个取哪个，已存在即跳过。

用法：
  python3 build/vendor/build/deps.py            # 准备并打印目录
  python3 build/vendor/build/deps.py --check    # 只校验不准备
"""

import argparse
import os
import subprocess
import sys

# buildenv 位于 build/scripts；独立执行本模块时自行插入（vendor_builder 调用时已存在）
DEVICE_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(DEVICE_ROOT, "build", "scripts"))

from buildenv import DIST_DIR, ROM_ROOT_DIR  # noqa: E402

# 编译链接所需的 6 个运行库（顺序无关，仅用于缺失检查）
RUNTIME_LIBS = [
    "libc++.so", "libbase.so", "libbinder_ndk.so", "libcrypto.so",
    "libkeymaster_messages.so", "liblog.so",
]
# 各库的来源镜像与镜像内路径（官方 EROFS）
VENDOR_IMG = os.path.join(ROM_ROOT_DIR, "vendor_a.img")
SYSTEM_IMG = os.path.join(ROM_ROOT_DIR, "system_a.img")
VENDOR_LIBS = {
    "libc++.so": ("/lib64/libc++.so", VENDOR_IMG),
    "libbase.so": ("/lib64/libbase.so", VENDOR_IMG),
    "libcrypto.so": ("/lib64/libcrypto.so", VENDOR_IMG),
    "libkeymaster_messages.so": ("/lib64/libkeymaster_messages.so", VENDOR_IMG),
}
SYSTEM_LIBS = {
    "liblog.so": ("/system/lib64/liblog.so", SYSTEM_IMG),
    "libbinder_ndk.so": ("/system/lib64/libbinder_ndk.so", SYSTEM_IMG),
}
# 组装目标目录（与 pixel6/note13 的 vendor-compile-dep 同名便于对照）
COMPILE_DEP_DIR = os.path.join(DIST_DIR, "vendor-compile-dep")

# 容器：Linux root + erofs-utils（本仓库 builder 镜像已含）
CONTAINER_IMAGE = os.environ.get("CDH_VENDOR_BUILDER",
                                 "cloud-phone-hub-ubuntu-builder:latest")


def _extract(img, path_in_img, dst):
    """用容器 dump.erofs --cat 把 EROFS 内文件提取到 dst（宿主无 erofs 工具）。"""
    if not os.path.isfile(img):
        print(f"[err] 依赖研磨: 缺官方镜像：{img}", file=sys.stderr)
        sys.exit(1)
    rom_dir = os.path.dirname(img)
    name = os.path.basename(dst)
    res = subprocess.run(
        ["docker", "run", "--rm",
         "-v", f"{rom_dir}:/roms:ro",
         "-v", f"{COMPILE_DEP_DIR}:/out",
         CONTAINER_IMAGE, "bash", "-c",
         f"dump.erofs --cat --path={path_in_img} /roms/{os.path.basename(img)} "
         f"> /out/{name} && chmod 0644 /out/{name}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0 or not os.path.isfile(dst) or os.path.getsize(dst) == 0:
        print(f"[err] 依赖研磨: 提取 {path_in_img} 失败", file=sys.stderr)
        print(res.stderr.decode("utf8", "replace")[-400:], file=sys.stderr)
        sys.exit(1)
    print(f"[ok] 依赖研磨: {name} <- 官方 {os.path.basename(img)}{path_in_img}")


def prepare_runtime_libs(dry_run=False):
    """准备链接期运行库目录，返回目录路径。幂等。"""
    os.makedirs(COMPILE_DEP_DIR, exist_ok=True)
    missing = [lib for lib in RUNTIME_LIBS
               if not os.path.isfile(os.path.join(COMPILE_DEP_DIR, lib))]
    if not missing:
        return COMPILE_DEP_DIR
    if dry_run:
        print(f"[..] (dry) 将补齐运行库: {', '.join(missing)} -> {COMPILE_DEP_DIR}")
        return COMPILE_DEP_DIR
    print(f"[..] 依赖研磨: 补齐 {len(missing)} 个链接期运行库"
          f"（来源: 官方 gold images，EROFS）")
    for lib in RUNTIME_LIBS:
        dst = os.path.join(COMPILE_DEP_DIR, lib)
        if os.path.isfile(dst):
            continue
        path_in_img, img = (VENDOR_LIBS if lib in VENDOR_LIBS else SYSTEM_LIBS)[lib]
        _extract(img, path_in_img, dst)
    return COMPILE_DEP_DIR


def main():
    ap = argparse.ArgumentParser(description="gold-15 vendor 编译依赖准备")
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