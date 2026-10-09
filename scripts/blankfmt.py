#!/usr/bin/env python3
"""构建期预格式化云机的空白分区（/metadata → ext4，/data → f2fs）。

为什么需要（实测证据）：云机盘集是"全新空白"的——note13 的实例盘是历史上格式化过的，
所以它从不触发运行期 format。空镜像会让 fs_mgr 走 `formattable` 分支去格式化，而 guest
的 `e2fs` 域被 SELinux 拦住：

  # /metadata：mke2fs 取不到设备大小
  avc: denied { read } comm="mke2fs" name="vda5" scontext=u:r:e2fs:s0
       tcontext=u:object_r:vd_device:s0 tclass=blk_file
  mke2fs: Permission denied while trying to determine filesystem size → Format failed
  # /data：mkfs.f2fs 判定 zoned 失败
  avc: denied { read } comm="make_f2fs" name="zoned" dev="sysfs" scontext=u:r:e2fs:s0
       tcontext=u:object_r:sysfs:s0 tclass=file
  make_f2fs: Error: Failed to check the device zoned model → exit(255)
  # 后果：/data 与 /metadata 都没挂上 → apexd 找不到 <data|metadata>/apex/sessions
  #       → apexd abort(signal 6) → reboot,apexd-failed（起机死循环）

构建期先格式化好，运行期只需 mount，从而绕开该 sepolicy 缺口（不改 guest 策略）。
工具在 docker builder 镜像里（mke2fs/tune2fs/mkfs.f2fs/qemu-img 均已含）。

对外：
  metadata_ext4(dst, size)        生成 size 字节的 ext4（含 init.mt6833.rc 的 tune2fs 参数）
  data_f2fs_qcow2(dst, virt_bytes) 生成已格式化的 f2fs data 盘（qcow2，稀疏种子）
"""

import os
import shlex
import subprocess
import sys

from buildenv import PROJECT_ROOT, TMP_DIR

CONTAINER_IMAGE = os.environ.get("CDH_VENDOR_BUILDER",
                                 "cloud-phone-hub-ubuntu-builder:latest")
WORK_DIR = os.path.join(TMP_DIR, "blankfmt")


def _docker(script):
    cmd = ["docker", "run", "--rm", "-v", f"{PROJECT_ROOT}:{PROJECT_ROOT}",
           "-w", PROJECT_ROOT, CONTAINER_IMAGE, "bash", "-lc", script]
    if subprocess.run(cmd).returncode != 0:
        print("[err] 容器内格式化失败")
        sys.exit(1)


def metadata_ext4(dst, size):
    """生成 size 字节的 ext4 镜像（容器内 mke2fs + tune2fs）。

    tune2fs 参数照抄真机 init.mt6833.rc 对 /metadata 的后续处理：
      exec /system/bin/tune2fs -O has_journal -u 10010 -r 4096 /dev/block/by-name/metadata
    """
    os.makedirs(WORK_DIR, exist_ok=True)
    q = shlex.quote(dst)
    _docker(
        "set -euo pipefail\n"
        f"truncate -s {size} {q}\n"
        f"mke2fs -q -F -t ext4 -L metadata {q}\n"
        f"tune2fs -O has_journal -u 10010 -r 4096 {q}\n"
        f"echo \"[ok] /metadata 预格式化 ext4：{size} B\"\n"
    )


def data_f2fs_qcow2(dst, virt_bytes):
    """生成已格式化的 /data 种子（f2fs，qcow2 稀疏）。

    在容器内建稀疏 raw（不进 bind mount，避免 110 GiB 走 virtiofs）→ mkfs.f2fs →
    qemu-img 转 qcow2（raw 的洞会被识别为零，转换很快）→ 拷回宿主产物路径。
    参数对齐真机：`-g android`（guest 自己的 make_f2fs 也用了该配置）+ 标签 data。
    """
    os.makedirs(WORK_DIR, exist_ok=True)
    q = shlex.quote(dst)
    _docker(
        "set -euo pipefail\n"
        f"rm -f /tmp/data.raw /tmp/data.qcow2\n"
        f"truncate -s {virt_bytes} /tmp/data.raw\n"
        "mkfs.f2fs -q -f -l data -g android /tmp/data.raw\n"
        "qemu-img convert -f raw -O qcow2 -o compat=1.1,cluster_size=65536 "
        "/tmp/data.raw /tmp/data.qcow2\n"
        f"cp /tmp/data.qcow2 {q}\n"
        "rm -f /tmp/data.raw /tmp/data.qcow2\n"
        f"echo \"[ok] /data 预格式化 f2fs：虚拟 {virt_bytes} B -> {q}\"\n"
    )