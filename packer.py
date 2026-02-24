#!/usr/bin/env python3

import argparse
import json
import struct
from pathlib import Path

# 静态定义结构
ENTRY_STRUCT = struct.Struct("<9sII")  # name(9), size(u32), offset(u32)
GROUP_STRUCT = struct.Struct("<4sII")  # ext(4), count(u32), table_offset(u32)


def _read_cstr(raw: bytes) -> str:
    """读取以 \0 结尾的字节串，严格解码。"""
    content = raw.split(b"\x00", 1)[0]
    try:
        return content.decode("ascii")
    except UnicodeDecodeError:
        raise ValueError(f"编码错误：无法将字节流 {content.hex()} 解码为 ASCII，请检查文件名或文件格式。")


def _decode_scr(data: bytes) -> bytes:
    """对应游戏读取后执行的 ROR 2。"""
    out = bytearray(data)
    for i, b in enumerate(out):
        out[i] = ((b >> 2) | ((b & 0x03) << 6)) & 0xFF
    return bytes(out)


def _encode_scr(data: bytes) -> bytes:
    """_decode_scr 的逆运算（ROL 2）。"""
    out = bytearray(data)
    for i, b in enumerate(out):
        out[i] = (((b << 2) & 0xFF) | (b >> 6)) & 0xFF
    return bytes(out)


def _iter_input_files(input_dir: Path):
    for p in sorted(input_dir.rglob("*")):
        if not p.is_file():
            continue
        # 避免把解包产生的元数据再次打进包里
        if p.name == "__rio_meta__.json":
            continue
        rel = p.relative_to(input_dir)
        if len(rel.parts) != 1:
            raise ValueError(f"RIO.ARC 仅支持平铺文件名，检测到子目录结构: {rel}")
        yield p


def unpack(input_path: Path, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)

    with input_path.open("rb") as f:
        raw_header = f.read(4)
        if len(raw_header) != 4:
            raise EOFError("读取 Header 失败：文件过小或已损坏")

        group_count = struct.unpack("<I", raw_header)[0]
        groups = []
        for i in range(group_count):
            raw_group = f.read(GROUP_STRUCT.size)
            if len(raw_group) != GROUP_STRUCT.size:
                raise EOFError(f"读取第 {i} 个组信息时文件意外结束")

            ext_raw, count, table_offset = GROUP_STRUCT.unpack(raw_group)
            groups.append({
                "ext": _read_cstr(ext_raw),
                "count": count,
                "table_offset": table_offset,
            })

        meta_entries = []
        for g in groups:
            ext = g["ext"]
            f.seek(g["table_offset"])
            entries = []
            for i in range(g["count"]):
                raw_entry = f.read(ENTRY_STRUCT.size)
                if len(raw_entry) != ENTRY_STRUCT.size:
                    raise EOFError(f"在组 '{ext}' 中读取第 {i} 个项时文件意外结束")

                name_raw, size, offset = ENTRY_STRUCT.unpack(raw_entry)
                stem = _read_cstr(name_raw)
                full_name = f"{stem}.{ext}" if ext else stem
                entries.append((full_name, size, offset, stem, ext))

            for full_name, size, offset, stem, ext in entries:
                f.seek(offset)
                data = f.read(size)
                if len(data) != size:
                    raise EOFError(f"文件数据不完整：'{full_name}' (预期 {size} 字节)")

                if ext.lower() == "scr":
                    data = _decode_scr(data)
                (out_dir / full_name).write_bytes(data)

                meta_entries.append({
                    "name": full_name,
                    "stem": stem,
                    "ext": ext,
                    "size": size,
                    "offset": offset,
                    "table_offset": g["table_offset"],
                })

    meta = {"source": str(input_path),
            "group_count": group_count, "entries": meta_entries}
    (out_dir / "__rio_meta__.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"[OK] unpack 完成: {input_path} -> {out_dir} ({len(meta_entries)} files)")


def pack(input_dir: Path, out_path: Path):
    if not input_dir.is_dir():
        raise ValueError(f"输入目录不存在: {input_dir}")

    files = list(_iter_input_files(input_dir))
    if not files:
        raise ValueError("输入目录没有可打包文件")

    groups = {}
    # 用于检测 8.3 重名冲突
    seen_internal_names = {}

    for p in files:
        stem = p.stem
        ext = p.suffix[1:] if p.suffix.startswith(".") else ""

        # --- 硬校验开始 ---
        if len(stem.encode("ascii")) > 8:
            raise ValueError(f"文件名过长 (MAX 8): '{stem}' 来自文件 {p.name}")
        if len(ext.encode("ascii")) > 3:
            raise ValueError(f"后缀名过长 (MAX 3): '{ext}' 来自文件 {p.name}")

        internal_full_name = (f"{stem}.{ext}" if ext else stem).upper()
        if internal_full_name in seen_internal_names:
            raise ValueError(
                f"检测到打包冲突！内部名称 '{internal_full_name}' 重复。\n"
                f"文件 1: {seen_internal_names[internal_full_name]}\n"
                f"文件 2: {p.name}"
            )
        seen_internal_names[internal_full_name] = p.name
        # --- 硬校验结束 ---

        key = ext.lower()
        groups.setdefault(key, {"ext": ext, "items": []})

        data = p.read_bytes()
        if ext.lower() == "scr":
            data = _encode_scr(data)

        groups[key]["items"].append({
            "stem": stem,
            "ext": ext,
            "full_name_upper": internal_full_name,
            "data": data,
        })

    # 游戏在组内做二分查找（不区分大小写），这里按同规则排序
    ordered_groups = []
    for _, g in sorted(groups.items(), key=lambda kv: kv[0]):
        g["items"].sort(key=lambda it: it["full_name_upper"])
        ordered_groups.append(g)

    # 偏移量计算逻辑保持不变（自动适应大小变化）
    group_count = len(ordered_groups)
    header_size = 4 + group_count * GROUP_STRUCT.size

    # 先排 group table
    current = header_size
    for g in ordered_groups:
        g["table_offset"] = current
        current += ENTRY_STRUCT.size * len(g["items"])

    # 再排数据区
    for g in ordered_groups:
        for item in g["items"]:
            item["offset"] = current
            item["size"] = len(item["data"])
            current += item["size"]

    # 写入文件
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as f:
        f.write(struct.pack("<I", group_count))

        for g in ordered_groups:
            # 使用更安全的 encode，如果超出定义长度这里会直接引发 struct.error
            ext_raw = g["ext"].encode("ascii").ljust(4, b"\x00")
            if len(ext_raw) > 4:  # 二次防线
                raise ValueError(f"扩展名处理异常: {g['ext']}")
            f.write(GROUP_STRUCT.pack(
                ext_raw, len(g["items"]), g["table_offset"]))

        for g in ordered_groups:
            for item in g["items"]:
                name_raw = item["stem"].encode("ascii").ljust(9, b"\x00")
                if len(name_raw) > 9:  # 二次防线
                    raise ValueError(f"文件名处理异常: {item['stem']}")
                f.write(ENTRY_STRUCT.pack(
                    name_raw, item["size"], item["offset"]))

        for g in ordered_groups:
            for item in g["items"]:
                f.write(item["data"])

    total_files = sum(len(g["items"]) for g in ordered_groups)
    print(f"[OK] pack 完成: {input_dir} -> {out_path} ({total_files} files)")


def main():
    ap = argparse.ArgumentParser(description="RIO.ARC 严格校验工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_unpack = sub.add_parser("unpack")
    ap_unpack.add_argument("-i", "--input", required=True)
    ap_unpack.add_argument("-o", "--out", required=True)

    ap_pack = sub.add_parser("pack")
    ap_pack.add_argument("-i", "--input", required=True)
    ap_pack.add_argument("-o", "--out", required=True)

    args = ap.parse_args()
    try:
        if args.cmd == "unpack":
            unpack(Path(args.input), Path(args.out))
        elif args.cmd == "pack":
            pack(Path(args.input), Path(args.out))
    except Exception as e:
        print(f"\n[错误] 任务失败: {e}")
        exit(1)


if __name__ == "__main__":
    main()
