#!/usr/bin/env python3
"""xiaomi gold（Redmi Note 13 5G, android15）—— board（chipinfo）设备集成层。

本模块**不自行编译** chipinfo：整套编译委托给
`hal/android15/android.hardware.board/build.py` 的 `build(out_dir, runtime_lib_dirs, dry_run)`。
gold 只做设备集成层独有的三件事：
  1. 把引擎产出的二进制从 `<out>/vendor/bin/hw/android.hardware.board-service` 拷进 /vendor staging 的 bin/hw/；
  2. 落 **gold 独有配置**（本目录 vendor/：chipinfo_init.rc、SoC 身份 cfg）；
  3. 登记 SELinux（chipinfo_exec / vendor_configs_file）。

SoC 身份属性（ro.vendor.soc.*）在 vendor/etc/cfg/cdh_board.cfg，装进 /vendor/etc/cfg/
供 chipinfo 运行时读取（对齐 audio 外置做法，改身份值不重编二进制）。

产物（相对路径即镜像内 /vendor 路径）：
  out/dist/vendor/bin/hw/android.hardware.board-service   标签 u:object_r:chipinfo_exec:s0（0755）
  out/dist/vendor/etc/init/chipinfo_init.rc
  out/dist/vendor/etc/cfg/cdh_board.cfg

用法（在设备根 devices/xiaomi/gold-15 执行）：
  python3 build/vendor/source/android.hardware.board/build.py
  python3 build/vendor/source/android.hardware.board/build.py --dry-run
"""

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_DEVICE_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(MODULE_DIR))))
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_DEVICE_ROOT)))

_HAL_ROOT = os.path.join(_REPO_ROOT, "hal", "android15")
_HAL_ENGINE = os.path.join(_HAL_ROOT, "android.hardware.board", "build.py")

sys.path.insert(0, os.path.join(_DEVICE_ROOT, "build", "scripts"))
from buildenv import OUT_DIR, VENDOR_STAGE_DIR, baselines_file  # noqa: E402


def _load_by_path(name, path):
    """按绝对路径加载模块，规避 hal/deps.py 与设备/deps.py 同名 deps 的冲突。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


hal_board = _load_by_path("cdh_hal_board_engine", _HAL_ENGINE)

ENGINE_OUT_DIR = os.path.join(OUT_DIR, "vendor")

# ---- gold 独有配置 / SELinux ----
BINARY = hal_board.BINARY                       # "android.hardware.board-service"
BIN_SUBDIR = hal_board.BIN_SUBDIR               # "bin/hw"
RC_NAME = "chipinfo_init.rc"                       # 沿用官方 rc 名以覆盖之（官方 rc execs 旧 /vendor/bin/chipinfo）
MODULE_CFG_NAME = "cdh_board.cfg"
LABEL_BIN = "u:object_r:chipinfo_exec:s0"
LABEL_CONFIG = "u:object_r:vendor_configs_file:s0"


def install_binary(dry_run=False):
    """把引擎编译好的 board-service 从 <out>/vendor/bin/hw 拷进 /vendor staging 的 bin/hw/。"""
    src = os.path.join(ENGINE_OUT_DIR, BIN_SUBDIR, BINARY)
    dst = os.path.join(VENDOR_STAGE_DIR, BIN_SUBDIR, BINARY)
    if dry_run:
        print(f"[..] (dry) cp {src} -> {dst}")
        return
    if not os.path.isfile(src):
        print(f"[err] 引擎未产出二进制: {src}", file=sys.stderr)
        sys.exit(1)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    os.chmod(dst, 0o755)
    print(f"    写 {os.path.relpath(dst, VENDOR_STAGE_DIR)}")


def _copy_asset(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(src, encoding="utf-8") as f:
        content = f.read()
    with open(dst, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"    写 {os.path.relpath(dst, VENDOR_STAGE_DIR)}")


def write_rc(dst):
    _copy_asset(os.path.join(MODULE_DIR, "vendor", "etc", "init", RC_NAME), dst)


def write_module_cfg(dst):
    """拷贝 SoC 身份属性到 staging /vendor/etc/cfg/（供 chipinfo 运行时读取）。"""
    src = os.path.join(MODULE_DIR, "vendor", "etc", "cfg", MODULE_CFG_NAME)
    if not os.path.isfile(src):
        print(f"[err] 缺身份配置 {MODULE_CFG_NAME}（chipinfo 无值可写）", file=sys.stderr)
        sys.exit(1)
    _copy_asset(src, dst)


def register_labels(baselines_path, dry_run=False):
    """向设备 baselines 清单登记本模块安装文件的 SELinux 上下文。"""
    if not baselines_path or not os.path.isfile(baselines_path):
        print(f"[err] 基线清单缺失: {baselines_path or 'None'}", file=sys.stderr)
        sys.exit(1)
    with open(baselines_path, encoding="utf-8") as f:
        doc = json.load(f)
    labels = doc.setdefault("vendor", {}).setdefault("selinux-labels", {})
    entries = {
        f"{BIN_SUBDIR}/{BINARY}": LABEL_BIN,
        f"etc/init/{RC_NAME}": LABEL_CONFIG,
        f"etc/cfg/{MODULE_CFG_NAME}": LABEL_CONFIG,
    }
    missing = {k: v for k, v in entries.items() if labels.get(k) != v}
    if not missing:
        print("[..] baselines 标签已齐，跳过")
        return
    labels.update(missing)
    if dry_run:
        print(f"[..] (dry) baselines 将登记标签 {len(missing)} 条")
        return
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(baselines_path),
                               prefix=".baselines.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, baselines_path)
    except BaseException:
        os.unlink(tmp)
        raise
    print(f"[ok] baselines 登记标签 {len(missing)} 条")


def build(dry_run=False):
    # board 纯 C：无 AIDL codegen、无运行库，引擎 build 忽略 runtime。
    print(f"==> 委托 hal 引擎：编译 chipinfo（out={ENGINE_OUT_DIR}）")
    hal_board.build(ENGINE_OUT_DIR, dry_run=dry_run)

    print("==> 安装二进制到 /vendor staging")
    install_binary(dry_run=dry_run)

    print("==> rc / cfg")
    write_rc(os.path.join(VENDOR_STAGE_DIR, "etc", "init", RC_NAME))
    write_module_cfg(os.path.join(VENDOR_STAGE_DIR, "etc", "cfg", MODULE_CFG_NAME))

    print("==> SELinux 标签登记")
    register_labels(baselines_file(), dry_run=dry_run)

    print("[ok] gold board 集成完成（委托 hal 引擎构建 + gold 配置）")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="xiaomi gold board（chipinfo）设备集成层（委托 hal 引擎构建）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不执行")
    args = ap.parse_args()
    sys.exit(build(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
