#!/usr/bin/env python3

import json
import os
from typing import Any, Dict, List, Tuple

from utils_tools.libs.ops_lib import (
    Handler,
    MatchFailed,
    assemble_one_op,
    byte_slice,
    end,
    fix_offset,
    flat,
    h,
    i16,
    parse_data,
    string,
    u8,
    u16,
    u32,
)
from utils_tools.libs.translate_lib import collect_files, de, read_bytes_s, read_str_s, read_u16_s, read_u32_s, read_u8_s, se


def parse_02_handler(data: bytes, offset: int, ctx: Dict) -> Tuple[List[Any], int]:
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
    results = []
    current_offset = offset

    # count / reserved
    count_val, current_offset = read_u8_s(data, current_offset)
    results.append(count_val)
    reserved_val, current_offset = read_u8_s(data, current_offset)
    results.append(reserved_val)

    count, _ = de(count_val)

    for _ in range(count):
        # id:u16
        u16_val, current_offset = read_u16_s(data, current_offset)
        results.append(u16_val)

        # text:string
        str_val, current_offset = read_str_s(data, current_offset)
        results.append(str_val)

        # type:u8
        type_val, current_offset = read_u8_s(data, current_offset)
        results.append(type_val)

        raw_type, _ = de(type_val)

        if raw_type == 0x07:
            s, current_offset = read_str_s(data, current_offset)
            results.append(s)
        elif raw_type == 0x06:
            u32, current_offset = read_u32_s(data, current_offset)
            results.append(u32)
            u8, current_offset = read_u8_s(data, current_offset)
            results.append(u8)
        elif raw_type == 0x03:
            v0, current_offset = read_u8_s(data, current_offset)
            results.append(v0)
            v1, current_offset = read_u8_s(data, current_offset)
            results.append(v1)
            v2, current_offset = read_u16_s(data, current_offset)
            results.append(v2)
            v3, current_offset = read_u8_s(data, current_offset)
            results.append(v3)
            v4, current_offset = read_u16_s(data, current_offset)
            results.append(v4)

    return results, current_offset


parse_02 = Handler(parse_02_handler)


def fix_02_type06_indices(op: Dict[str, Any]) -> List[int]:
    """
    返回 op=0x02 中需要做 old_offset->new_offset 修复的 value 索引列表。
    仅 type==0x06 的 payload[0](u32) 需要修。
    """
    vals = op.get("value", [])
    if len(vals) < 2:
        raise ValueError(f"0x02 结构异常: {op}")

    count, _ = de(vals[0])
    if not isinstance(count, int) or count < 0:
        raise ValueError(f"0x02 count 非法: {op}")

    i = 2  # 跳过 count/reserved
    indices: List[int] = []

    for _ in range(count):
        # id + text + type
        if i + 2 >= len(vals):
            raise ValueError(f"0x02 entry 结构不完整: {op}")

        i += 1  # id:u16
        i += 1  # text:string

        raw_type, _ = de(vals[i])
        i += 1  # type:u8

        if raw_type == 0x07:
            if i >= len(vals):
                raise ValueError(f"0x02 type=0x07 缺少字符串参数: {op}")
            i += 1
        elif raw_type == 0x06:
            # payload: [u32_offset, u8]
            if i + 1 >= len(vals):
                raise ValueError(f"0x02 type=0x06 缺少参数: {op}")
            indices.append(i)  # u32_offset 在 value[i]
            i += 2
        elif raw_type == 0x03:
            # payload: [u8, u8, u16, u8, u16]
            if i + 4 >= len(vals):
                raise ValueError(f"0x02 type=0x03 缺少参数: {op}")
            i += 5
        else:
            # 其它类型当前 parse_02 不读取附加参数
            pass

    return indices


FIX_OPS_MAP = {
    # case 0x06: dword_45D718 = base + u32_offset
    "06": [0],
    # case 0x02: entries(type==0x06) 的 payload[0] = u32_offset
    "02": fix_02_type06_indices,
}

OPCODES_MAP = flat(
    {
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
)


def disasm_mode(input_path: str, output_path: str):
    """反汇编模式：将二进制文件转换为JSON"""
    files = collect_files(input_path)

    for file in files:
        if file.endswith("json"):
            continue

        with open(file, "rb") as f:
            data = f.read()

        # 使用通用解析引擎和opcodes map
        opcodes, offset = parse_data(
            {
                "file_name": file,
                "offset": 0,
            },
            data,
            OPCODES_MAP,
        )

        assert offset == len(data)

        # 保存为JSON
        rel_path = os.path.relpath(file, start=input_path)
        out_file = os.path.join(output_path, rel_path + ".json")
        os.makedirs(os.path.dirname(out_file), exist_ok=True)

        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(opcodes, f, ensure_ascii=False, indent=2)


def asm_mode(input_path: str, output_path: str):
    """汇编模式：将JSON转换回二进制文件"""
    files = collect_files(input_path, "json")

    for file in files:
        with open(file, "r", encoding="utf-8") as f:
            opcodes = json.load(f)

        # ========= 第一步：assemble opcode，计算新 offset =========
        old2new = {}  # old_offset -> new_offset
        cursor = 0

        for op in opcodes:
            old_offset = op["offset"]
            b = assemble_one_op(op)

            old2new[old_offset] = cursor
            cursor += len(b)

        # ========= 第二步：修复 0x01 相对跳转偏移 =========
        # 01 | cond:u8 | lhs:u16 | rhs:u16 | rel:u32 | tail:u8
        # 解释器跳转目标: old_target = old_op_offset + 11 + old_rel
        # 重组后应更新为: new_rel = new_target - (new_op_offset + 11)
        for op in opcodes:
            if op.get("op") != "01":
                continue

            old_rel, type_hint = de(op["value"][3])
            old_op_offset = op["offset"]
            old_target = old_op_offset + 11 + old_rel

            if old_target not in old2new:
                raise ValueError(
                    f"{file}, {op} 0x01 指向不存在的 offset: {old_target}")

            new_op_offset = old2new[old_op_offset]
            new_target = old2new[old_target]
            new_rel = new_target - (new_op_offset + 11)
            op["value"][3] = se(new_rel, type_hint)

        # ========= 第三步：修复其它 opcodes 的偏移（如 0x06 绝对偏移） =========
        opcodes = fix_offset(file, opcodes, old2new, FIX_OPS_MAP)

        # ========= 第四步：assemble 修复过偏移的 opcodes =========
        new_blob = b"".join([assemble_one_op(op) for op in opcodes])

        # 保存二进制文件
        rel_path = os.path.relpath(file, start=input_path)
        rel_path = rel_path[:-5]  # 移除.json扩展名
        out_file = os.path.join(output_path, rel_path)
        os.makedirs(os.path.dirname(out_file), exist_ok=True)

        with open(out_file, "wb") as f:
            f.write(new_blob)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="游戏脚本反汇编/汇编工具")
    parser.add_argument(
        "mode", choices=["disasm", "asm"], help="模式: disasm(反汇编) 或 asm(汇编)"
    )
    parser.add_argument("input", help="输入文件夹路径")
    parser.add_argument("output", help="输出文件夹路径")

    args = parser.parse_args()

    if args.mode == "disasm":
        disasm_mode(args.input, args.output)
        print(f"反汇编完成: {args.input} -> {args.output}")
    elif args.mode == "asm":
        asm_mode(args.input, args.output)
        print(f"汇编完成: {args.input} -> {args.output}")


if __name__ == "__main__":
    main()
