#!/usr/bin/env python3
"""解析 Android boot/vendor_boot 镜像（v3/v4），导出内核，并探测内嵌 config。"""
import json
import os
import struct
import sys
import zlib


def parse_boot(path, out_prefix):
    with open(path, "rb") as f:
        data = f.read()
    assert data[:8] == b"ANDROID!", "非 Android boot 镜像"
    kernel_size, ramdisk_size, os_version, header_size, reserved0, reserved1, \
        header_version, cmdline = struct.unpack_from("<IIIIIIII", data, 8)
    print(f"== {path}")
    print(f"  kernel_size={kernel_size} ramdisk_size={ramdisk_size} "
          f"header_version={header_version}")
    # 位于 cmdline 之后的字段（v3/v4）
    off = 8 + 4 * 9
    off += 16          # id (sha1)
    off += 512         # cmdline pad
    if header_version >= 3:
        p = off
        while data[p:p + 4] != b"\0\0\0\0" or True:
            # boot v3: board/extra cmdline 各 1024 字节，page_size 4B
            pass
        page_size, = struct.unpack_from("<I", data, off + 2048)
        print(f"  page_size={page_size}")
        off += 2048 + 4
        if header_version == 4:
            # v4: 4 个 4B 字段
            off += 16
        kernel_off = (off + page_size - 1) // page_size * page_size
    else:
        page_size, = struct.unpack_from("<I", data, 36)
        kernel_off = page_size
        print(f"  page_size={page_size}")
    kernel = data[kernel_off:kernel_off + kernel_size]
    kpath = out_prefix + "-kernel.bin"
    with open(kpath, "wb") as f:
        f.write(kernel)
    print(f"  cmdline={cmdline!r}")
    print(f"  内核导出 -> {kpath} ({len(kernel)} 字节)")
    return kernel


def find_config(kernel):
    """在内核镜像中查找 IKCFG_ST ... IKCFG_ED 包裹的 gzip config。"""
    start = kernel.find(b"IKCFG_ST")
    end = kernel.find(b"IKCFG_ED")
    if start < 0 or end < 0:
        print("  [config] 未找到 IKCFG_ST（内核未启用 IKCONFIG_PROC/EMBED）")
        return None
    blob = kernel[start + 8:end]
    try:
        cfg = zlib.decompress(blob, 16 + 15)
    except Exception as exc:  # noqa: BLE001
        print("  [config] 解压失败:", exc)
        return None
    text = cfg.decode("utf-8", "replace")
    lines = text.splitlines()
    print(f"  [config] 内嵌 config 解压成功，{len(lines)} 行")
    return lines


def grep_config(lines, patterns):
    for pat in patterns:
        hits = [l for l in lines if pat in l]
        if hits:
            print(f"  [config] {pat}: {len(hits)} 行")
            for h in hits[:12]:
                print("     ", h)


def main():
    out = sys.argv[1]
    for img in sys.argv[2:]:
        prefix = os.path.join(out, os.path.basename(img).replace(".img", ""))
        kernel = parse_boot(img, prefix)
        lines = find_config(kernel)
        if lines:
            grep_config(lines, [
                "CONFIG_MODVERSIONS", "CONFIG_MODULE_SIG", "CONFIG_VIRTIO",
                "CONFIG_DRM_VIRTIO_GPU", "CONFIG_DRM_MEDIATEK", "CONFIG_MALI",
                "CONFIG_ARM64_4K_PAGES", "CONFIG_LOCALVERSION",
                "CONFIG_IKCONFIG", "CONFIG_DRM=y", "CONFIG_DMABUF_HEAPS",
                "CONFIG_ION", "CONFIG_IOMMU", "CONFIG_MODULES=y",
            ])


if __name__ == "__main__":
    main()
