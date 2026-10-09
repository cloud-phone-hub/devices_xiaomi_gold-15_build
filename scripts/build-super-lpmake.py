#!/usr/bin/env python3
"""gold-15 super.img lpmake 全量重建（官方分区 + 自制 vendor/vendor_dlkm）。

为什么要重建：vendor 基座改用**官方（厂商）vendor**后，vendor.img 是 ext4 的
~2.36 GiB（官方 vendor_a 分配仅 1.28 GiB，余量 0），「原位替换」装不下（见
build-super.py 的拒绝逻辑），故必须重建 liblp 元数据、把 vendor_a 的 extent 放大。

分区来源表：
  system / system_ext / product / system_dlkm / odm_dlkm / mi_ext  <- 官方 super 解包
  vendor / vendor_dlkm                                            <- 自制产物
（不设 odm 逻辑分区：官方 vendor 内自带 odm/ 目录，system 的 /odm 软链指向
 /vendor/odm，天然成立——note13 需要 odm_a 是因为它的 vendor 来自 lineage。）

super 总大小保持 = 官方 super.img 大小（9.13 GiB）：各 extent 之和 + 256 MiB 余量
实测仍在官方大小内，故不放大；pack-roms 再把 super 就地写进底包 GPT 的 super 分区。

工具：宿主无 lpmake/lpdump，走 docker 镜像 room-service-ubuntu-builder:latest（已含）。
产物 = out/dist/rom/super.img，并做回读自检。

用法：
  python3 build/scripts/build-super-lpmake.py
  python3 build/scripts/build-super-lpmake.py --dry-run
"""

import argparse
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from buildenv import (  # noqa: E402
    PROJECT_ROOT, ROM_DIST, ROM_ROOT_DIR, SUPER_DIST, SUPER_IMG, TMP_DIR,
    VENDOR_DIST, VENDOR_DLKM_DIST,
)
import lpmemeta  # noqa: E402

METADATA_SIZE = 65536
METADATA_SLOTS = 2
SECTOR = 512
DOCKER_IMAGE = "room-service-ubuntu-builder:latest"
COPY_CHUNK = 4 * 1024 * 1024
WORK_DIR = os.path.join(TMP_DIR, "super")

# 分区（super 内名，无槽位后缀，可带槽位后缀） -> 数据来源镜像
PART_SOURCES = {
    "system": os.path.join(ROM_ROOT_DIR, "system_a.img"),
    "system_ext": os.path.join(ROM_ROOT_DIR, "system_ext_a.img"),
    "product": os.path.join(ROM_ROOT_DIR, "product_a.img"),
    "vendor": VENDOR_DIST,
    "vendor_dlkm": VENDOR_DLKM_DIST,
    "odm_dlkm": os.path.join(ROM_ROOT_DIR, "odm_dlkm_a.img"),
    "system_dlkm": os.path.join(ROM_ROOT_DIR, "system_dlkm_a.img"),
    "mi_ext": os.path.join(ROM_ROOT_DIR, "mi_ext_a.img"),
}
# 自制产物必须存在；官方件缺失仅警告跳过
REQUIRED = {"vendor"}

# 逻辑分区预留容量下限（字节）：lpmake 的 extent 取 max(镜像, 预留)，供后续
# `pack-roms.py --patch` 增量覆盖；当前 vendor 镜像已远大于该下限，等于不生效。
SUPER_RESERVE = {
    "vendor": 512 * 1024 * 1024,
}


def _run_tool(name, args, capture=False):
    """执行 lpmake/lpdump（宿主无工具则走 docker 镜像）。"""
    if shutil.which(name):
        return subprocess.run([name] + args, check=True,
                              capture_output=capture, text=capture)
    return subprocess.run(
        ["docker", "run", "--rm", "-v", f"{PROJECT_ROOT}:{PROJECT_ROOT}",
         "-w", PROJECT_ROOT, "--user", f"{os.getuid()}:{os.getgid()}",
         DOCKER_IMAGE, name] + args,
        check=True, capture_output=capture, text=capture)


def build_super_lpmake(dry_run=False):
    if not os.path.isfile(SUPER_IMG):
        print(f"[err] 官方 super.img 缺失: {SUPER_IMG}（先 unpack）")
        sys.exit(1)

    super_size = os.path.getsize(SUPER_IMG)
    os.makedirs(WORK_DIR, exist_ok=True)
    os.makedirs(ROM_DIST, exist_ok=True)

    parts = []
    for part, src in PART_SOURCES.items():
        if not os.path.isfile(src):
            if part in REQUIRED:
                print(f"[err] 必需分区 {part} 缺失: {src}")
                sys.exit(1)
            print(f"[warn] {part} 源缺失，跳过: {src}")
            continue
        psize = os.path.getsize(src)
        capacity = max(psize, SUPER_RESERVE.get(part, 0))
        parts.append((part, src, psize, capacity))
        extra = f"（预留 {capacity} B）" if capacity != psize else ""
        print(f"[super] 分区 {part}_a ({psize} B){extra} <- {src}")
    if not parts:
        print("[err] 未收集到任何分区镜像", file=sys.stderr)
        sys.exit(1)

    alloc_sum = sum(cap for _p, _s, _sz, cap in parts)
    if alloc_sum + 256 * 1024 * 1024 > super_size:
        new_size = ((alloc_sum + 256 * 1024 * 1024 + (1 << 20) - 1) >> 20) << 20
        print(f"[super] 预留冗余撑大 super: {super_size} -> {new_size} B")
        super_size = new_size
    else:
        print(f"[super] super 总大小保持官方值 {super_size} B"
              f"（分区合计 {alloc_sum} B + 256 MiB 余量仍在其中）")

    if dry_run:
        print(f"[..] (dry) lpmake 重建 super（{super_size} B），{len(parts)} 个逻辑分区")
        for p, s, sz, cap in parts:
            extra = f"（预留 {cap} B）" if cap != sz else ""
            print(f"      {p + '_a':14s} <- {s}（{sz} B）{extra}")
        return 0

    work = os.path.join(WORK_DIR, "super.work.img")
    if os.path.exists(work):
        os.remove(work)

    # 1. lpmake 仅 metadata（分区块数据后续各自 dd 写入）
    lp = [
        "lpmake",
        "--metadata-size", str(METADATA_SIZE),
        "--metadata-slots", str(METADATA_SLOTS),
        "--super-name", "super",
        "--device", f"super:{super_size}",
        "--group", f"main_a:{super_size}",
        "--group", "main_b:0",
        "--virtual-ab",
    ]
    for part, _src, _psize, capacity in parts:
        lp += ["--partition", f"{part}_a:readonly:{capacity}:main_a"]
    lp += ["--force-full-image", "--output", work]
    print("[super] lpmake（仅 metadata）...")
    _run_tool("lpmake", lp)

    # 2. lpdump 定位各分区起始扇区
    print("[super] lpdump 解析分区偏移...")
    result = _run_tool("lpdump", [work], capture=True)
    offsets = {}
    for line in result.stdout.splitlines():
        if not line.startswith("super:"):
            continue
        fields = line.split()
        offsets[fields[4].rstrip(":")] = int(fields[1])

    # 3. 按偏移 dd 写入各分区数据
    print("[super] 写入分区数据...")
    with open(work, "r+b") as out:
        for part, src, psize, _capacity in parts:
            pname = f"{part}_a"
            sector = offsets.get(pname)
            if sector is None:
                print(f"[warn] {pname} 偏移未找到，跳过写入")
                continue
            print(f"[super] dd {pname} @ sector {sector}（{os.path.basename(src)}）")
            out.seek(sector * SECTOR)
            remaining = psize
            with open(src, "rb") as r:
                while remaining > 0:
                    chunk = min(remaining, COPY_CHUNK)
                    out.write(r.read(chunk))
                    remaining -= chunk

    # 4. 回读自检
    ok = True
    for part, src, _psize, _capacity in parts:
        if not lpmemeta.verify_partition(work, part, src):
            ok = False
    if not ok:
        print("[err] 回读校验失败，不产出 super.img")
        sys.exit(1)

    shutil.copy2(work, SUPER_DIST)
    print(f"[ok] super.img -> {SUPER_DIST}"
          f"（{os.path.getsize(SUPER_DIST)} B = {os.path.getsize(SUPER_DIST) // 2**20} MB）")
    return 0


def main():
    ap = argparse.ArgumentParser(description="gold-15 super.img lpmake 全量重建")
    ap.add_argument("--dry-run", action="store_true", help="只打印不执行")
    args = ap.parse_args()
    sys.exit(build_super_lpmake(dry_run=args.dry_run))


if __name__ == "__main__":
    main()