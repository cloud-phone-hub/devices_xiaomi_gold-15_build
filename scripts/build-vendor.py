#!/usr/bin/env python3
"""gold-15 vendor.img 重打包——薄入口。

具体实现见 build/vendor/build/vendor_builder.py；此处仅解析参数并委托。
与 note13 的差异：**基座是官方（厂商）vendor_a.img（EROFS）**，而非 lineage 基座。
  1. 官方 vendor_a.img（EROFS，压缩率 ~64%）双挂载提取 → ext4 镜像（保 uid/gid/mode
     与 security.selinux 标签）；
  2. 覆盖注入 build/vendor/patch/（板级 rc/fstab/idc/permissions/bin）；
  3. e2fsck -fy 重打包 -> out/dist/rom/vendor.img。

用法：
  python3 build/scripts/build-vendor.py
  python3 build/scripts/build-vendor.py --dry-run   # 只打印将执行的动作
"""

import argparse
import os
import sys

# 本文件位于 build/scripts/ 下，向上 1 级即为 build/（含 vendor/build/）
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
VENDOR_BUILD_DIR = os.path.join(os.path.dirname(_SCRIPTS_DIR), "vendor", "build")
sys.path.insert(0, VENDOR_BUILD_DIR)

from vendor_builder import build_vendor  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="gold-15 vendor.img 重打包（官方 vendor 基座）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不执行")
    args = ap.parse_args()
    sys.exit(build_vendor(dry_run=args.dry_run))


if __name__ == "__main__":
    main()