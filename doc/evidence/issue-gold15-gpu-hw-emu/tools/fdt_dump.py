#!/usr/bin/env python3
"""极简 FDT(dtb/dtbo) 解析：打印节点树，可按 compatible/名称过滤。

用法：python3 fdt_dump.py <dtb-or-dtbo> [过滤关键字...]
"""
import struct
import sys


def parse_fdt(b, base=0):
    magic, totalsize, off_struct, off_strings, off_rsvmap, version, \
        last_comp, boot_cpu, size_strings, size_struct = struct.unpack_from(">10I", b, base)
    assert magic == 0xD00DFEED, hex(magic)
    strings = b[base + off_strings:base + off_strings + size_strings]
    struct_block = b[base + off_struct:base + off_struct + size_struct]
    p = 0
    stack = []
    nodes = []

    def s(off):
        e = strings.find(b"\0", off)
        return strings[off:e].decode("utf8", "replace")

    while p < len(struct_block):
        tok, = struct.unpack_from(">I", struct_block, p)
        p += 4
        if tok == 1:  # FDT_BEGIN_NODE
            e = struct_block.find(b"\0", p)
            name = struct_block[p:e].decode("utf8", "replace")
            name = name.split("@")[0]
            p = e + 1
            if (p % 4):
                p += 4 - (p % 4)
            stack.append({"name": name, "props": {}, "path": "/" + "/".join(
                x["name"] for x in stack + [{"name": name}])})
            nodes.append(stack[-1])
        elif tok == 2:  # FDT_END_NODE
            stack.pop()
        elif tok == 3:  # FDT_PROP
            plen, nameoff = struct.unpack_from(">II", struct_block, p)
            p += 8
            val = struct_block[p:p + plen]
            p += plen
            if p % 4:
                p += 4 - (p % 4)
            if not stack:
                continue
            stack[-1]["props"][s(nameoff)] = val
        elif tok == 4:  # FDT_NOP
            continue
        elif tok == 9:  # FDT_END
            break
        else:
            raise ValueError(f"未知 token {tok} @ {p}")
    return nodes


def fmt_prop(name, val):
    if name == "compatible" or name in ("status", "model", "clock-names",
                                        "interrupt-names", "reg-names",
                                        "power-domain-names", "iommus",
                                        "operating-points-v2", "nvmem-cell-names"):
        return b" ".join(x for x in val.split(b"\0") if x).decode("utf8", "replace")
    if name in ("reg",) or name.endswith("-supply") or name in ("interrupts",):
        if len(val) % 4 == 0 and name == "reg":
            words = struct.unpack(f">{len(val)//4}I", val)
            return " ".join(f"0x{w:x}" for w in words)
        if name == "interrupts" and len(val) % 4 == 0:
            words = struct.unpack(f">{len(val)//4}I", val)
            return " ".join(str(w) for w in words)
    if len(val) == 4:
        return f"<0x{struct.unpack('>I', val)[0]:x}>"
    if len(val) == 8:
        a, b2 = struct.unpack(">II", val)
        return f"<0x{a:x} 0x{b2:x}>"
    if len(val) % 4 == 0 and len(val) <= 64:
        words = struct.unpack(f">{len(val)//4}I", val)
        return "<" + " ".join(f"0x{w:x}" for w in words) + ">"
    if all(32 <= c < 127 or c == 0 for c in val):
        return val.rstrip(b"\0").decode("utf8", "replace").replace("\0", " | ")
    return f"<{len(val)} bytes>"


def main():
    path = sys.argv[1]
    keys = [k.lower() for k in sys.argv[2:]]
    b = open(path, "rb").read()
    # dtbo: dt_table_header -> entries
    blobs = []
    if b[:4] == b"\xd0\x0d\xfe\xed" or struct.unpack_from(">I", b, 0)[0] == 0xD00DFEED:
        blobs = [(0, len(b), "root")]
    else:
        magic, = struct.unpack_from(">I", b, 0)
        if magic == 0xD7B7AB1E:  # dt_table_header magic
            total, hdr_size, dt_entry_size, dt_entry_count, dt_entries_offset, \
                page_size, version = struct.unpack_from(">7I", b, 4)
            print(f"[dtbo] total={total} header={hdr_size} entries={dt_entry_count} "
                  f"entry_size={dt_entry_size} page={page_size} version={version}")
            for i in range(dt_entry_count):
                off = dt_entries_offset + i * dt_entry_size
                sz, off_dt, id_, rev = struct.unpack_from(">4I", b, off)
                blobs.append((off_dt, sz, f"entry{i}(id={id_},rev={rev})"))
        else:
            print("未知镜像格式", hex(magic))
            return
    for off, sz, label in blobs:
        try:
            nodes = parse_fdt(b, off)
        except Exception as exc:  # noqa: BLE001
            print(f"--- {label}: 解析失败 {exc}")
            continue
        hits = []
        for n in nodes:
            blob = " ".join(
                fmt_prop(k, v) if isinstance(v, bytes) else str(v)
                for k, v in n["props"].items())
            text = (n["path"] + " " + blob).lower()
            if not keys or any(k in text for k in keys):
                hits.append((n, blob))
        if hits:
            print(f"--- {label}: {len(hits)} 命中节点")
            for n, blob in hits:
                print(f"  {n['path']}  [{blob}]")
                for k in sorted(n["props"]):
                    print(f"      {k} = {fmt_prop(k, n['props'][k])}")


if __name__ == "__main__":
    main()
