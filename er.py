#!/usr/bin/env python3

import argparse
import json
import os
from typing import Any, Dict, List

from utils_tools.libs import translate_lib


def op02_text_indices(op: Dict[str, Any]) -> List[int]:
    """
    返回 op=0x02 中每个 entry 的 text 字段在 value 里的索引。
    结构见 ops.py 的 parse_02_handler：
      [count, reserved, (id, text, type, payload...) * count]
    payload 分支：
      type=0x07 -> +1(string)
      type=0x06 -> +2(u32,u8)
      type=0x03 -> +5(u8,u8,u16,u8,u16)
    """
    vals = op.get("value", [])
    if len(vals) < 2:
        raise ValueError(f"0x02 结构异常（缺少 count/reserved）: {op}")

    count, _ = translate_lib.de(vals[0])

    if not isinstance(count, int) or count < 0:
        raise ValueError(f"0x02 count 非法: {count}")

    i = 2
    indices: List[int] = []

    for _ in range(count):
        if i + 2 >= len(vals):
            raise ValueError(f"0x02 entry 结构不完整: {op}")

        i += 1  # id
        indices.append(i)  # text
        i += 1

        typ_raw = vals[i]
        typ, _ = translate_lib.de(typ_raw)

        if not isinstance(typ, int):
            raise ValueError(f"0x02 type 非法: {typ}")

        i += 1

        if typ == 0x07:
            if i >= len(vals):
                raise ValueError(f"0x02 type=0x07 缺少参数: {op}")
            i += 1
        elif typ == 0x06:
            if i + 1 >= len(vals):
                raise ValueError(f"0x02 type=0x06 缺少参数: {op}")
            i += 2
        elif typ == 0x03:
            if i + 4 >= len(vals):
                raise ValueError(f"0x02 type=0x03 缺少参数: {op}")
            i += 5

    return indices


def extract_strings_from_file(file_path: str) -> List[Dict]:
    """
    扫描单文件，提取 41 / 42 / 02 的可翻译文本。
    - 41: 无角色对话，message = value[2]
    - 42: 有角色对话，name = value[3], message = value[4]
    - 02: 选项块，每个 entry 的 text 都提取为 message，并标记 is_select=True
    """
    results: List[Dict] = []
    with open(file_path, "r", encoding="utf-8") as f:
        opcodes = json.load(f)

    for op in opcodes:
        if op["op"] == "41":
            item = {"message": op["value"][2]}
            if item["message"].startswith("　"):
                item["need_whitespace"] = True
            results.append(item)
        elif op["op"] == "42":
            item = {"name": op["value"][3], "message": op["value"][4]}
            if item["message"].startswith("　"):
                item["need_whitespace"] = True
            results.append(item)
        elif op["op"] == "02":
            for text_idx in op02_text_indices(op):
                results.append(
                    {"message": op["value"][text_idx], "is_select": True})

    return results


def extract_strings(path: str, output_file: str):
    files = translate_lib.collect_files(path)
    results = []
    for file in files:
        results.extend(extract_strings_from_file(file))

    print(f"提取了 {len(results)} 项")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)


# ========== 替换 ==========


def replace_in_file(
    file_path: str,
    text: List[Dict[str, Any]],
    output_dir: str,
    trans_index: int,
    base_root: str,
) -> int:
    """
    替换单文件中的字符串。返回更新后的 trans_index。
    text: 全局译文列表（每项至少有 'message'，42 可带 'name'）
    """
    with open(file_path, "r", encoding="utf-8") as f:
        opcodes = json.load(f)

    new_opcodes = []

    for op in opcodes:
        if op["op"] == "41":
            trans_item = text[trans_index]
            trans_index += 1
            op["value"][2] = trans_item["message"]
        elif op["op"] == "42":
            trans_item = text[trans_index]
            trans_index += 1
            if "name" in trans_item:
                op["value"][3] = trans_item["name"]
            op["value"][4] = trans_item["message"]
        elif op["op"] == "02":
            for text_idx in op02_text_indices(op):
                trans_item = text[trans_index]
                trans_index += 1
                op["value"][text_idx] = trans_item["message"]

        new_opcodes.append(op)

    # ---------- 保存 ----------
    rel = os.path.relpath(file_path, start=base_root)
    out_path = os.path.join(output_dir, rel)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(new_opcodes, f, ensure_ascii=False, indent=2)

    return trans_index


def replace_strings(path: str, text_file: str, output_dir: str):
    with open(text_file, "r", encoding="utf-8") as f:
        text = json.load(f)
    files = translate_lib.collect_files(path)
    trans_index = 0

    for file in files:
        trans_index = replace_in_file(
            file, text, output_dir, trans_index, base_root=path
        )
        print(f"已处理: {file}")
    if trans_index != len(text):
        print(f"错误: 有 {len(text)} 项译文，但只消耗了 {trans_index}。")
        exit(1)


# ---------------- main ----------------


def main():
    parser = argparse.ArgumentParser(description="文件提取和替换工具")
    subparsers = parser.add_subparsers(
        dest="command", help="功能选择", required=True)

    ep = subparsers.add_parser("extract", help="解包文件提取文本")
    ep.add_argument("--path", required=True, help="文件夹路径")
    ep.add_argument("--output", default="raw.json", help="输出JSON文件路径")

    rp = subparsers.add_parser("replace", help="替换解包文件中的文本")
    rp.add_argument("--path", required=True, help="文件夹路径")
    rp.add_argument("--text", default="translated.json", help="译文JSON文件路径")
    rp.add_argument(
        "--output-dir", default="translated", help="输出目录(默认: translated)"
    )

    args = parser.parse_args()
    if args.command == "extract":
        extract_strings(args.path, args.output)
        print(f"提取完成! 结果保存到 {args.output}")
    elif args.command == "replace":
        replace_strings(args.path, args.text, args.output_dir)
        print(f"替换完成! 结果保存到 {args.output_dir} 目录")


if __name__ == "__main__":
    main()
