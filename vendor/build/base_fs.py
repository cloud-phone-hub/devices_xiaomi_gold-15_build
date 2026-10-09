#!/usr/bin/env python3
"""官方 vendor（EROFS）→ ext4 基座转换（容器内 root 双挂载）。

官方 vendor_a.img 是 EROFS（无原地编辑工具，加文件必须重排 inode），而注入按 ext4
走；故在容器内以 root + `--privileged` 双挂载——erofs 源只读挂、新建 ext4 loop 挂
——再 `cp -a` 把内容（含 uid/gid/mode 与 security.selinux）整体搬进 ext4。
宿主 macOS 与容器 overlay 都保不住 security.selinux，双挂载是唯一可行路径。

本模块只负责「基座转换」这一件事；注入见 inject.py，流程编排见 vendor_builder.py。

对外：
  erofs_tool()        定位 dump.erofs
  content_bytes(img)  erofs 未压缩内容总字节（ext4 尺寸基准）
  mirror_size(n)      按 1 MiB 对齐的镜像尺寸（含注入预留）
  mounts(src_img, out_dir)   容器挂载表
  prepare(size) / finish(out_dir, out_name)   容器脚本片段（由编排层拼装）
"""

import math
import os
import re
import shutil
import subprocess
import sys

# ext4 镜像尺寸 = erofs 未压缩内容 + 注入预留，按 1 MiB 对齐
MIRROR_SLACK = 256 * 1024 * 1024
ALIGN = 1024 * 1024

# 容器内路径（宿主镜像文件只读挂到 /src，产物目录挂到 /out）
C_SRC_IMG = "/src/vendor_a.img"
C_SRC_DIR = "/mnt/src"      # erofs 挂载点
C_DST_DIR = "/mnt/dst"      # ext4 挂载点
C_WORK = "/work.img"        # ext4 镜像（放容器内，避免在 virtiofs 上 loop）
C_OUT_DIR = "/out"
C_WORKDIR = "/workdir"      # 宿主 TMP_DIR/vendor（注入脚本 / 标签 / props 改写脚本）

PREPARE = """mkdir -p {src_dir} {dst_dir}
mount -t erofs -o ro {src_img} {src_dir}
truncate -s {size} {work}
mkfs.ext4 -q -F -m 0 -L vendor {work}
mount -o loop {work} {dst_dir}
echo "[..] 复制官方 vendor 内容 -> ext4（保 uid/gid/mode/xattr）"
cp -a {src_dir}/. {dst_dir}/
"""

FINISH = """sync
umount {dst_dir}
umount {src_dir}
rc=0; e2fsck -fy {work} >/dev/null || rc=$?
[ "$rc" -le 1 ]
echo "[ok] e2fsck 通过"
cp {work} {out_dir}/{out_name}
"""


def erofs_tool():
    """定位 dump.erofs；EROFSPROGS_DUMP > brew > PATH，缺失即报错。"""
    for cand in (os.environ.get("EROFSPROGS_DUMP"),
                 "/opt/homebrew/bin/dump.erofs", shutil.which("dump.erofs")):
        if cand and os.path.isfile(cand):
            return cand
    print("[err] 缺少 dump.erofs（brew install erofs-utils）")
    sys.exit(1)


def content_bytes(img):
    """erofs 镜像内「未压缩内容总字节」（= ext4 需要的大小基准）。

    取 dump.erofs -S 的 "Filesystem total original file size"（压缩前合计），
    而不是镜像自身大小（那是压缩后的）。
    """
    res = subprocess.run([erofs_tool(), "-S", img], capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[err] dump.erofs 解析失败: {img}\n{res.stderr}")
        sys.exit(1)
    m = re.search(r"Filesystem total original file size:\s+(\d+)", res.stdout)
    if not m:
        print("[err] dump.erofs 输出缺 total original file size")
        sys.exit(1)
    return int(m.group(1))


def mirror_size(bytes_):
    return int(math.ceil((bytes_ + MIRROR_SLACK) / ALIGN) * ALIGN)


def mounts(src_img, out_dir):
    """容器挂载表：官方镜像文件只读 + 产物目录可写。"""
    return [(src_img, C_SRC_IMG, True), (out_dir, C_OUT_DIR, False)]


def prepare(size):
    return PREPARE.format(src_dir=C_SRC_DIR, src_img=C_SRC_IMG,
                          dst_dir=C_DST_DIR, work=C_WORK, size=size)


def finish(out_name):
    return FINISH.format(dst_dir=C_DST_DIR, src_dir=C_SRC_DIR, work=C_WORK,
                         out_dir=C_OUT_DIR, out_name=out_name)