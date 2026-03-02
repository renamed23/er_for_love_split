from pathlib import Path

from er.core.gal_json import GalJson
from er.utils.binary import de
from er.utils.console import console
from er.utils.fs import PathLike, collect_files, to_path
from er.utils.instructions import Instruction
from er.utils.misc import ensure_str, read_json, str_or_none, write_json


def _collect_choice_text_indices(inst: Instruction) -> list[int]:
    """
    获取 0x02 选项块内所有文本字段索引。

    结构：
    [count, reserved, (id, text, type, payload...) * count]

    Args:
        inst: op=0x02 指令对象。

    Returns:
        每个 entry 的 text 在 value 中的索引列表。
    """
    values_obj = inst.get("value")
    if not isinstance(values_obj, list):
        raise TypeError(f"0x02 value 字段非法: {values_obj}")
    if len(values_obj) < 2:
        raise ValueError(f"0x02 结构异常（缺少 count/reserved）: {inst}")

    count_raw = ensure_str(values_obj[0], "0x02 count")
    count = de(count_raw)
    if not isinstance(count, int) or count < 0:
        raise ValueError(f"0x02 count 非法: {count_raw}")

    i = 2
    indices: list[int] = []

    for _ in range(count):
        if i + 2 >= len(values_obj):
            raise ValueError(f"0x02 entry 结构不完整: {inst}")

        i += 1  # id
        indices.append(i)  # text
        i += 1

        typ_raw = ensure_str(values_obj[i], "0x02 type")
        typ = de(typ_raw)
        if not isinstance(typ, int):
            raise TypeError(f"0x02 type 非法: {typ_raw}")
        i += 1

        match typ:
            case 0x07:
                if i >= len(values_obj):
                    raise ValueError(f"0x02 type=0x07 缺少参数: {inst}")
                i += 1
            case 0x06:
                if i + 1 >= len(values_obj):
                    raise ValueError(f"0x02 type=0x06 缺少参数: {inst}")
                i += 2
            case 0x03:
                if i + 4 >= len(values_obj):
                    raise ValueError(f"0x02 type=0x03 缺少参数: {inst}")
                i += 5
            case _:
                pass

    return indices


def _extract_from_script(
    script_path: Path,
    gal_json: GalJson,
) -> None:
    """
    从单个脚本中提取可翻译条目。

    Args:
        script_path: 输入脚本路径。
        gal_json: 原文容器。

    Returns:
        None
    """
    insts: list[Instruction] = read_json(script_path)

    for index, inst in enumerate(insts):
        op = ensure_str(inst.get("op"))
        values = inst["value"]

        match op:
            case "41":
                if len(values) <= 2:
                    raise ValueError(f"0x41 参数不足: {script_path}:{index}")
                message = ensure_str(values[2])
                item: dict[str, object] = {"message": message}
                if message.startswith("　"):
                    item["need_whitespace"] = True
                gal_json.add_item(item)

            case "42":
                if len(values) <= 4:
                    raise ValueError(f"0x42 参数不足: {script_path}:{index}")
                name = ensure_str(values[3])
                message = ensure_str(values[4])
                item = {"name": name, "message": message}
                if message.startswith("　"):
                    item["need_whitespace"] = True
                gal_json.add_item(item)

            case "02":
                for text_idx in _collect_choice_text_indices(inst):
                    if text_idx >= len(values):
                        raise ValueError(
                            f"0x02 文本索引越界: {script_path}:{index}, idx={text_idx}"
                        )
                    message = ensure_str(values[text_idx])
                    gal_json.add_item({"message": message, "is_select": True})

            case _:
                continue


def _apply_translation_to_script(
    script_path: Path,
    gal_json: GalJson,
    output_root: Path,
    base_root: Path,
) -> None:
    """
    将译文应用到单个脚本。

    Args:
        script_path: 输入脚本路径。
        gal_json: 译文数据容器。
        output_root: 输出目录。
        base_root: 输入根目录，用于计算相对路径。

    Returns:
        None
    """
    insts: list[Instruction] = read_json(script_path)

    for index, inst in enumerate(insts):
        op = ensure_str(inst.get("op"))
        values = inst["value"]

        match op:
            case "41":
                if len(values) <= 2:
                    raise ValueError(f"0x41 参数不足: {script_path}:{index}")
                item = gal_json.pop_next_item()
                values[2] = ensure_str(item.get("message"))

            case "42":
                if len(values) <= 4:
                    raise ValueError(f"0x42 参数不足: {script_path}:{index}")

                item = gal_json.pop_next_item()
                raw_name = ensure_str(values[3])
                override_name = str_or_none(item.get("name"), f"item.name: {index}")

                if override_name is not None:
                    values[3] = override_name
                elif raw_name in gal_json.names:
                    values[3] = gal_json.get_translated_name(raw_name)

                values[4] = ensure_str(item.get("message"))

            case "02":
                for text_idx in _collect_choice_text_indices(inst):
                    if text_idx >= len(values):
                        raise ValueError(
                            f"0x02 文本索引越界: {script_path}:{index}, idx={text_idx}"
                        )
                    item = gal_json.pop_next_item()
                    values[text_idx] = ensure_str(item.get("message"))

            case _:
                continue

    output_path = output_root / script_path.relative_to(base_root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_path, insts)


def extract(input_dir: PathLike, gal_json: GalJson) -> None:
    """
    提取目录下脚本文本到容器中。

    Args:
        input_dir: 反汇编后的脚本目录（json）。
        gal_json: 原文容器。

    Returns:
        None
    """
    source_root = to_path(input_dir)
    files = collect_files(source_root, "json")

    for file in files:
        _extract_from_script(file, gal_json)

    console.print(
        f"[OK] 文本提取完成: {source_root} ({len(files)} files, {gal_json.total_count()} items)",
        style="info",
    )


def apply(input_dir: PathLike, gal_json: GalJson, output_dir: PathLike) -> None:
    """
    将 GalJson 中的译文应用到原始脚本，新文件输出到新目录中

    Args:
        input_dir: 原始脚本目录（json）。
        gal_json: 译文容器。
        output_dir: 替换后脚本输出目录。

    Returns:
        None
    """
    source_root = to_path(input_dir)
    output_root = to_path(output_dir)

    files = collect_files(source_root, "json")
    gal_json.reset_cursor()

    for file in files:
        _apply_translation_to_script(
            script_path=file,
            gal_json=gal_json,
            output_root=output_root,
            base_root=source_root,
        )

    if not gal_json.is_ran_out():
        raise ValueError(
            "替换完成但仍有未消费译文条目："
            f"remaining={gal_json.remaining_count()}, consumed={gal_json.consumed_count()}, "
            f"total={gal_json.total_count()}"
        )

    console.print(
        f"[OK] 文本替换完成: {source_root} -> {output_root} ({len(files)} files)",
        style="info",
    )
