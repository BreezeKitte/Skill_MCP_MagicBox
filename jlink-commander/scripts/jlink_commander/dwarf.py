# Copyright (c) 2026 BreezeKitte
# SPDX-License-Identifier: MIT

"""DWARF 全局地址 + 结构体成员偏移解析（R6 地址重算的底层依据）。

把固件的 .axf/.elf 读进内存，用 .symtab 取全局变量基址、用 DWARF 取嵌套
结构体成员偏移，从而把「源码里的 drvFOC.iDQFbck.fltArg1」之类路径解析成
绝对地址。改结构后旧地址整体漂移，必须重新解析，不能写死（R6）。

依赖 pyelftools（`pip install pyelftools`）。若未安装，resolve_* 会抛出
ImportError，调用方给出安装提示即可；无 DWARF 时也能用「显式地址表」模式采样。
"""
import os


def _import_elftools():
    try:
        from elftools.elf.elffile import ELFFile
        return ELFFile
    except ImportError as e:  # pragma: no cover - 依赖提示
        raise ImportError(
            "DWARF 解析需要 pyelftools：pip install pyelftools"
        ) from e


def resolve_globals(elf_path, names):
    """从 .symtab 取全局变量基址。names: 可迭代的全局名集合。返回 {name: addr_int}。"""
    ELFFile = _import_elftools()
    out = {}
    with open(elf_path, "rb") as f:
        elf = ELFFile(f)
        symtab = elf.get_section_by_name(".symtab")
        if symtab is None:
            return out
        for sym in symtab.iter_symbols():
            if sym.name in names and sym["st_info"]["type"] != "STT_NOTYPE":
                out[sym.name] = sym["st_value"]
    return out


class _DwarfResolver:
    def __init__(self, elf):
        self.elf = elf
        self.di = elf.get_dwarf_info()

    @staticmethod
    def _name(die):
        n = die.attributes.get("DW_AT_name")
        if n is None:
            return None
        v = n.value
        return v.decode("utf-8", "replace") if isinstance(v, bytes) else v

    def _ref_die(self, die, attr):
        if attr not in die.attributes:
            return None
        val = die.attributes[attr].value
        ref_addr = die.cu.cu_offset + val
        return self.di.get_DIE_from_refaddr(ref_addr)

    def _resolve(self, die):
        guard = 0
        while die is not None and die.tag in (
            "DW_TAG_typedef", "DW_TAG_const_type", "DW_TAG_volatile_type",
            "DW_TAG_restrict_type", "DW_TAG_reference_type",
        ):
            nxt = self._ref_die(die, "DW_AT_type")
            if nxt is None:
                break
            die = nxt
            guard += 1
            if guard > 50:
                break
        return die

    def named_type(self, type_name):
        for cu in self.di.iter_CUs():
            for die in cu.iter_DIEs():
                if die.tag in ("DW_TAG_typedef", "DW_TAG_structure_type",
                               "DW_TAG_union_type", "DW_TAG_class_type",
                               "DW_TAG_enumeration_type"):
                    if self._name(die) == type_name:
                        return die
        return None

    def _member_offset(self, die):
        if "DW_AT_data_member_location" not in die.attributes:
            return None
        loc = die.attributes["DW_AT_data_member_location"].value
        if isinstance(loc, int):
            return loc
        if hasattr(loc, "loc") and isinstance(loc.loc, list):
            for op, args in loc.loc:
                if op in ("DW_OP_plus_uconst", "DW_OP_constu") and args:
                    return args[0]
        return None

    def offset_of(self, type_name, member_path):
        die = self.named_type(type_name)
        if die is None:
            raise KeyError(f"type {type_name} not found")
        cur = self._resolve(die)
        total = 0
        for seg in member_path:
            if cur.tag not in ("DW_TAG_structure_type", "DW_TAG_union_type",
                               "DW_TAG_class_type"):
                raise TypeError(f"{type_name}: {seg} 基类型 {cur.tag}，非 struct/union")
            found = None
            for ch in cur.iter_children():
                if ch.tag == "DW_TAG_member" and self._name(ch) == seg:
                    found = ch
                    break
            if found is None:
                raise KeyError(f"member {seg} not found in {type_name}")
            bo = self._member_offset(found)
            if bo is None:
                raise ValueError(f"member {seg} 偏移非编译期常量")
            total += bo
            cur = self._resolve(self._ref_die(found, "DW_AT_type"))
        return total


def member_offset(elf_path, type_name, member_path):
    """解析某个类型中嵌套成员路径的偏移。member_path: ['pospeSensorless','wRotEl']。"""
    ELFFile = _import_elftools()
    with open(elf_path, "rb") as f:
        elf = ELFFile(f)
        res = _DwarfResolver(elf)
        return res.offset_of(type_name, member_path)


def resolve_elf(elf_path, globals_dict, member_paths_dict):
    """一次性解析多个全局 + 其成员路径。

    globals_dict: {global_name: static_type_or_None}
    member_paths_dict: {global_name: [(label, [path_segs]), ...]}
    返回 {'globals': {g: {'addr':..,'type':..}}, 'members': {f'{g}.{label}': {'offset':..,'abs':..}}}
    """
    ELFFile = _import_elftools()
    with open(elf_path, "rb") as f:
        elf = ELFFile(f)
        addrs = resolve_globals(elf_path, set(globals_dict.keys()))
        res = _DwarfResolver(elf)
        result = {"axf": elf_path, "globals": {}, "members": {}}
        for g, t in globals_dict.items():
            base = addrs.get(g)
            result["globals"][g] = {"addr": base, "type": t}
            if t and g in member_paths_dict:
                for label, path in member_paths_dict[g]:
                    try:
                        off = res.offset_of(t, path)
                        result["members"][f"{g}.{label}"] = {
                            "offset": off,
                            "abs": (base + off) if base is not None else None,
                        }
                    except Exception as e:
                        result["members"][f"{g}.{label}"] = {"error": str(e)}
        return result
