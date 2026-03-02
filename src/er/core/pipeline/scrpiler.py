from er.utils.console import console
from er.utils.instructions import (
    Handler,
    Instruction,
    assemble_one_inst,
    byte_slice,
    end,
    fix_offset,
    h,
    i16,
    parse_data,
    string,
    u8,
    u16,
    u32,
)
from er.utils.binary import BinaryReader, de, se
from er.utils.fs import PathLike, collect_files, to_path
from er.utils.misc import read_json, write_json


def parse_02_handler(reader: BinaryReader, ctx: Instruction) -> list[str]:
    """
    0x02 选项块解析器（对应 sub_4051A0 case 0x02）

    结构：
    - count:u8
    - reserved:u8
    - entries * count，每项：
      - id:u16
      - text:string
      - type:u8
      - 按 type 的附加参数：
        - type == 0x07: string
        - type == 0x06: u32, u8
        - type == 0x03: u8, u8, u16, u8, u16
        - 其他类型：当前不读取附加参数（按 case 分支默认保留）
    """
    _ = ctx
    results: list[str] = []

    # count / reserved
    count_val = se(reader.read_u8())
    results.append(count_val)
    reserved_val = se(reader.read_u8())
    results.append(reserved_val)

    count = de(count_val)
    if not isinstance(count, int):
        raise TypeError(f"0x02 count 不是整数: {count_val}")

    for _ in range(count):
        # id:u16
        u16_val = se(reader.read_u16())
        results.append(u16_val)

        # text:string
        str_val = se(reader.read_str())
        results.append(str_val)

        # type:u8
        type_val = se(reader.read_u8())
        results.append(type_val)

        raw_type = de(type_val)
        if not isinstance(raw_type, int):
            raise TypeError(f"0x02 type 不是整数: {type_val}")

        if raw_type == 0x07:
            s = se(reader.read_str())
            results.append(s)
        elif raw_type == 0x06:
            u32_val = se(reader.read_u32())
            results.append(u32_val)
            u8_val = se(reader.read_u8())
            results.append(u8_val)
        elif raw_type == 0x03:
            v0 = se(reader.read_u8())
            results.append(v0)
            v1 = se(reader.read_u8())
            results.append(v1)
            v2 = se(reader.read_u16())
            results.append(v2)
            v3 = se(reader.read_u8())
            results.append(v3)
            v4 = se(reader.read_u16())
            results.append(v4)

    return results


parse_02 = Handler(parse_02_handler)


def fix_02_type06_indices(inst: Instruction) -> list[int]:
    """
    返回 op=0x02 中需要做 old_offset->new_offset 修复的 value 索引列表。
    仅 type==0x06 的 payload[0](u32) 需要修。
    """
    vals = inst.get("value", [])
    if not isinstance(vals, list):
        raise TypeError(f"0x02 value 字段非法: {vals}")
    if len(vals) < 2:
        raise ValueError(f"0x02 结构异常: {inst}")
    if not isinstance(vals[0], str):
        raise TypeError(f"0x02 count 字段非法: {vals[0]}")

    count = de(vals[0])
    if not isinstance(count, int) or count < 0:
        raise ValueError(f"0x02 count 非法: {inst}")

    i = 2  # 跳过 count/reserved
    indices: list[int] = []

    for _ in range(count):
        # id + text + type
        if i + 2 >= len(vals):
            raise ValueError(f"0x02 entry 结构不完整: {inst}")

        i += 1  # id:u16
        i += 1  # text:string

        if not isinstance(vals[i], str):
            raise TypeError(f"0x02 type 字段非法: {vals[i]}")
        raw_type = de(vals[i])
        if not isinstance(raw_type, int):
            raise TypeError(f"0x02 type 不是整数: {vals[i]}")
        i += 1  # type:u8

        if raw_type == 0x07:
            if i >= len(vals):
                raise ValueError(f"0x02 type=0x07 缺少字符串参数: {inst}")
            i += 1
        elif raw_type == 0x06:
            # payload: [u32_offset, u8]
            if i + 1 >= len(vals):
                raise ValueError(f"0x02 type=0x06 缺少参数: {inst}")
            indices.append(i)  # u32_offset 在 value[i]
            i += 2
        elif raw_type == 0x03:
            # payload: [u8, u8, u16, u8, u16]
            if i + 4 >= len(vals):
                raise ValueError(f"0x02 type=0x03 缺少参数: {inst}")
            i += 5
        else:
            # 其它类型当前 parse_02 不读取附加参数
            pass

    return indices


FIX_INST_MAP = {
    # case 0x06: dword_45D718 = base + u32_offset
    "06": [0],
    # case 0x02: entries(type==0x06) 的 payload[0] = u32_offset
    "02": fix_02_type06_indices,
}

INST_MAP = {
    # 条件判断 + 相对跳转（固定 11 字节）
    # 01 | cond:u8 | lhs_var:u16 | rhs:u16 | jmp_rel:u32 | tail:u8
    h("01"): [u8, u16, u16, u32, u8],
    # 变量操作族（固定 8 字节）
    # 03 | sub:u8 | dst:u16 | flag:u8 | rhs:u16 | tail:u8
    # sub_4051A0 中 sub!=0..6 也会按 default 消耗同样长度
    h("03"): [u8, u16, u8, u16, u8],
    # 选项块（case 0x02）
    h("02"): [parse_02],
    # 跳转到脚本内绝对偏移（相对脚本起始）
    h("06"): [u32, u8.eq(0x0)],
    # 切换脚本（读取 C 字符串脚本名）
    h("07"): [string],
    # 子调用 / 标签调用（字符串）
    h("09"): [string],
    # 音乐/音效/语音文件相关的 OP
    h("21"): [u8, u16, string],
    # 音乐/音效/语音文件相关的 OP
    h("25"): [u8, u16, string],
    # 音乐/音效/语音文件相关的 OP
    h("23"): [u8, u16.repeat(2), string],
    # ***文本显示（单行）
    h("41"): [u16, u8, string],
    # ***文本显示（人名 + 正文）
    h("42"): [u16, u8.repeat(2), string.repeat(2)],
    # 立绘/图层（文件名 + 两个坐标 + 标志）
    h("43"): [u8, u16.repeat(2), u8, string],
    # 背景（文件名 + 两个坐标 + 资源id + 标志）
    h("46"): [u16.repeat(2), u32, u8, string],
    # 叠加层（slot + 两个坐标 + 资源id + 标志 + 文件名）
    h("48"): [u8, u16.repeat(2), u32, u8, string],
    # 表格资源（*.tbl）
    h("50"): [string],
    # msk 文件名
    h("54"): [string],
    # 调用 DAT 文件的 OP
    h("61"): [u8, string],
    # 0xFF 在解释器里走错误处理；这里设为可识别终止，便于反汇编不中断。
    h("FF"): [end],
    # -----------------------------------------------------
    h("26"): [u16],  # 非偏移
    h("45"): [u8, u8, u8, u8],  # 非偏移
    h("49"): [u8, u8, u8],  # 非偏移
    h("4E"): [u8, u8, u8],  # 非偏移
    h("4F"): [u8, u8, u8, u8],  # 非偏移
    # u16 代表文件序号？
    h("8C"): [u16, u8],  # 非偏移
    h("4A"): [byte_slice.args(4)],  # 非偏移
    h("8E"): [u8],  # 非偏移
    h("22"): [u8, u16, u8],  # 非偏移
    h("E4"): [u8, u8],  # 非偏移
    h("E5"): [u8],  # 非偏移
    h("4D"): [u8, u8, u16, u8],  # 非偏移
    h("47"): [u8, u8],  # 非偏移
    h("8A"): [u8, u8],  # 非偏移
    h("8B"): [u8],  # 非偏移
    h("85"): [u8, u8],  # 非偏移
    h("83"): [u8],  # 非偏移
    h("86"): [u8, u8],  # 非偏移
    h("89"): [u8],  # 非偏移
    h("74"): [u8, u8],  # 非偏移
    h("05"): [u8],  # 非偏移
    h("0A"): [u8],  # 非偏移
    h("0B"): [u8, u8],  # 非偏移
    h("0C"): [u16, u8],  # 非偏移
    h("69"): [u8, u8],  # 非偏移
    h("65"): [i16, i16, u8],  # 非偏移
    h("64"): [u8, i16, u8],  # 非偏移
    h("4C"): [u8],  # 非偏移
    h("4B"): [u8, u16, u16, u32, u8],  # 非偏移
    h("04"): [],  # 非偏移
    h("55"): [u8],  # 非偏移
    h("52"): [u8, u8],  # 非偏移
    h("51"): [u16, u16, u8],  # 非偏移
    # ====== 已出现但参数结构仍不确定（仅记录，不纳入解析） ======
    # 0x02,0x04,0x05,0x08,0x0A,0x0B,0x0C,
    # 0x21~0x26,
    # 0x44,0x45,0x47,0x49,0x4A,0x4B,0x4C,0x4D,0x4E,0x4F,0x51,0x52,0x55,0x56,0x57,0x58,
    # 0x60~0x69,
    # 0x70,0x72,0x74~0x79,
    # 0x81~0x8E,
    # 0xB1~0xB5,
    # 0xE1~0xE5
}


def decompile(input_path: PathLike, output_path: PathLike) -> None:
    """反编译：将二进制文件转换为JSON"""
    input_root = to_path(input_path)
    output_root = to_path(output_path)
    files = collect_files(input_root)

    for file in files:
        if file.suffix == ".json":
            continue

        reader = BinaryReader(file.read_bytes())

        insts = parse_data(
            {
                "file_name": str(file),
                "offset": 0,
            },
            reader,
            INST_MAP,
        )

        assert reader.is_eof()

        # 保存为JSON
        rel_path = file.relative_to(input_root)
        out_file = output_root / f"{rel_path.as_posix()}.json"
        out_file.parent.mkdir(parents=True, exist_ok=True)

        write_json(out_file, insts)

    console.print(f"[OK] decompile 完成: {input_path} -> {output_path}", style="info")


def compile(input_path: PathLike, output_path: PathLike) -> None:
    """编译：将JSON转换回二进制文件"""
    input_root = to_path(input_path)
    output_root = to_path(output_path)
    files = collect_files(input_root, "json")

    for file in files:
        insts: list[Instruction] = read_json(file)

        # ========= 第一步：assemble instruction，计算新 offset =========
        old2new = {}  # old_offset -> new_offset
        cursor = 0

        for inst in insts:
            old_offset = inst["offset"]
            b = assemble_one_inst(inst)

            old2new[old_offset] = cursor
            cursor += len(b)

        # ========= 第二步：修复 0x01 相对跳转偏移 =========
        # 01 | cond:u8 | lhs:u16 | rhs:u16 | rel:u32 | tail:u8
        # 解释器跳转目标: old_target = old_inst_offset + 11 + old_rel
        # 重组后应更新为: new_rel = new_target - (new_inst_offset + 11)
        for inst in insts:
            if inst.get("op") != "01":
                continue

            old_rel_raw = de(inst["value"][3])
            if not isinstance(old_rel_raw, int):
                raise TypeError(f"0x01 跳转偏移不是整数: {inst}")

            old_inst_offset = inst["offset"]
            old_target = old_inst_offset + 11 + old_rel_raw

            if old_target not in old2new:
                raise ValueError(
                    f"{file}, {inst} 0x01 指向不存在的 offset: {old_target}"
                )

            new_inst_offset = old2new[old_inst_offset]
            new_target = old2new[old_target]
            new_rel = new_target - (new_inst_offset + 11)
            inst["value"][3] = se(type(old_rel_raw)(new_rel))

        # ========= 第三步：修复其它指令的偏移（如 0x06 绝对偏移） =========
        insts = fix_offset(str(file), insts, old2new, FIX_INST_MAP)

        # ========= 第四步：assemble 修复过偏移的指令 =========
        new_blob = b"".join([assemble_one_inst(inst) for inst in insts])

        # 保存二进制文件
        rel_path = file.relative_to(input_root)
        out_file = output_root / rel_path.with_suffix("")
        out_file.parent.mkdir(parents=True, exist_ok=True)

        out_file.write_bytes(new_blob)

    console.print(f"[OK] compile 完成: {input_path} -> {output_path}", style="info")
