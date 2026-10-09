#!/usr/bin/env python3
"""组装 gold-15 最终盘集 vda.img + data.qcow2。

参考 pioneer-phone recipe/repack.sh 的 assemble_vda 路线：
  1. 从 lineage 底包（UTM 虚机）提取 vda.qcow2 -> raw，作为引导盘（GPT + preloader/lk +
     boot 等分区，virtio 内核可引导）；
  2. 拷贝引导盘到目标目录产物 vda.img，并以官方 super.img（pack_super 注入 vendor/
     vendor_dlkm 后的 SUPER_DIST）GPT 原位替换其 super 分区（官方 9.1GB <= 底包 12.8GB
     分区，原地写入）；
  3. 生成 data.qcow2 稀疏种子（daemon 以可写 overlay 叠于其上）。
  4. 空分区预格式化：vda 内 metadata -> ext4（容量按 GPT），data -> f2fs（见 blankfmt.py）。

目标目录（VERSION_DIR，daemon 运行盘集）：
  /Volumes/work/cloud-phone-hub/out/device/gold-15/15/os3.0.5.0.vnqmixm/{vda.img,data.qcow2}

用法：
  python3 build/scripts/pack-roms.py
  python3 build/scripts/pack-roms.py --force      # 强制重建并覆盖
  python3 build/scripts/pack-roms.py --dry-run    # 只打印不执行
"""

import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from buildenv import (  # noqa: E402
    BOOT_DIST, LINEAGE_VDA_MEMBER, LINEAGE_ZIP, PATCH_DIST, SUPER_DIST, TMP_DIR,
    VENDOR_BOOT_DIST, VERSION_DATA, VERSION_DIR, VERSION_VDA,
)
from buildenv import _qemu_img  # noqa: E402
import blankfmt  # noqa: E402
import lpmemeta  # noqa: E402

# super 内逻辑分区（无槽位名），供 --list-partitions 遍历展示
SUPER_LOGICAL_NAMES = (
    "system", "system_ext", "product", "vendor", "vendor_dlkm",
    "odm", "odm_dlkm", "system_dlkm", "mi_ext",
)

SECTOR = 512
GPT_SIGNATURE = b"EFI PART"
# data.qcow2 虚拟大小（与 cloud-phone-hub pixel6 基线一致，110 GiB 稀疏种子）
DATA_VIRT_BYTES = 118398996480
WORK_DIR = os.path.join(TMP_DIR, "pack")
VDA_RAW_CACHE = os.path.join(WORK_DIR, "vda.raw")


def _detect_sector(f):
    for ss in (512, 4096):
        f.seek(ss)
        if f.read(8) == GPT_SIGNATURE:
            return ss
    return SECTOR


def _gpt_replace_partition(vda_path, part_name, repl_img):
    """GPT 原位替换 vda.img 的 part_name 分区数据（需 len(repl) <= 该分区分配大小）。"""
    repl_size = os.path.getsize(repl_img)
    with open(vda_path, "r+b") as f:
        sector = _detect_sector(f)
        f.seek(sector)
        hdr = f.read(92)
        if hdr[0:8] != GPT_SIGNATURE:
            raise SystemExit(f"[err] {vda_path} 不是合法 GPT 镜像")
        entry_lba, num, esize = struct.unpack("<QII", hdr[72:88])
        f.seek(entry_lba * sector)
        entries = f.read(num * esize)
        target = None
        for i in range(num):
            e = entries[i * esize:(i + 1) * esize]
            first, last = struct.unpack_from("<QQ", e, 32)
            if first == 0 and last == 0:
                continue
            name = e[56:128].decode("utf-16-le", "ignore").rstrip("\x00")
            if name == part_name:
                target = (first, last, name)
                break
        if not target:
            raise SystemExit(f"[err] {vda_path} 中未找到 {part_name} 分区")
        first, last, name = target
        part_size = (last - first + 1) * sector
        if repl_size > part_size:
            raise SystemExit(
                f"[err] {part_name} 镜像 {repl_size} B > GPT 分区 {part_size} B，无法原位替换")

        print(f"[gpt] 替换 {name}: {repl_size // 2**20}MB / {part_size // 2**20}MB")
        f.seek(first * sector)
        with open(repl_img, "rb") as src:
            remaining = repl_size
            while remaining > 0:
                buf = src.read(min(16 * 1024 * 1024, remaining))
                if not buf:
                    break
                f.write(buf)
                remaining -= len(buf)
        if part_size > repl_size:
            f.write(b"\x00" * (part_size - repl_size))
    return True


def _gpt_replace_super(vda_path, repl_img):
    """GPT 原位替换 vda.img 的 super 分区数据（复用通用替换）。"""
    return _gpt_replace_partition(vda_path, "super", repl_img)


# ---------- EFI grub.cfg 引导策略固化（固定 _a + 菜单零等待）----------
#
# 1) 槽位固化：GRUB 的 abootctrl 逻辑在 active 槽连续 3 次启动未成功（XXX）或标记
#    not bootable 时会 save_env 切到另一槽（persist/grubenv_abootctrl）。本项目官方
#    super 的 system_b/vendor_b 等为 0 长度分区，_b 必然挂载失败；且 update_verifier
#    被内核白名单屏蔽，is_successful 永不为真 → 每 3 轮启动跳槽一次，白白耗一轮引导。
#    注入：pre_boot_hook 起始处固定 active 槽为 a + 禁自动切槽。
# 2) 菜单零等待：GRUB 菜单 15s 等待来自 persist grubenv 的 grub_timeout；强制置 0。

GRUB_SLOT_MARKER = "# CDH: 固定启动 A 槽"
GRUB_CFG_PATH = "/boot/grub/grub.cfg"
# 槽位补丁锚点：pre_boot_hook 内 set androidboot_mode=$1 行（vda 底包 grub.cfg 用 tab
# 缩进、两个 lineage 版本文本有差异，用正则放宽空白；该行全文件唯一）。
GRUB_SLOT_ANCHOR_RE = re.compile(r"([ \t]*set androidboot_mode=\$1\r?\n)")
GRUB_SLOT_PATCH = """        # CDH: 固定启动 A 槽（_b 为零长度分区不可用），并禁 abootctrl 自动切槽
        if [ "$abootctrl_global_active_slot" != "a" ]; then
                set abootctrl_global_active_slot=a
                export abootctrl_global_active_slot
        fi
        set abootctrl_global_no_auto_slot_switch=true
        export abootctrl_global_no_auto_slot_switch
        ab_update_active_slot_boot_count clear
        ab_set_active_slot_is_bootable true
"""

GRUB_TIMEOUT_PATCH = """
# CDH: 引导菜单零等待（0 秒）
set grub_timeout=0
"""
GRUB_TIMEOUT_MARKER = "# CDH: 引导菜单零等待"
GRUB_TIMEOUT_ANCHOR = "load_env -f ${partition_persist}/grubenv android_theme grub_android_default grub_timeout"

# Remote KeyMint verified boot 参数注入：guest keymint HAL（cuttlefish remote）
# 读 ro.boot.vbmeta.digest / device_state / verifiedbootstate（源自 androidboot.*
# cmdline），host secure_env 以 device.go Note135G15.FactoryTEEVerifiedBootHash 校验，
# 两端不一致时 guest HAL 停在 initialize。gold lineage 底包无 AVB 绑定，直接注入
# 固定值（与 pixel6 同款）即可。锚点 export kernel_cmdline_dynamic 全文件唯一。
GRUB_VBMETA_MARKER = "# CDH: Remote KeyMint verified boot 参数"
GRUB_VBMETA_ANCHOR = "\texport kernel_cmdline_dynamic"
GRUB_VBMETA_PATCH = """\t# CDH: Remote KeyMint verified boot 参数（与 device.go FactoryTEEVerifiedBootHash 一致）
\tset kernel_cmdline_dynamic="${kernel_cmdline_dynamic} androidboot.vbmeta.digest=fe049d35e450d9faf4b26788864f7b5fd5b857276d5271b067ef0e0268acff6f androidboot.vbmeta.device_state=locked androidboot.verifiedbootstate=green"
"""


def _efi_mtools_args(vda_path):
    """定位 EFI 分区，返回 mtools 参数（-i image@@byte_offset）；无 EFI 分区返回 None。"""
    sector, gpt = _read_gpt(vda_path)
    for name, (first, last) in gpt.items():
        if name.lower() == "efi":
            return ["-i", f"{vda_path}@@{first * sector}"]
    print("[warn] vda.img 未找到 EFI 分区，跳过 grub.cfg 引导策略固化")
    return None


def patch_efi_grub_slot_a(vda_path):
    """幂等注入 grub.cfg（固定 _a + 菜单零等待 + vbmeta）；失败仅告警不阻断打包。"""
    mi = _efi_mtools_args(vda_path)
    if mi is None:
        return False
    for tool in ("mtype", "mcopy"):
        if not shutil.which(tool):
            print(f"[warn] 缺少 mtools {tool}，跳过 grub.cfg 引导策略固化")
            return False
    try:
        cur = subprocess.run(["mtype"] + mi + [GRUB_CFG_PATH],
                             capture_output=True, text=True, check=True).stdout
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        print(f"[warn] 读取 {GRUB_CFG_PATH} 失败（{e}），跳过 grub.cfg 引导策略固化")
        return False
    orig = cur

    if GRUB_SLOT_MARKER not in cur:
        m = GRUB_SLOT_ANCHOR_RE.search(cur)
        if not m:
            print("[warn] grub.cfg 未找到锚点 set androidboot_mode，跳过槽位固化")
            return False
        cur = cur[:m.end()] + GRUB_SLOT_PATCH + cur[m.end():]
    if GRUB_TIMEOUT_MARKER not in cur:
        if GRUB_TIMEOUT_ANCHOR not in cur:
            print(f"[warn] grub.cfg 未找到锚点 {GRUB_TIMEOUT_ANCHOR!r}，跳过菜单零等待")
            return False
        cur = cur.replace(GRUB_TIMEOUT_ANCHOR,
                          GRUB_TIMEOUT_ANCHOR + GRUB_TIMEOUT_PATCH, 1)
    if GRUB_VBMETA_MARKER not in cur:
        if GRUB_VBMETA_ANCHOR not in cur:
            print(f"[warn] grub.cfg 未找到锚点 {GRUB_VBMETA_ANCHOR!r}，跳过 vbmeta 注入")
            return False
        cur = cur.replace(GRUB_VBMETA_ANCHOR,
                          GRUB_VBMETA_PATCH + GRUB_VBMETA_ANCHOR, 1)

    if cur == orig:
        print("[..] EFI grub.cfg 已含全部 CDH 固化，跳过")
        return True

    host = os.path.join(WORK_DIR, "grub.cfg.cdh")
    os.makedirs(WORK_DIR, exist_ok=True)
    with open(host, "w") as f:
        f.write(cur)
    try:
        subprocess.run(["mcopy", "-o"] + mi + [host, "::" + GRUB_CFG_PATH],
                       capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"[warn] 写回 grub.cfg 失败（{e.stderr.strip()}）")
        return False
    verify = subprocess.run(["mtype"] + mi + [GRUB_CFG_PATH],
                            capture_output=True, text=True, check=True).stdout
    if GRUB_SLOT_MARKER not in verify or GRUB_TIMEOUT_MARKER not in verify \
            or GRUB_VBMETA_MARKER not in verify:
        print("[warn] grub.cfg 写回校验失败")
        return False
    print("[ok] EFI grub.cfg 已固化：固定 _a、禁自动切槽、菜单零等待、vbmeta")
    return True


# ---------- 单分区增量补丁：只改某分区，dd 写回 vda.img ----------
#
# 原理：GPT 头 CRC 只覆盖分区表条目（名字/GUID/LBA），liblp 元数据 checksum 只覆盖
# 元数据表——两者都不覆盖分区数据本身。只要新镜像 ≤ 已分配容量，直接把数据写到对应
# 字节偏移即可，无需重算校验和、无需重建 super/vda。物理分区（super/boot/…）偏移取自
# GPT；逻辑分区（vendor/system/…）先在 GPT 定位 super、再读 super 内 liblp extent 定位。
# 预留冗余在 pack_super.py 的 SUPER_RESERVE 声明。详见 skills/build-device/SKILL.md。


def _read_gpt(vda_path):
    """解析 vda.img 主 GPT，返回 (sector, dict[name -> (first_lba, last_lba)])。"""
    with open(vda_path, "rb") as f:
        sector = _detect_sector(f)
        f.seek(sector)
        hdr = f.read(92)
        if hdr[0:8] != GPT_SIGNATURE:
            raise SystemExit(f"[err] {vda_path} 不是合法 GPT 镜像")
        entry_lba, num, esize = struct.unpack("<QII", hdr[72:88])
        f.seek(entry_lba * sector)
        entries = f.read(num * esize)
    out = {}
    for i in range(num):
        e = entries[i * esize:(i + 1) * esize]
        first, last = struct.unpack_from("<QQ", e, 32)
        if first == 0 and last == 0:
            continue
        name = e[56:128].decode("utf-16-le", "ignore").rstrip("\x00")
        if name:
            out[name] = (first, last)
    return sector, out


def _locate_partition(vda_path, name):
    """定位分区在 vda.img 中的 (data_offset_bytes, capacity_bytes, kind)。

    kind ∈ {"physical","logical"}；找不到返回 None。
    """
    sector, gpt = _read_gpt(vda_path)
    if name in gpt:
        first, last = gpt[name]
        return (first * sector, (last - first + 1) * sector, "physical")
    if "super" not in gpt:
        return None
    super_base = gpt["super"][0] * sector
    # 逻辑分区：从 vda 内偏移 super_base 处解析 liblp（lpmemeta 支持 base 偏移）
    parts, extents = lpmemeta.read_super(vda_path, base=super_base)
    segs = lpmemeta.partition_extents(parts, extents, name)
    if not segs:
        return None
    # segs 的 offset 是 super 内相对字节偏移；叠加 super_base 得 vda 内绝对偏移
    rel_off = segs[0][0]
    capacity = sum(sz for _o, sz in segs)
    return (super_base + rel_off, capacity, "logical")


def list_partitions(vda_path):
    """列出 vda.img 里所有可增量补丁的分区（物理 + 逻辑）及容量。"""
    if not os.path.isfile(vda_path):
        print(f"[err] 找不到 vda.img: {vda_path}")
        return 1
    print(f"[list] {vda_path} ({os.path.getsize(vda_path)} B)")
    sector, gpt = _read_gpt(vda_path)
    print("  物理分区（GPT）:")
    for name in sorted(gpt, key=lambda n: gpt[n][0]):
        first, last = gpt[name]
        cap = (last - first + 1) * sector
        print(f"    {name:14s} offset={first * sector:>13d} capacity={cap:>13d} B")
    if "super" in gpt:
        super_base = gpt["super"][0] * sector
        parts, extents = lpmemeta.read_super(vda_path, base=super_base)
        print("  逻辑分区（super/liblp）:")
        for name in SUPER_LOGICAL_NAMES:
            segs = lpmemeta.partition_extents(parts, extents, name)
            if segs:
                off = super_base + segs[0][0]
                cap = sum(sz for _o, sz in segs)
                print(f"    {name:14s} offset={off:>13d} capacity={cap:>13d} B")
    return 0


def patch_partition(vda_path, name, image_path, dry_run=False):
    """把 image_path 直接 dd 写入 vda.img 中 name 分区偏移（新镜像 ≤ 容量才允许）。"""
    if not os.path.isfile(vda_path):
        print(f"[err] 找不到 vda.img: {vda_path}")
        return 1
    if not os.path.isfile(image_path):
        print(f"[err] 找不到分区镜像: {image_path}")
        return 1
    loc = _locate_partition(vda_path, name)
    if loc is None:
        print(f"[err] vda.img 中找不到分区: {name}")
        return 1
    offset, capacity, kind = loc
    new_size = os.path.getsize(image_path)
    print(f"[patch] {name}（{kind}）: offset={offset} capacity={capacity} B 新镜像={new_size} B")
    if new_size > capacity:
        over = new_size - capacity
        print(f"[err] 新镜像超出已分配容量 {over} B。")
        if kind == "logical":
            print(f"      提高 pack_super.py SUPER_RESERVE['{name}'] 后重跑 super+pack 全量重建。")
        else:
            print(f"      物理分区容量由 GPT 布局固定，需重建 vda。")
        return 1
    if dry_run:
        print(f"[..] (dry) 将 dd {image_path} -> {vda_path} @ {offset}（{new_size} B）")
        return 0
    # 原地覆盖：只写新镜像字节；extent 尾部残留旧数据无害（fs superblock 自带真实大小，
    # dm-linear 只映射固定扇区，内核不会读到尾部残留）。
    with open(vda_path, "r+b") as f:
        f.seek(offset)
        with open(image_path, "rb") as src:
            shutil.copyfileobj(src, f, 16 * 1024 * 1024)
        f.flush()
        os.fsync(f.fileno())
    print(f"[ok] 已增量写入 {name}: {new_size} B @ {offset}（未改 GPT/liblp 校验和）")
    return 0


def _ensure_vda_raw():
    """确保底包 vda.raw 存在（从 UTM zip 提取 qcow2 -> qemu-img convert raw，缓存）。"""
    if os.path.isfile(VDA_RAW_CACHE):
        return VDA_RAW_CACHE
    if not os.path.isfile(LINEAGE_ZIP):
        raise SystemExit(f"[err] 底包 zip 缺失: {LINEAGE_ZIP}")
    os.makedirs(WORK_DIR, exist_ok=True)
    tmp_qcow = os.path.join(WORK_DIR, "vda.qcow2")
    if not os.path.isfile(tmp_qcow):
        print(f"[..] 提取 {LINEAGE_VDA_MEMBER} from 底包")
        with zipfile.ZipFile(LINEAGE_ZIP) as zf:
            if LINEAGE_VDA_MEMBER not in zf.namelist():
                raise SystemExit(f"[err] 底包中未找到成员 {LINEAGE_VDA_MEMBER}")
            with zf.open(LINEAGE_VDA_MEMBER) as src, open(tmp_qcow, "wb") as dst:
                shutil.copyfileobj(src, dst, 16 * 1024 * 1024)
    qemu_img = _qemu_img()
    print(f"[..] qemu-img convert qcow2 -> raw: {os.path.basename(VDA_RAW_CACHE)}")
    subprocess.run([qemu_img, "convert", "-f", "qcow2", "-O", "raw", tmp_qcow, VDA_RAW_CACHE],
                   check=True)
    return VDA_RAW_CACHE


def _make_data_qcow2(force, dry_run):
    """生成 /data 稀疏种子——**已预格式化 f2fs**（见 blankfmt.py 的取证说明）。"""
    if os.path.isfile(VERSION_DATA) and not force:
        print(f"[ok] data.qcow2 已就位（跳过）: {os.path.getsize(VERSION_DATA)} B")
        return
    if dry_run:
        print(f"[..] (dry) 将生成已预格式化（f2fs）的 data.qcow2：{DATA_VIRT_BYTES} B")
        return
    blankfmt.data_f2fs_qcow2(VERSION_DATA, DATA_VIRT_BYTES)
    print(f"[ok] data.qcow2（f2fs 预格式化）-> {VERSION_DATA}"
          f"（{os.path.getsize(VERSION_DATA)} B）")


def _preformat_metadata(vda_path, dry_run=False):
    """把 vda 内 /metadata 分区预格式化为 ext4（空分区运行期 format 会被 SELinux 拒）。

    复用已有 GPT 定位与原地写入（patch_partition）：容量校验天然生效。
    """
    loc = _locate_partition(vda_path, "metadata")
    if loc is None:
        print("[warn] vda 内无 metadata 分区，跳过预格式化")
        return
    _offset, capacity, _kind = loc
    if dry_run:
        print(f"[..] (dry) 将把 metadata 分区（{capacity} B）预格式化为 ext4")
        return
    img = os.path.join(WORK_DIR, "metadata.ext4.img")
    blankfmt.metadata_ext4(img, capacity)
    if patch_partition(vda_path, "metadata", img) != 0:
        sys.exit(1)
    os.remove(img)


def pack(force=False, dry_run=False):
    os.makedirs(VERSION_DIR, exist_ok=True)

    # 1) 底包 vda.raw -> 产物 vda.img（dry 只规划，不要求 super 已产出）
    vda_src = _ensure_vda_raw() if not dry_run else None
    if dry_run:
        print(f"[..] (dry) 将拷贝底包 vda.raw -> {VERSION_VDA} 并 GPT 替换 super（{SUPER_DIST}）")
        _preformat_metadata(VERSION_VDA, dry_run=True)
        _make_data_qcow2(force, dry_run)
        return 0
    if not os.path.isfile(SUPER_DIST):
        print(f"[err] 缺少 super.img: {SUPER_DIST}（先 pack_super）")
        sys.exit(1)

    if not os.path.isfile(VERSION_VDA) or force:
        shutil.copy2(vda_src, VERSION_VDA)
        print(f"[ok] 拷贝底包 vda -> {VERSION_VDA}")
    else:
        print(f"[ok] vda.img 已就位（仅刷新 super 分区）: {os.path.getsize(VERSION_VDA)} B")
    # super 分区无条件原位刷新：SUPER_DIST（pack_super 产物，官方 super 零改动）
    # 必须同步进实例盘，否则 super 阶段的更新不会生效。
    _gpt_replace_super(VERSION_VDA, SUPER_DIST)

    # boot 分区（A/B 槽）写入我们的 boot.img：自编译内核（含 getname_flags 重定向）
    # + first_stage fstab（含 vdc -> /mnt 挂载）。boot_a 容量 = boot.img 大小。
    if os.path.isfile(BOOT_DIST):
        for slot in ("boot_a", "boot_b"):
            _gpt_replace_partition(VERSION_VDA, slot, BOOT_DIST)
    else:
        print(f"[warn] 缺 boot.img: {BOOT_DIST}（先 build-boot），vda 沿用底包 boot 分区")

    # vendor_boot 分区（A/B 槽）写入处理后的 vendor_boot.img：删除其 ramdisk fragment
    # 内的 lineage 原版 fstab 副本（含 odm 挂载），让 boot.img 的 fstab 生效。
    if os.path.isfile(VENDOR_BOOT_DIST):
        for slot in ("vendor_boot_a", "vendor_boot_b"):
            _gpt_replace_partition(VERSION_VDA, slot, VENDOR_BOOT_DIST)
    else:
        print(f"[warn] 缺 vendor_boot.img: {VENDOR_BOOT_DIST}（先 build-vendor-boot），"
              f"vda 沿用底包 vendor_boot 分区（其 fstab 会覆盖 boot.img 的）")

    # EFI grub.cfg 槽位固化：固定启动 _a、禁用 abootctrl 自动切槽（_b 零长度分区）
    patch_efi_grub_slot_a(VERSION_VDA)

    # patch.img（lineage vendor/vendor_dlkm 修补内容 + /mnt 骨架，daemon 以 vdc 挂 /mnt）
    # 一并落到产物目录，供 daemon 自动识别（applyDeviceImage 检测 patch.img -> -gsi-image）
    if os.path.isfile(PATCH_DIST):
        patch_target = os.path.join(VERSION_DIR, "patch.img")
        shutil.copy2(PATCH_DIST, patch_target)
        print(f"[ok] patch.img -> {patch_target}（{os.path.getsize(patch_target)} B）")
    else:
        print(f"[warn] 缺 patch.img: {PATCH_DIST}（先 build-patch），vdc 将不挂载 /mnt")

    # 3) 空分区预格式化：/metadata(ext4) 与 /data(f2fs)
    #    云机盘集是全新的，运行期 format 会被 e2fs 域的 SELinux 拒（见 blankfmt.py）。
    _preformat_metadata(VERSION_VDA)

    # 4) data.qcow2 稀疏种子（已预格式化 f2fs）
    _make_data_qcow2(force, dry_run)
    return 0


def main():
    ap = argparse.ArgumentParser(description="gold-15 盘集打包 vda+data")
    ap.add_argument("--force", action="store_true", help="目标已存在时强制重建")
    ap.add_argument("--dry-run", action="store_true", help="只打印不执行")
    ap.add_argument("--patch", metavar="NAME=IMAGE", action="append", default=[],
                    help="增量补丁：把 IMAGE 直接 dd 写入已有 vda.img 的 NAME 分区，"
                         "不重建 super/vda（新镜像须 ≤ 已分配容量）。可多次指定。"
                         "例: --patch vendor=out/dist/rom/vendor.img")
    ap.add_argument("--list-partitions", action="store_true",
                    help="列出已有 vda.img 里所有可增量补丁的分区及容量")
    ap.add_argument("--vda", metavar="PATH", default=None,
                    help="指定 vda.img 路径（默认 VERSION_VDA），供 --patch/--list-partitions")
    args = ap.parse_args()

    vda_path = args.vda or VERSION_VDA

    if args.list_partitions:
        sys.exit(list_partitions(vda_path))

    if args.patch:
        rc = 0
        for spec in args.patch:
            if "=" not in spec:
                print(f"[err] --patch 需 NAME=IMAGE 格式: {spec}")
                rc = 1
                continue
            name, image = spec.split("=", 1)
            rc |= patch_partition(vda_path, name, image, dry_run=args.dry_run)
        sys.exit(rc)

    sys.exit(pack(force=args.force, dry_run=args.dry_run))


if __name__ == "__main__":
    main()