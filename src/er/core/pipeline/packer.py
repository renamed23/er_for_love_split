import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from er.utils.binary import BinaryReader
from er.utils.console import console
from er.utils.fs import PathLike, collect_files, to_path

# 静态定义结构
ENTRY_STRUCT = struct.Struct("<9sII")  # name(9), size(u32), offset(u32)
GROUP_STRUCT = struct.Struct("<4sII")  # ext(4), count(u32), table_offset(u32)


@dataclass(slots=True)
class ArcGroup:
    ext: str
    count: int
    table_offset: int


@dataclass(slots=True)
class ArcEntry:
    full_name: str
    stem: str
    ext: str
    size: int
    offset: int


@dataclass(slots=True)
class PackedItem:
    stem: str
    ext: str
    full_name_upper: str
    data: bytes
    size: int = 0
    offset: int = 0


@dataclass(slots=True)
class PackedGroup:
    ext: str
    items: list[PackedItem]
    table_offset: int = 0


def _read_cstr(raw: bytes) -> str:
    """读取以 ``\0`` 结尾的字节串，并按 ASCII 严格解码。

    Args:
        raw: 固定长度字节切片。

    Returns:
        解码后的字符串。

    Raises:
        ValueError: 字节内容无法按 ASCII 解码。
    """
    content = raw.split(b"\x00", 1)[0]
    try:
        return content.decode("ascii")
    except UnicodeDecodeError:
        raise ValueError(
            f"编码错误：无法将字节流 {content.hex()} 解码为 ASCII，请检查文件名或文件格式。"
        )


def _decode_scr(data: bytes) -> bytes:
    """对 ``.scr`` 文件执行 ROR 2 解码。

    Args:
        data: 原始字节。

    Returns:
        解码后的字节。
    """
    out = bytearray(data)
    for i, b in enumerate(out):
        out[i] = ((b >> 2) | ((b & 0x03) << 6)) & 0xFF
    return bytes(out)


def _encode_scr(data: bytes) -> bytes:
    """对 ``.scr`` 文件执行 ROL 2 编码。

    Args:
        data: 原始字节。

    Returns:
        编码后的字节。
    """
    out = bytearray(data)
    for i, b in enumerate(out):
        out[i] = (((b << 2) & 0xFF) | (b >> 6)) & 0xFF
    return bytes(out)


def _iter_input_files(input_dir: Path) -> Iterator[Path]:
    """遍历输入目录下可打包文件。

    Args:
        input_dir: 输入目录。

    Returns:
        文件路径迭代器。

    Raises:
        ValueError: 输入目录存在子目录结构时抛出。
    """
    for file in collect_files(input_dir):
        rel = file.relative_to(input_dir)
        if len(rel.parts) != 1:
            raise ValueError(f"RIO.ARC 仅支持平铺文件名，检测到子目录结构: {rel}")
        yield file


def unpack(input_path: PathLike, out_dir: PathLike) -> None:
    """解包 RIO.ARC。

    Args:
        input_path: 输入包路径。
        out_dir: 解包输出目录。

    Returns:
        None

    Raises:
        EOFError: 二进制数据不完整。
        ValueError: 字段解码失败或格式非法。
    """
    source = to_path(input_path)
    output_dir = to_path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with source.open("rb") as f:
        raw_header = f.read(4)
        if len(raw_header) != 4:
            raise EOFError("读取 Header 失败：文件过小或已损坏")

        group_count = int(BinaryReader(raw_header).read_u32())
        groups: list[ArcGroup] = []
        for i in range(group_count):
            raw_group = f.read(GROUP_STRUCT.size)
            if len(raw_group) != GROUP_STRUCT.size:
                raise EOFError(f"读取第 {i} 个组信息时文件意外结束")

            ext_raw, count, table_offset = GROUP_STRUCT.unpack(raw_group)
            groups.append(
                ArcGroup(
                    ext=_read_cstr(ext_raw), count=count, table_offset=table_offset
                )
            )

        total_files = 0
        for group in groups:
            f.seek(group.table_offset)
            entries: list[ArcEntry] = []
            for i in range(group.count):
                raw_entry = f.read(ENTRY_STRUCT.size)
                if len(raw_entry) != ENTRY_STRUCT.size:
                    raise EOFError(
                        f"在组 '{group.ext}' 中读取第 {i} 个项时文件意外结束"
                    )

                name_raw, size, offset = ENTRY_STRUCT.unpack(raw_entry)
                stem = _read_cstr(name_raw)
                full_name = f"{stem}.{group.ext}" if group.ext else stem
                entries.append(
                    ArcEntry(
                        full_name=full_name,
                        stem=stem,
                        ext=group.ext,
                        size=size,
                        offset=offset,
                    )
                )

            for entry in entries:
                f.seek(entry.offset)
                data = f.read(entry.size)
                if len(data) != entry.size:
                    raise EOFError(
                        f"文件数据不完整：'{entry.full_name}' (预期 {entry.size} 字节)"
                    )

                if entry.ext.lower() == "scr":
                    data = _decode_scr(data)
                (output_dir / entry.full_name).write_bytes(data)
                total_files += 1

    console.print(
        f"[OK] unpack 完成: {source} -> {output_dir} ({total_files} files)",
        style="info",
    )


def pack(input_dir: PathLike, out_path: PathLike) -> None:
    """将目录内容打包为 RIO.ARC。

    Args:
        input_dir: 输入目录路径。
        out_path: 输出包路径。

    Returns:
        None

    Raises:
        ValueError: 输入非法、命名冲突或字段超限。
    """
    input_root = to_path(input_dir)
    output_path = to_path(out_path)
    if not input_root.is_dir():
        raise ValueError(f"输入目录不存在: {input_root}")

    files = list(_iter_input_files(input_root))
    if not files:
        raise ValueError("输入目录没有可打包文件")

    groups: dict[str, PackedGroup] = {}
    # 用于检测 8.3 重名冲突
    seen_internal_names: dict[str, str] = {}

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
        group = groups.setdefault(key, PackedGroup(ext=ext, items=[]))

        data = p.read_bytes()
        if ext.lower() == "scr":
            data = _encode_scr(data)

        group.items.append(
            PackedItem(
                stem=stem,
                ext=ext,
                full_name_upper=internal_full_name,
                data=data,
            )
        )

    # 游戏在组内做二分查找（不区分大小写），这里按同规则排序
    ordered_groups = sorted(groups.values(), key=lambda g: g.ext.lower())
    for group in ordered_groups:
        group.items.sort(key=lambda item: item.full_name_upper)

    # 偏移量计算逻辑保持不变（自动适应大小变化）
    group_count = len(ordered_groups)
    header_size = 4 + group_count * GROUP_STRUCT.size

    # 先排 group table
    current = header_size
    for group in ordered_groups:
        group.table_offset = current
        current += ENTRY_STRUCT.size * len(group.items)

    # 再排数据区
    for group in ordered_groups:
        for item in group.items:
            item.offset = current
            item.size = len(item.data)
            current += item.size

    # 写入文件
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as f:
        f.write(struct.pack("<I", group_count))

        for group in ordered_groups:
            # 使用更安全的 encode，如果超出定义长度这里会直接引发 struct.error
            ext_raw = group.ext.encode("ascii").ljust(4, b"\x00")
            if len(ext_raw) > 4:  # 二次防线
                raise ValueError(f"扩展名处理异常: {group.ext}")
            f.write(GROUP_STRUCT.pack(ext_raw, len(group.items), group.table_offset))

        for group in ordered_groups:
            for item in group.items:
                name_raw = item.stem.encode("ascii").ljust(9, b"\x00")
                if len(name_raw) > 9:  # 二次防线
                    raise ValueError(f"文件名处理异常: {item.stem}")
                f.write(ENTRY_STRUCT.pack(name_raw, item.size, item.offset))

        for group in ordered_groups:
            for item in group.items:
                f.write(item.data)

    total_files = sum(len(group.items) for group in ordered_groups)
    console.print(
        f"[OK] pack 完成: {input_root} -> {output_path} ({total_files} files)",
        style="info",
    )


def decode_patch_files(input_dir: PathLike, output_dir: PathLike) -> None:
    """解码补丁文件到输出目录"""
    input_dir = to_path(input_dir)
    output_dir = to_path(output_dir)

    files = collect_files(input_dir)
    for file in files:
        decoded = _decode_scr(file.read_bytes())
        fullpath = output_dir / file.name
        fullpath.write_bytes(decoded)

    console.print(
        f"[OK] decode patch 完成: {input_dir} -> {output_dir}",
        style="info",
    )
