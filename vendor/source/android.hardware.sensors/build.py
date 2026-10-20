#!/usr/bin/env python3
"""xiaomi gold（Redmi Note 13 5G, android15）—— AIDL sensors HAL 设备集成层。

本模块**不自行编译/链接驱动**：codegen + 编译 + 链接整套流水线全部委托给
`hal/android15/android.hardware.sensors/build.py` 的
`build(out_dir, runtime_lib_dirs, dry_run)`。gold 只做设备集成层独有的两件事：
  1. 把引擎产出的服务二进制从 `<out>/vendor/bin/hw` 拷进 /vendor staging；
  2. 落 **gold 独有配置**（本目录 vendor/：rc、vintf、传感器清单 cfg）并登记 SELinux。

机型传感器清单（handle/name/vendor/type/量程/延迟/FIFO/wakeUp/reportingMode）
在 vendor/etc/cfg/cdh_sensors.cfg，装进 /vendor/etc/cfg/ 供驱动运行时读取
（对齐 audio 外置做法，改清单不重编驱动）。

产物（仅服务，framework 依赖一律不得链入）：
  out/dist/vendor/bin/hw/android.hardware.sensors-service.example

用法（在设备根 devices/xiaomi/gold-15 执行，前置：hal deps 就绪）：
  python3 build/vendor/source/android.hardware.sensors/build.py
  python3 build/vendor/source/android.hardware.sensors/build.py --dry-run
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
_HAL_ENGINE = os.path.join(_HAL_ROOT, "android.hardware.sensors", "build.py")

sys.path.insert(0, os.path.join(_DEVICE_ROOT, "build", "scripts"))
from buildenv import OUT_DIR, VENDOR_STAGE_DIR, baselines_file  # noqa: E402


def _load_by_path(name, path):
    """按绝对路径加载模块，规避 hal/deps.py 与设备/deps.py 同名 deps 的冲突。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


device_deps = _load_by_path(
    "cdh_device_deps", os.path.join(_DEVICE_ROOT, "build", "vendor", "build", "deps.py"))
hal_sensors = _load_by_path("cdh_hal_sensors_engine", _HAL_ENGINE)

ENGINE_OUT_DIR = os.path.join(OUT_DIR, "vendor")

# ---- gold 独有配置 / SELinux ----
RC_NAME = "android.hardware.sensors-service.rc"
VINTF_NAMES = ["android.hardware.sensors-service.xml"]
MODULE_CFG_NAME = "cdh_sensors.cfg"
LABEL_BIN = "u:object_r:hal_sensors_default_exec:s0"
LABEL_CONFIG = "u:object_r:vendor_configs_file:s0"


def prepare_abi(dry_run=False):
    """运行库**目录列表**：CDH_RUNTIME 优先（os.pathsep 传多目录，单目录兼容），
    否则研磨 gold 官方镜像（vendor-/system-compile-deps 两个 lib64）。"""
    env = os.environ.get("CDH_RUNTIME")
    if env:
        return [p for p in env.split(os.pathsep) if p]
    if dry_run and not os.path.isdir(device_deps.VENDOR_COMPILE_DEP_DIR):
        return [os.path.join("<out>", "runtime_lib_dir", "vendor-compile-deps", "lib64"),
                os.path.join("<out>", "runtime_lib_dir", "system-compile-deps", "lib64")]
    return device_deps.prepare_runtime_libs(dry_run)


def install_binaries(dry_run=False):
    """把引擎链接好的服务二进制从 <out>/vendor/bin/hw 拷进 /vendor staging。"""
    bin_subdir = hal_sensors.BIN_SUBDIR
    for service in hal_sensors.SERVICES:
        src = os.path.join(ENGINE_OUT_DIR, bin_subdir, service)
        dst = os.path.join(VENDOR_STAGE_DIR, bin_subdir, service)
        if dry_run:
            print(f"[..] (dry) cp {src} -> {dst}")
            continue
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


def write_vintf(dst):
    _copy_asset(os.path.join(MODULE_DIR, "vendor", "etc", "vintf", "manifest",
                             os.path.basename(dst)), dst)


def write_module_cfg(dst):
    """拷贝传感器清单到 staging /vendor/etc/cfg/（供 HAL 运行时读取）。"""
    src = os.path.join(MODULE_DIR, "vendor", "etc", "cfg", MODULE_CFG_NAME)
    if not os.path.isfile(src):
        print(f"[warn] 缺清单 {MODULE_CFG_NAME}，跳过（驱动回退保守默认）", file=sys.stderr)
        return
    _copy_asset(src, dst)


def register_labels(baselines_path, dry_run=False):
    """向设备 baselines 清单登记本模块安装文件的 SELinux 上下文。"""
    if not baselines_path or not os.path.isfile(baselines_path):
        print(f"[err] 基线清单缺失: {baselines_path or 'None'}", file=sys.stderr)
        sys.exit(1)
    with open(baselines_path, encoding="utf-8") as f:
        doc = json.load(f)
    labels = doc.setdefault("vendor", {}).setdefault("selinux-labels", {})
    entries = {f"bin/hw/{name}": LABEL_BIN for name in hal_sensors.SERVICES}
    entries[f"etc/init/{RC_NAME}"] = LABEL_CONFIG
    for v in VINTF_NAMES:
        entries[f"etc/vintf/manifest/{v}"] = LABEL_CONFIG
    entries[f"etc/cfg/{MODULE_CFG_NAME}"] = LABEL_CONFIG
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
    runtime = prepare_abi(dry_run)

    print(f"==> 委托 hal 引擎：codegen + 编译 + 链接"
          f"（out={ENGINE_OUT_DIR}, runtime={runtime}）")
    hal_sensors.build(ENGINE_OUT_DIR, runtime, dry_run=dry_run)

    print("==> 安装服务二进制到 /vendor staging")
    install_binaries(dry_run=dry_run)

    print("==> rc / vintf / cfg")
    write_rc(os.path.join(VENDOR_STAGE_DIR, "etc", "init", RC_NAME))
    for v in VINTF_NAMES:
        write_vintf(os.path.join(VENDOR_STAGE_DIR, "etc", "vintf", "manifest", v))
    write_module_cfg(os.path.join(VENDOR_STAGE_DIR, "etc", "cfg", MODULE_CFG_NAME))

    print("==> SELinux 标签登记")
    register_labels(baselines_file(), dry_run=dry_run)

    print("[ok] gold sensors 集成完成（委托 hal 引擎构建 + gold 配置）")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="xiaomi gold AIDL sensors HAL 设备集成层（委托 hal 引擎构建）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不执行")
    args = ap.parse_args()
    sys.exit(build(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
