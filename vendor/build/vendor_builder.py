#!/usr/bin/env python3
"""gold-15 vendor.img 重打包编排（由 build-vendor.py 薄入口调用）。

基座 = **官方（厂商）vendor_a.img**（EROFS），不是 lineage 基座。本文件只做编排与
容器脚本拼装，具体逻辑各自独立成模块：

  base_fs.py   官方 EROFS → ext4 基座转换（容器内 root 双挂载 cp -a，保
               uid/gid/mode + security.selinux；宿主与容器 overlay 都保不住标签）
  inject.py    build/vendor/patch/ 注入（uid/gid 0:0、mode 0644、baselines 标签回填）
  remove.py    按 build/vendor/remove.txt 删除官方 vendor 内不需要的条目（rc 类
               "定义式"文件只能删不能覆盖）
  props.py     改写官方 vendor 内 prop 文件（如 /odm/etc/build.prop 的 abilist32，
               官方声明 32+64 而云机是 arm64-only）
  fstab.py     把官方 /etc/fstab.mt6833 云机化后就地覆盖（二阶段 mount_all 读它；
               规则来自 build/scripts/fstab_cloud.py，与 boot ramdisk 那份同口径）

产物 out/dist/rom/vendor.img。

⚠️ 注入面：`build/vendor/source/**`（CDH HAL 子项目）由 source_build 编译落盘
`out/dist/vendor`，与 `build/vendor/patch/` 一并注入（inject.py 两棵树）；Remote
KeyMint 的 sepolicy 段由 sepolicy.py 追加到 vendor_sepolicy.cil + vendor_file_contexts。
当前已移植 keymint 一个子项目，其余 HAL 待后续。

⚠️ 体积：官方 vendor 未压缩内容 ~1.95 GiB，ext4 后 ~2.2 GiB > 官方 super 给
vendor_a 的 1.28 GiB → 落地必须走「重建 super 并放大 vendor 分区」（super 内部余量
1.54 GiB 足够）。

注：ext4 必然自带 /lost+found（e2fsck 缺它会重建，删不掉），官方 erofs 的 /vendor
没有该条目——一处已知形态差异。
"""

import os
import subprocess
import sys

import base_fs
import deps
import fstab
import inject
import props
import remove
import sepolicy
import source_build
import ueventd
from buildenv import (ROM_DIST, ROM_ROOT_DIR, TMP_DIR, VENDOR_DIST,
                      VENDOR_PATCH_DIR)

# 官方（厂商）vendor 逻辑分区（EROFS）
VENDOR_OFFICIAL = os.path.join(ROM_ROOT_DIR, "vendor_a.img")

WORK_DIR = os.path.join(TMP_DIR, "vendor")

# 容器：Linux root + erofs-utils + e2fsprogs（本仓库 builder 镜像已含）
CONTAINER_IMAGE = os.environ.get("CDH_VENDOR_BUILDER",
                                 "cloud-phone-hub-ubuntu-builder:latest")


def build_vendor(dry_run=False):
    if not os.path.isfile(VENDOR_OFFICIAL):
        print(f"[err] 官方 vendor 缺失: {VENDOR_OFFICIAL}（先 unpack）")
        sys.exit(1)
    if not os.path.isdir(VENDOR_PATCH_DIR):
        print(f"[err] 注入树缺失: {VENDOR_PATCH_DIR}")
        sys.exit(1)

    # 0) 编译依赖准备 + source 子构建（keymint HAL 等）：先编译落盘 out/dist/vendor，
    #    随后与 patch 树一并注入。deps 从**官方 gold images**（vendor_a/system_a，
    #    EROFS）研磨链接期运行库。
    deps.prepare_runtime_libs(dry_run=dry_run)
    source_build.run_source_builds(dry_run=dry_run)

    content = base_fs.content_bytes(VENDOR_OFFICIAL)
    size = base_fs.mirror_size(content)
    files = inject.patch_files()
    labels = inject.labels()
    lack = inject.missing(files, labels)
    removals = remove.paths()
    print(f"[vendor] 基座 = 官方 vendor_a.img（EROFS）: {VENDOR_OFFICIAL}")
    print(f"[vendor] 未压缩内容 {content} B；ext4 镜像 {size} B"
          f"（含注入预留 {base_fs.MIRROR_SLACK} B）；注入树 {len(files)} 个文件；"
          f"删除清单 {len(removals)} 条")
    print(f"[vendor] baselines 标签 {len(labels)} 条；覆盖注入树 "
          f"{len(files) - len(lack)}/{len(files)}")
    if lack:
        print("[err] baselines vendor.selinux-labels 缺以下键:")
        for f in lack:
            print("   ", f)
        sys.exit(1)

    if dry_run:
        print(f"[..] (dry) 容器 {CONTAINER_IMAGE}：" + base_fs.prepare(size).strip()
              .replace("\n", " | "))
        print(f"       {remove.script().strip()}（删除 {len(removals)} 条）")
        print(f"       {inject.script().strip()}（注入 {len(files)} 文件）")
        print(f"       {sepolicy.script().strip()}"
              f"（追加 keymint sepolicy 段：vendor_sepolicy.cil + vendor_file_contexts）")
        print(f"       {ueventd.script().strip()}"
              f"（追加 /dev/hvc* ueventd 授权）")
        print(f"       {props.script().strip()}"
              f"（改写 /vendor/{props.TARGET} 的 {len(props.SETS)} 个属性）")
        print(f"       cp <云机派生 fstab> -> /vendor{fstab.FSTAB_IN_VENDOR}"
              f"（二阶段 mount_all 读它）")
        print(f"       {base_fs.finish(os.path.basename(VENDOR_DIST)).strip()}"
              .replace("\n", " | "))
        return 0

    inject.write_workspace(WORK_DIR, labels)
    sepolicy.write_workspace(WORK_DIR)
    ueventd.write_workspace(WORK_DIR)
    props.write_workspace(WORK_DIR)
    fstab_script = fstab.generate(VENDOR_OFFICIAL, WORK_DIR)
    os.makedirs(ROM_DIST, exist_ok=True)
    if os.path.exists(VENDOR_DIST):
        os.remove(VENDOR_DIST)

    script = (base_fs.prepare(size) + remove.script() + inject.script()
              + sepolicy.script() + ueventd.script() + props.script() + fstab_script
              + base_fs.finish(os.path.basename(VENDOR_DIST)))
    mounts = (base_fs.mounts(VENDOR_OFFICIAL, ROM_DIST)
              + inject.mounts(WORK_DIR))
    cmd = ["docker", "run", "--rm", "-i", "--privileged"]
    for host, cont, ro in mounts:
        cmd += ["-v", f"{host}:{cont}:{'ro' if ro else 'rw'}"]
    cmd += [CONTAINER_IMAGE, "bash", "-s"]
    if subprocess.run(cmd, input=script.encode()).returncode != 0:
        print("[err] vendor 重打包失败")
        sys.exit(1)

    print(f"[ok] vendor.img -> {VENDOR_DIST}（{os.path.getsize(VENDOR_DIST)} B）")
    return 0