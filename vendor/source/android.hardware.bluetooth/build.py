#!/usr/bin/env python3
"""xiaomi gold-15（Redmi Note 13 5G, android15）—— AIDL 蓝牙 HCI HAL 设备集成层。

本模块**不自行编译/链接驱动**：codegen + 编译 + 链接整套流水线全部委托给
`hal/android15/android.hardware.bluetooth/build.py` 的
`build(out_dir, runtime_lib_dirs, *, tmp_dir, dry_run)`。gold 只做设备集成层独有的两件事：
  1. 把引擎产出的服务二进制从 `<out>/vendor/bin/hw` 拷进 /vendor staging；
  2. 落 **gold 独有配置**（本目录 vendor/：rc、vintf）并登记 SELinux。

蓝牙 HCI 是**纯传输层**（H4 over /dev/hvc5）：扫描/连接逻辑在 framework Fluoride 栈，
本 HAL 只忠实透传 HCI 包。设备节点走编译期内置默认 `/dev/hvc5`，可被属性
`vendor.ser.bt-uart` 覆盖（与 AOSP reference default 同名，换机型改线号无需重编），
故与 vibrator/wifi 不同——**不需要外置 cfg**（无机型能力清单要运行时读）。

产物（仅服务，framework 依赖一律不得链入）：
  out/dist/vendor/bin/hw/android.hardware.bluetooth-service

用法（在设备根 devices/xiaomi/gold-15 执行，前置：hal deps bluetooth 就绪）：
  python3 build/vendor/source/android.hardware.bluetooth/build.py
  python3 build/vendor/source/android.hardware.bluetooth/build.py --dry-run
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
_HAL_ENGINE = os.path.join(_HAL_ROOT, "android.hardware.bluetooth", "build.py")

sys.path.insert(0, os.path.join(_DEVICE_ROOT, "build", "scripts"))
sys.path.insert(0, os.path.join(_DEVICE_ROOT, "build", "vendor", "build"))
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
hal_bluetooth = _load_by_path("cdh_hal_bluetooth_engine", _HAL_ENGINE)

ENGINE_OUT_DIR = os.path.join(OUT_DIR, "vendor")

# ---- gold 独有配置 / SELinux ----
# rc 文件名同样**必须与底座同名**（bluetooth-service-default.rc）：底座那份也定义
# `service vendor.bluetooth-default`（指向 AOSP 参考二进制），另起文件名会让 init 报
# "ignored duplicate definition of service 'vendor.bluetooth-default'"，谁生效取决于
# rc 解析顺序（真机日志 evidence: issue-bt-hci-hal）。同名覆盖后只剩本服务定义。
RC_NAME = "bluetooth-service-default.rc"
# 落 vintf 片段的**文件名必须与底座同名**（bluetooth-service-default.xml）：lineage 底座
# 已带一份 AOSP 参考声明（同样声明 IBluetoothHci/default），若另起文件名追加，vendor
# VINTF 会出现两条 Conflicting FqInstance，servicemanager 整份 device manifest 解析失败
# （真机日志："HAL android.hardware.bluetooth has a conflict"）。同名覆盖后只剩一条声明。
VINTF_NAMES = ["bluetooth-service-default.xml"]
# 复用底包既有标准域 hal_bluetooth_default（其 exec/域转换/service 属性 plat+base 均已备齐），
# 仅授权不改域（详见 build/vendor/build/sepolicy.py 的 bluetooth_hal_cil）。
LABEL_BIN = "u:object_r:hal_bluetooth_default_exec:s0"
LABEL_CONFIG = "u:object_r:vendor_configs_file:s0"


def prepare_abi(dry_run=False):
    """运行库**目录列表**：CDH_RUNTIME 优先（os.pathsep 传多目录，单目录兼容），
    否则研磨 gold lineage super（单目录 vendor-compile-dep）。"""
    env = os.environ.get("CDH_RUNTIME")
    if env:
        return [p for p in env.split(os.pathsep) if p]
    d = device_deps.prepare_runtime_libs(dry_run)
    return [d] if isinstance(d, str) else d


def install_binaries(dry_run=False):
    """把引擎链接好的服务二进制从 <out>/vendor/bin/hw 拷进 /vendor staging。"""
    bin_subdir = hal_bluetooth.BIN_SUBDIR
    for service in hal_bluetooth.SERVICES:
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


def register_labels(baselines_path, dry_run=False):
    """向设备 baselines 清单登记本模块安装文件的 SELinux 上下文。"""
    if not baselines_path or not os.path.isfile(baselines_path):
        print(f"[err] 基线清单缺失: {baselines_path or 'None'}", file=sys.stderr)
        sys.exit(1)
    with open(baselines_path, encoding="utf-8") as f:
        doc = json.load(f)
    labels = doc.setdefault("vendor", {}).setdefault("selinux-labels", {})
    entries = {f"bin/hw/{name}": LABEL_BIN for name in hal_bluetooth.SERVICES}
    entries[f"etc/init/{RC_NAME}"] = LABEL_CONFIG
    for v in VINTF_NAMES:
        entries[f"etc/vintf/manifest/{v}"] = LABEL_CONFIG
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
    hal_bluetooth.build(ENGINE_OUT_DIR, runtime, tmp_dir=OUT_DIR, dry_run=dry_run)

    print("==> 安装服务二进制到 /vendor staging")
    install_binaries(dry_run=dry_run)

    print("==> rc / vintf")
    write_rc(os.path.join(VENDOR_STAGE_DIR, "etc", "init", RC_NAME))
    for v in VINTF_NAMES:
        write_vintf(os.path.join(VENDOR_STAGE_DIR, "etc", "vintf", "manifest", v))

    print("==> SELinux 标签登记")
    register_labels(baselines_file(), dry_run=dry_run)

    print("[ok] gold 蓝牙 HCI 集成完成（委托 hal 引擎构建 + gold 配置 + SELinux）")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="xiaomi gold-15 AIDL 蓝牙 HCI HAL 设备集成层（委托 hal 引擎构建）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不执行")
    args = ap.parse_args()
    sys.exit(build(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
