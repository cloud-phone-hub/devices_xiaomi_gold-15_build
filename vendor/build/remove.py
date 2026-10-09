#!/usr/bin/env python3
"""按 build/vendor/remove.txt 删除云机 vendor 内不需要的条目（容器内 root 执行）。

官方 vendor 是 EROFS 基座，rc 这类"定义式"文件无法靠覆盖关闭（同名 service 重复定义
会报错），只能删除；故与 inject.py（新增/覆盖）互补，提供删除面。

执行顺序（见 vendor_builder 拼装）：cp -a 落基座 → 本模块删除 → inject 注入。
条目在官方 vendor 内不存在时只告警不中断（ROM 版本漂移时据此发现清单过时）。

对外：
  paths()      删除清单（相对 /vendor 的路径，去注释/空行）
  script()     容器脚本片段（由编排层拼装）
"""

import os
import shlex

from buildenv import VENDOR_PATCH_DIR
from base_fs import C_DST_DIR

# 删除清单：放在注入树同级（build/vendor/remove.txt）
REMOVE_FILE = os.path.join(os.path.dirname(VENDOR_PATCH_DIR), "remove.txt")


def paths():
    """读删除清单：每行一个 /vendor 内路径；忽略注释与空行，统一去掉前导 '/'。"""
    if not os.path.isfile(REMOVE_FILE):
        return []
    out = []
    with open(REMOVE_FILE, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            out.append(line.lstrip("/"))
    return out


def script():
    """容器内删除脚本：逐条 rm -rf，并打印结果（缺失只告警）。"""
    lines = []
    for rel in paths():
        q = shlex.quote(rel)
        lines.append(
            f'if [ -e {C_DST_DIR}/{q} ]; then rm -rf {C_DST_DIR}/{q}; '
            f'echo "[ok] 移除 /vendor/{rel}"; '
            f'else echo "[warn] remove.txt 条目在官方 vendor 内不存在: /vendor/{rel}"; fi'
        )
    return "\n".join(lines) + ("\n" if lines else "")