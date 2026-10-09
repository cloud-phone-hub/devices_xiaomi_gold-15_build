#!/usr/bin/env python3
"""把官方 /etc/fstab.mt6833 云机化后就地覆盖 vendor 内的那份。

二阶段 `mount_all --early`（/vendor/etc/init/hw/init.mt6833.rc）读的是 /vendor 里的
fstab，不是 boot ramdisk 那份；官方那份未派生时的实测后果：
  * 逐个等不存在的 MTK 分区 uevent，每次 20s，`mount_all --early` 共耗 120186ms；
  * /data 仍指 by-name/userdata（云机在 vdb）。

派生规则见 fstab_cloud.py（与 build-boot.py 注入 ramdisk 的那份共用同一套规则）。
覆盖方式：宿主机派生后落进工作目录，容器内 `cp` 覆盖原文件——就地覆盖保留其
owner/mode/SELinux 标签，无需重打标签。

对外：
  generate(official_img, work_dir)   派生并落盘，返回容器脚本片段
"""

import os
import subprocess
import sys

import base_fs
from buildenv import LINEAGE_OUT_DIR
from fstab_cloud import adapt, cloud_partitions, report

FSTAB_IN_VENDOR = "/etc/fstab.mt6833"
WORK_NAME = "cloud_fstab.mt6833"


def generate(official_img, work_dir):
    """取官方 vendor 内 fstab → 云机派生 → 落工作目录，返回容器覆盖脚本。"""
    res = subprocess.run([base_fs.erofs_tool(), f"--path={FSTAB_IN_VENDOR}",
                          "--cat", official_img],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0 or not res.stdout:
        print(f"[err] 官方 vendor 缺少 {FSTAB_IN_VENDOR}: "
              f"{res.stderr.decode('utf8', 'replace')}")
        sys.exit(1)

    data, dropped, rewrote = adapt(res.stdout, cloud_partitions(LINEAGE_OUT_DIR))
    report(dropped, rewrote, where="（/vendor 二阶段）")
    os.makedirs(work_dir, exist_ok=True)
    with open(os.path.join(work_dir, WORK_NAME), "wb") as f:
        f.write(data)
    print(f"[ok] /vendor{FSTAB_IN_VENDOR} 将替换为云机派生版（{len(data)} B）")
    return f"cp {base_fs.C_WORKDIR}/{WORK_NAME} {base_fs.C_DST_DIR}{FSTAB_IN_VENDOR}\n"