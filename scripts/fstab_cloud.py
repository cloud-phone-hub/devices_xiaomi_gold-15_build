#!/usr/bin/env python3
"""云机 fstab 派生规则（单一来源，两个消费方共用）。

官方 MTK fstab 直接用在云机会卡住/走错，按下列规则派生为云机版。每条都对应实测证据：

  1) 剥掉挂载选项里的 avb / avb=… / avb_keys=…：云机无 vbmeta* 分区、不开 AVB；
     留着 init 的 BlockDevInitializer 会等 vbmeta uevent 直到 10s 超时 abort 重启
     （实测：partition(s) not found in /sys ... vbmeta_a/vbmeta_system_a/vbmeta_vendor_a
      → InitFatalReboot signal 6 → reboot 循环）。
  2) 丢弃 device 为 /dev/block/by-name/<名> 而该分区不在云机 GPT 的行（MTK 物理分区
     rescue/protect1/protect2/nvdata/nvcfg/lk/tee/scp/…/vbmeta*）。其中带 wait 的条目
     属"必需挂载"，二阶段 mount_all 会逐个等 uevent 超时（实测每次 20s，合计 120s）。
  3) /data：云机在独立 virtio 盘，by-name/userdata -> /dev/block/vdb。
  4) persist：云机该盘是 vfat（GRUB grubenv 依赖），官方写 ext4 会被 fs_mgr check 后
     格式化；改为 vfat 行（与 note13 云机版一致）。

消费方（一阶段与二阶段读的是两个不同文件，故两边都要派生，口径必须一致）：
  * build-boot.py   取官方 vendor 的 /etc/fstab.mt6833 派生后注入 boot ramdisk
                    （一阶段 first_stage_mount 读 ramdisk 内那份）
  * build/vendor/build/fstab.py  同样派生后就地覆盖 vendor 内的 /etc/fstab.mt6833
                    （二阶段 mount_all --early 读 /vendor 那份）
"""

import os


def cloud_partitions(lineage_dir):
    """云机 GPT 内实际存在的分区名（= lineage 底包解包导出的 <名>.img）。"""
    names = {f[:-4] for f in os.listdir(lineage_dir) if f.endswith(".img")}
    if not names:
        raise RuntimeError(f"{lineage_dir} 内无分区镜像，无法判定云机分区")
    return names


# 云机 persist 行的权威写法（vfat；与 note13 云机版一致）
CLOUD_PERSIST_LINE = (
    "/dev/block/by-name/persist /mnt/vendor/persist vfat "
    "rw,sync,shortname=lower,uid=1000,gid=1000,dmask=007,fmask=117,"
    "context=u:object_r:persist_file:s0 formattable"
)


def adapt(raw, existing):
    """派生云机版 fstab。

    raw      官方 fstab 字节
    existing 云机 GPT 内存在的分区名集合（cloud_partitions）
    返回 (字节, 被丢弃的分区名列表, 改写说明列表)
    """
    kept, dropped, rewrote = [], [], []
    for line in raw.decode("utf-8", "replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            kept.append(line)
            continue
        f = s.split()
        if len(f) < 5:
            kept.append(line)
            continue
        dev, flags = f[0], f[4]
        if dev.startswith("/dev/block/by-name/"):
            name = dev[len("/dev/block/by-name/"):]
            if name == "userdata":
                f[0] = "/dev/block/vdb"
                rewrote.append(f"{dev} -> /dev/block/vdb")
            elif name == "persist":
                kept.append(CLOUD_PERSIST_LINE)
                rewrote.append("persist: 官方 ext4 -> 云机 vfat")
                continue
            elif (name not in existing and f"{name}_a" not in existing
                  and f"{name}_b" not in existing):
                dropped.append(name)
                continue
        tokens = [t for t in flags.split(",")
                  if t != "avb" and not t.startswith(("avb=", "avb_keys="))]
        f[4] = ",".join(tokens)
        kept.append(" ".join(f))

    return ("\n".join(kept) + "\n").encode(), dropped, rewrote


def report(dropped, rewrote, where=""):
    """打印派生摘要（供各消费方调用，口径统一）。"""
    uniq = sorted(set(dropped))
    print(f"[ok] fstab 云机派生{where}：剔除非云机分区行 {len(dropped)} 行"
          f"（{len(uniq)} 个分区：{'、'.join(uniq[:12])}"
          f"{'…' if len(uniq) > 12 else ''}）")
    for r in rewrote:
        print(f"     改写 {r}")