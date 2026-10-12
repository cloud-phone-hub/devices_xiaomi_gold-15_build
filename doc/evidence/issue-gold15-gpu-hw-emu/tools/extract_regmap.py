#!/usr/bin/env python3
"""从 kbase 的寄存器 LUT 生成指定 arch 的「权威寄存器表」（只读）。

原理：`mali_kbase_regmap_jm.c` 里每个 `kbase_regmap_<ver>_init()` 先调用上一代 init
（链式继承），再对 `kbdev->regmap.{flags,regs}[ENUM]` 赋值（同一 ENUM 后写覆盖先写；
`= NULL`/`= 0` 表示该寄存器在本代被作废）。因此只要按链序求值，就得到该 arch 真正会
访问的寄存器集合 —— 这正是"设备模型必须实现哪些寄存器"的权威依据。

用法：
  python3 extract_regmap.py <mali_kbase_regmap_jm.c> <arch: v9_0|v9_2|v7_2|...>
输出：Markdown 表格（ENUM / 偏移 / 位宽 / 权限）
"""
import re
import sys


def read_functions(text):
    """返回 {func_name: body_text}（大括号配平）。"""
    funcs = {}
    for m in re.finditer(r"static void (kbase_regmap_[a-z0-9_]+_init)\(struct kbase_device \*kbdev\)\s*\{",
                         text):
        name = m.group(1)
        i = m.end() - 1            # 指向 '{'
        depth = 0
        j = i
        while j < len(text):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        funcs[name] = text[i + 1:j]
    return funcs


CALL_RE = re.compile(r"(kbase_regmap_[a-z0-9_]+_init)\(kbdev\);")
FLAG_RE = re.compile(r"kbdev->regmap\.flags\[([A-Z0-9_]+)\]\s*=\s*(.*?);", re.S)
REG_RE = re.compile(r"kbdev->regmap\.regs\[([A-Z0-9_]+)\]\s*=\s*(.*?);", re.S)


def width_perm(expr):
    width = "64" if "WIDTH_64_BIT" in expr else ("32" if "WIDTH_32_BIT" in expr else "?")
    perms = ""
    if "PERM_READ" in expr:
        perms += "R"
    if "PERM_WRITE" in expr:
        perms += "W"
    return width, (perms or "?")


def resolve(funcs, name):
    """递归求值（先 callee 后自身赋值），返回 (regs, flags)。"""
    if name not in funcs:
        return {}, {}
    body = funcs[name]
    regs, flags = {}, {}
    # 1) 先按出现顺序处理继承调用
    for call in CALL_RE.finditer(body):
        sub_regs, sub_flags = resolve(funcs, call.group(1))
        regs.update(sub_regs)
        flags.update(sub_flags)
    # 2) 再处理自身赋值（按源码顺序；后写覆盖先写）
    events = []
    for m in FLAG_RE.finditer(body):
        events.append((m.start(), "flag", m.group(1), m.group(2)))
    for m in REG_RE.finditer(body):
        events.append((m.start(), "reg", m.group(1), m.group(2)))
    for _, kind, enum, expr in sorted(events):
        if kind == "flag":
            flags[enum] = expr.strip()
        else:
            e = expr.strip()
            if e == "NULL":
                regs.pop(enum, None)          # 本代作废
                flags.pop(enum, None)
            else:
                mm = re.search(r"kbdev->reg\s*\+\s*(0x[0-9a-fA-F]+)", e)
                regs[enum] = int(mm.group(1), 16) if mm else e
    return regs, flags


def main():
    path, arch = sys.argv[1], sys.argv[2]
    funcs = read_functions(open(path, encoding="utf8").read())
    regs, flags = resolve(funcs, f"kbase_regmap_{arch}_init")
    print(f"# kbase 寄存器 LUT：{arch}（权威集合，机器抽取）\n")
    print(f"- 来源：`{path}`（`kbase_regmap_{arch}_init` 及其继承链）")
    print(f"- 条目数：**{len(regs)}**（作废条目已剔除）\n")
    print("| # | 寄存器 ENUM | 偏移 | 位宽 | 权限 |")
    print("|---|---|---|---|---|")
    rows = sorted(regs.items(), key=lambda kv: (kv[1] if isinstance(kv[1], int) else 1 << 40, kv[0]))
    for idx, (enum, off) in enumerate(rows, 1):
        w, p = width_perm(flags.get(enum, ""))
        offs = f"0x{off:04X}" if isinstance(off, int) else str(off)
        print(f"| {idx} | `{enum}` | {offs} | {w} | {p} |")
    # 未被 regs 覆盖的 flags（异常，提示人工核对）
    orphan = [e for e in flags if e not in regs]
    if orphan:
        print(f"\n> ⚠️ 仅有 flags 无 regs 的条目（{len(orphan)}）：{', '.join(sorted(orphan))}")


if __name__ == "__main__":
    main()
