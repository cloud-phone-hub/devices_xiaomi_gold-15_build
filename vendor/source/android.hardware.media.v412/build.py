#!/usr/bin/env python3
"""xiaomi note13（Redmi Note 13 5G, android15）—— V4L2 Codec2 真实硬件编解码 store 设备集成层。

代码逻辑（codegen + 编译 + 链接整套 v4l2_codec2）委托
`hal/android15/android.hardware.media.v412/build.py` 的 `build(out_dir, abi_dirs, dry_run)`。
note13 只做设备集成层独有的事：
  1. **从 lineage super 研磨**链接期/运行期 ABI 库（debugfs dump system_a.img +
     swcodec apex_payload.img），得 [system_lib64, apex_lib64] 两目录传给引擎；
  2. 把引擎产出的两服务二进制 + libc2plugin_store.so 拷进 /vendor staging；
  3. **落运行期 VENDOR_LIBS** 到 /vendor/lib64（vendor linker namespace 看不到非 LLNDK
     的 system 库与 APEX 私有库，必须随模块落盘）；
  4. 落 rc / vintf manifest / seccomp_policy；**合并** media_codecs_c2.xml（追加
     c2.v4l2.* 组、保留 MTK 全集，绝不覆盖）；
  5. 登记 SELinux baselines。

服务注册 android.hardware.media.c2.IComponentStore/default（note13 的 MTK 假 store 已在
android.hardware.media 让位至 default1，见其 cdh_media.cfg/manifest，二者实例不冲突）。
vintf manifest 仅本模块注入 IComponentStore/default（android.hardware.media 那份已改
default1，不重复声明 default）。

产物（staging，相对路径即镜像内 /vendor）：
  bin/hw/android.hardware.media.c2@1.2-service-v4l2-64（+ legacy c2-service-v4l2-64）
  lib64/libc2plugin_store.so
  lib64/<VENDOR_LIBS 运行期依赖库>
  etc/init/android.hardware.media.c2@1.2-service-v4l2-64.rc
  etc/seccomp_policy/android.hardware.media.c2-extended-seccomp_policy
  etc/vintf/manifest/android.hardware.media.c2@1.2-service-v4l2.xml
  etc/media_codecs_c2.xml（合并）

用法（在设备根 devices/xiaomi/note13-5G-15 执行；前置：hal deps media-v412 就绪）：
  python3 build/vendor/source/android.hardware.media.v412/build.py
  python3 build/vendor/source/android.hardware.media.v412/build.py --dry-run
"""

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_DEVICE_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(MODULE_DIR))))
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_DEVICE_ROOT)))

sys.path.insert(0, os.path.join(_DEVICE_ROOT, "build", "scripts"))
sys.path.insert(0, os.path.join(_DEVICE_ROOT, "build", "vendor", "build"))

from buildenv import (OUT_DIR, VENDOR_STAGE_DIR,  # noqa: E402
                      LINEAGE_SUPER_DIR, baselines_file)
from debugfs import DEBUGFS, path_exists  # noqa: E402

_HAL_ENGINE = os.path.join(_REPO_ROOT, "hal", "android15",
                           "android.hardware.media.v412", "build.py")


def _load_by_path(name, path):
    """按绝对路径加载引擎模块。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


hal_v412 = _load_by_path("cdh_hal_media_v412_engine", _HAL_ENGINE)

ENGINE_OUT_DIR = os.path.join(OUT_DIR, "vendor")

# ---- ABI 研磨来源（lineage super，与设备无关引擎解耦）----
SYSTEM_A_IMG = os.path.join(LINEAGE_SUPER_DIR, "system_a.img")
SWCODEC_IMG = os.path.join(LINEAGE_SUPER_DIR, "swcodec-work", "apex_payload.img")
# ABI 研磨根：与其它 HAL 共用 out/runtime_lib_dir/ 根（同 build/vendor/build/deps.py
# 的 RUNTIME_LIB_ROOT），但保留 media-v412 自己的命名空间子目录（v412 需 ~60 库 +
# apex，比通用 system-/vendor-compile-deps 的固定清单大得多）。
RUNTIME_LIB_ROOT = os.path.join(OUT_DIR, "runtime_lib_dir")
ABI_ROOT = os.path.join(RUNTIME_LIB_ROOT, "media-v412")
ABI_SYS = os.path.join(ABI_ROOT, "system", "lib64")
ABI_APEX = os.path.join(ABI_ROOT, "apex", "media-swcodec", "lib64")

# ---- 链接期研磨清单（system 侧 18 + apex 侧 9；引擎按全路径 -l 消费其子集）----
SYSTEM_LIBS = [
    "android.hardware.graphics.common@1.0.so",
    "android.hardware.media.c2@1.0.so",
    "android.hardware.media.c2@1.1.so",
    "android.hardware.media.c2@1.2.so",
    "libavservices_minijail.so",
    "libbase.so",
    "libbinder_ndk.so",
    "libc++.so",
    "libchrome.so",
    "libcutils.so",
    "libhidlbase.so",
    "liblog.so",
    "libnativewindow.so",
    "libsfplugin_ccodec_utils.so",
    "libstagefright_bufferqueue_helper.so",
    "libstagefright_foundation.so",
    "libui.so",
    "libutils.so",
]
APEX_LIBS = [
    "libcodec2_vndk.so",
    "libcodec2_hidl@1.0.so",
    "libcodec2_hidl@1.1.so",
    "libcodec2_hidl@1.2.so",
    "libcodec2_soft_common.so",
    "libcodec2_aidl.so",
    "android.hardware.media.c2-V1-ndk.so",
    "android.hardware.media.bufferpool2-V2-ndk.so",
    "android.hardware.common-V2-ndk.so",
]

# ---- 需随模块落盘到 /vendor/lib64 的运行期库（{库名: 研磨来源}）----
APEX_VENDOR_LIBS = [
    "libcodec2_vndk.so",   # 链接期取自 abi_apex
    "libcodec2_aidl.so",   # /system/lib64 无同名库，只能来自 APEX
]
SYSTEM_VENDOR_LIBS = [
    "android.hardware.media.c2-V1-ndk.so",
    "android.hardware.media.bufferpool2-V2-ndk.so",
    "android.hardware.common-V2-ndk.so",
    "android.hardware.common.fmq-V1-ndk.so",
    "android.hardware.graphics.common-V5-ndk.so",
    "android.hardware.graphics.allocator-V2-ndk.so",
    "libbinder.so",
    "libbase.so",
    "libcutils.so",
    "libutils.so",
    "libui.so",
    "libhidlbase.so",
    "libc++.so",
    "libfmq.so",
    "libion.so",
    "libcodec2.so",
    "libcodec2_hal_common.so",
    "libchrome.so",
    "libevent.so",
    "libdmabufheap.so",
    "libgralloctypes.so",
    "libaconfig_storage_read_api_cc.so",
    "server_configurable_flags.so",
    "libsfplugin_ccodec_utils.so",
    "libstagefright_foundation.so",
    "libstagefright_bufferpool@2.0.1.so",
    "libstagefright_aidl_bufferpool2.so",
    "android.hardware.graphics.allocator@2.0.so",
    "android.hardware.graphics.allocator@3.0.so",
    "android.hardware.graphics.allocator@4.0.so",
    "android.hardware.graphics.bufferqueue@2.0.so",
    "android.hardware.graphics.common@1.0.so",
    "android.hardware.graphics.common@1.1.so",
    "android.hardware.graphics.common@1.2.so",
    "android.hardware.graphics.mapper@2.0.so",
    "android.hardware.graphics.mapper@2.1.so",
    "android.hardware.graphics.mapper@3.0.so",
    "android.hardware.graphics.mapper@4.0.so",
    "android.hardware.media.bufferpool@2.0.so",
]
VENDOR_LIBS = {**{n: "apex" for n in APEX_VENDOR_LIBS},
               **{n: "system" for n in SYSTEM_VENDOR_LIBS}}
# system 侧研磨清单：链接用 SYSTEM_LIBS + 需落盘的非 LLNDK system 库
SYSTEM_DUMP_LIBS = sorted(set(SYSTEM_LIBS) | set(SYSTEM_VENDOR_LIBS))

# ---- 产物 / 资产名（读引擎常量，保持单一来源）----
SERVICE_BINARY = hal_v412.SERVICE_BINARY
SERVICE_BINARY_LEGACY = hal_v412.SERVICE_BINARY_LEGACY
PLUGIN_SONAME = hal_v412.PLUGIN_SONAME
BIN_SUBDIR = hal_v412.BIN_SUBDIR          # bin/hw
LIB_SUBDIR = hal_v412.LIB_SUBDIR          # lib64
RC_NAME = "android.hardware.media.c2@1.2-service-v4l2-64.rc"
SECCOMP_NAME = "android.hardware.media.c2-extended-seccomp_policy"
VINTF_NAME = "android.hardware.media.c2@1.2-service-v4l2.xml"

# ---- SELinux 标签 ----
LABEL_BIN = "u:object_r:hal_codec2_default_exec:s0"
LABEL_CONFIG = "u:object_r:vendor_configs_file:s0"
LABEL_LIB = "u:object_r:same_process_hal_file:s0"


def _dump_img_into(img, img_path, dst, dry_run=False):
    """debugfs dump img 内 <img_path> -> dst（幂等）。"""
    if os.path.isfile(dst) and os.path.getsize(dst) > 0:
        return
    if dry_run:
        print(f"[..] (dry) dump {img_path} <- {os.path.basename(img)}")
        return
    if not os.path.isfile(img):
        print(f"[err] 依赖研磨: 缺镜像 {img}", file=sys.stderr)
        sys.exit(1)
    if not path_exists(img, img_path):
        print(f"[err] 依赖研磨: {img} 内找不到 {img_path}", file=sys.stderr)
        sys.exit(1)
    res = subprocess.run([DEBUGFS, "-R", f"dump -p {img_path} {dst}", img],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0 or not os.path.isfile(dst) or os.path.getsize(dst) == 0:
        print(f"[err] 依赖研磨: dump {img_path} 失败", file=sys.stderr)
        sys.exit(1)
    print(f"[ok] 依赖研磨: {os.path.basename(img_path)} <- {os.path.basename(img)}")


def prepare_abi(dry_run=False):
    """研磨链接期 ABI 库到 out/runtime_lib_dir/media-v412/，返回两目录。"""
    os.makedirs(ABI_SYS, exist_ok=True)
    os.makedirs(ABI_APEX, exist_ok=True)
    for name in SYSTEM_DUMP_LIBS:
        _dump_img_into(SYSTEM_A_IMG, f"/system/lib64/{name}",
                       os.path.join(ABI_SYS, name), dry_run=dry_run)
    for name in APEX_LIBS:
        _dump_img_into(SWCODEC_IMG, f"/lib64/{name}",
                       os.path.join(ABI_APEX, name), dry_run=dry_run)
    return [ABI_SYS, ABI_APEX]


def install_engine(dry_run=False):
    """把引擎产出的两二进制 + plugin.so 从 <out>/vendor 拷进 /vendor staging。"""
    for name in (SERVICE_BINARY, SERVICE_BINARY_LEGACY):
        src = os.path.join(ENGINE_OUT_DIR, BIN_SUBDIR, name)
        dst = os.path.join(VENDOR_STAGE_DIR, BIN_SUBDIR, name)
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
    src = os.path.join(ENGINE_OUT_DIR, LIB_SUBDIR, PLUGIN_SONAME)
    dst = os.path.join(VENDOR_STAGE_DIR, LIB_SUBDIR, PLUGIN_SONAME)
    if dry_run:
        print(f"[..] (dry) cp {src} -> {dst}")
    else:
        if not os.path.isfile(src):
            print(f"[err] 引擎未产出共享库: {src}", file=sys.stderr)
            sys.exit(1)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        os.chmod(dst, 0o755)
        print(f"    写 {os.path.relpath(dst, VENDOR_STAGE_DIR)}")


def land_vendor_libs(dry_run=False):
    """把运行期 VENDOR_LIBS 从研磨目录拷进 staging /vendor/lib64。"""
    for lib, origin in VENDOR_LIBS.items():
        src_dir = ABI_SYS if origin == "system" else ABI_APEX
        src = os.path.join(src_dir, lib)
        dst = os.path.join(VENDOR_STAGE_DIR, LIB_SUBDIR, lib)
        if dry_run:
            print(f"[..] (dry) cp lib64/{lib} ({origin})")
            continue
        if not os.path.isfile(src):
            print(f"[err] 运行库未研磨到: {src}", file=sys.stderr)
            sys.exit(1)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        os.chmod(dst, 0o755)
        print(f"    写 {os.path.relpath(dst, VENDOR_STAGE_DIR)}")


def write_asset(src_rel, dst, dry_run=False):
    src = os.path.join(MODULE_DIR, src_rel)
    if dry_run:
        print(f"[..] (dry) 写 {os.path.relpath(dst, VENDOR_STAGE_DIR)}")
        return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(src, encoding="utf-8") as f:
        content = f.read()
    with open(dst, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"    写 {os.path.relpath(dst, VENDOR_STAGE_DIR)}")


def merge_media_codecs_c2(stage_etc, dry_run=False):
    """media_codecs_c2.xml 与 android.hardware.media 同路径：合并 c2.v4l2.*。

    android.hardware.media 注入真机 MTK 全集；本模块追加 c2.v4l2.* 组件定义，
    保留 MTK 条目（覆盖会摧毁已对齐的 MediaCodecList）。目标不存在时直接写入。
    """
    def v4l2_fragment():
        return (
            '    <Decoders>\n'
            '        <MediaCodec name="c2.v4l2.avc.decoder" type="video/avc">\n'
            '            <Feature name="adaptive-playback" />\n'
            '        </MediaCodec>\n'
            '        <MediaCodec name="c2.v4l2.hevc.decoder" type="video/hevc">\n'
            '            <Feature name="adaptive-playback" />\n'
            '        </MediaCodec>\n'
            '    </Decoders>\n'
            '    <Encoders>\n'
            '        <MediaCodec name="c2.v4l2.avc.encoder" type="video/avc">\n'
            '            <Limit name="size" min="32x32" max="1920x1088" />\n'
            '            <Limit name="alignment" value="2x2" />\n'
            '            <Limit name="block-size" value="16x16" />\n'
            '            <Limit name="bitrate" range="1-40000000" />\n'
            '            <Feature name="bitrate-modes" value="VBR,CBR" />\n'
            '        </MediaCodec>\n'
            '    </Encoders>\n'
        )

    src = os.path.join(MODULE_DIR, "vendor", "etc", "media_codecs_c2.xml")
    dst = os.path.join(stage_etc, "media_codecs_c2.xml")
    if dry_run:
        print("[..] (dry) media_codecs_c2.xml 合并 c2.v4l2.*")
        return
    with open(src, encoding="utf-8") as f:
        v4l2_xml = f.read()
    if os.path.isfile(dst):
        with open(dst, encoding="utf-8") as f:
            existing = f.read()
        if "c2.v4l2.avc.decoder" in existing:
            print("    media_codecs_c2.xml 已含 c2.v4l2.*，跳过合并")
            return
        if "<Encoders>" in existing and "<Decoders>" in existing:
            merged = existing.replace(
                "    <Encoders>", v4l2_fragment() + "    <Encoders>", 1)
        else:
            merged = existing.rstrip() + "\n" + v4l2_xml + "\n"
        with open(dst, "w", encoding="utf-8") as f:
            f.write(merged)
        print("    media_codecs_c2.xml 合并 c2.v4l2.*（保留 MTK 全集）")
    else:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w", encoding="utf-8") as f:
            f.write(v4l2_xml)
        print("    media_codecs_c2.xml 写入 v4l2 定义（目标不存在）")


def register_labels(baselines_path, dry_run=False):
    """向设备 baselines 清单登记本模块安装文件的 SELinux 上下文（幂等）。"""
    if not baselines_path or not os.path.isfile(baselines_path):
        print(f"[err] 基线清单缺失: {baselines_path or 'None'}", file=sys.stderr)
        sys.exit(1)
    with open(baselines_path, encoding="utf-8") as f:
        doc = json.load(f)
    labels = doc.setdefault("vendor", {}).setdefault("selinux-labels", {})
    entries = {
        f"bin/hw/{SERVICE_BINARY}": LABEL_BIN,
        f"bin/hw/{SERVICE_BINARY_LEGACY}": LABEL_BIN,
        f"lib64/{PLUGIN_SONAME}": LABEL_LIB,
        f"etc/init/{RC_NAME}": LABEL_CONFIG,
        f"etc/seccomp_policy/{SECCOMP_NAME}": LABEL_CONFIG,
        f"etc/vintf/manifest/{VINTF_NAME}": LABEL_CONFIG,
        "etc/media_codecs_c2.xml": LABEL_CONFIG,
    }
    for lib in VENDOR_LIBS:
        entries[f"lib64/{lib}"] = LABEL_LIB
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
    print(f"==> 研磨链接期 ABI 库（lineage super：{LINEAGE_SUPER_DIR}）")
    abi = prepare_abi(dry_run=dry_run)

    print(f"==> 委托 hal 引擎：codegen + 编译 + 链接（out={ENGINE_OUT_DIR}, "
          f"abi={abi}）")
    hal_v412.build(ENGINE_OUT_DIR, abi, tmp_dir=OUT_DIR, dry_run=dry_run)

    print("==> 安装引擎产物到 /vendor staging")
    install_engine(dry_run=dry_run)

    print("==> 落运行期 VENDOR_LIBS 到 /vendor/lib64")
    land_vendor_libs(dry_run=dry_run)

    print("==> rc / seccomp / vintf / media_codecs_c2.xml")
    write_asset(os.path.join("vendor", "etc", "init", RC_NAME),
                os.path.join(VENDOR_STAGE_DIR, "etc", "init", RC_NAME),
                dry_run=dry_run)
    write_asset(os.path.join("vendor", "etc", "seccomp_policy", SECCOMP_NAME),
                os.path.join(VENDOR_STAGE_DIR, "etc", "seccomp_policy",
                             SECCOMP_NAME), dry_run=dry_run)
    write_asset(os.path.join("vendor", "etc", "vintf", "manifest", VINTF_NAME),
                os.path.join(VENDOR_STAGE_DIR, "etc", "vintf", "manifest",
                             VINTF_NAME), dry_run=dry_run)
    merge_media_codecs_c2(os.path.join(VENDOR_STAGE_DIR, "etc"), dry_run=dry_run)

    print("==> SELinux 标签登记")
    register_labels(baselines_file(), dry_run=dry_run)

    print("[ok] note13 media-v412(v4l2 codec2) 集成完成（委托 hal 引擎 + note13 配置）")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="xiaomi note13 V4L2 Codec2 store 设备集成层（委托 hal 引擎构建）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不执行")
    args = ap.parse_args()
    sys.exit(build(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
