#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Windows 硬件体检分析器（v0.1.0）

输入：collect.ps1 生成的 JSON
输出：findings JSON + 自包含 HTML 报告

用法：
  python analyze.py out/hardware_audit_HOST_TS.json --profile mixed
  python analyze.py out/x.json --profile video --out-html report.html
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
from pathlib import Path

VERSION = "0.1.0"

SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
SEVERITY_LABEL = {
    "P0": "P0 数据风险",
    "P1": "P1 稳定性",
    "P2": "P2 性能",
    "P3": "P3 优化",
}

PROFILE_WEIGHTS = {
    "video":  {"cpu": 0.30, "ram": 0.25, "gpu": 0.25, "storage": 0.15, "thermal": 0.05},
    "quant":  {"cpu": 0.40, "ram": 0.30, "gpu": 0.05, "storage": 0.20, "thermal": 0.05},
    "office": {"cpu": 0.25, "ram": 0.35, "gpu": 0.05, "storage": 0.25, "thermal": 0.10},
    "mixed":  {"cpu": 0.30, "ram": 0.30, "gpu": 0.15, "storage": 0.20, "thermal": 0.05},
}


# ---------------------------------------------------------------- helpers
def as_list(v):
    """PowerShell 5.1 会把单元素数组序列化成对象，这里统一拉平。"""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        return [v]
    return [v]


def num(v, default=None):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def esc(v):
    return html.escape("" if v is None else str(v))


def parse_date(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(str(s)[:len(fmt) + 2].strip(), fmt)
        except ValueError:
            continue
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(s))
    if m:
        return dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


# ---------------------------------------------------------------- memory model
SAMSUNG_DENSITY = {"1": 8, "2": 16, "4": 32, "8": 64, "A": 128}


def samsung_pn_capacity(part):
    """按三星 UDIMM 命名推断标称容量（GB）。无法判断返回 None。"""
    if not part:
        return None
    p = str(part).upper().strip()
    m = re.search(r"M378A([0-9A])([GK])43", p)
    if m:
        return SAMSUNG_DENSITY.get(m.group(1))
    return None


def primary_gpu(gpus):
    gpus = [g for g in as_list(gpus) if isinstance(g, dict)]
    real = [g for g in gpus if str(g.get("status", "")).lower() in ("ok", "")]
    pool = real or gpus
    if not pool:
        return None
    return max(pool, key=lambda g: num(g.get("vram_gb_effective", g.get("vram_gb")), 0) or 0)


def nvidia_vram_gb(data):
    """Win32_VideoController.AdapterRAM 在 32 位字段里会被截到 4GB，优先用 nvidia-smi。"""
    s = ((data.get("thermal") or {}).get("nvidia_smi"))
    if isinstance(s, str):
        parts = [p.strip() for p in s.split(",")]
        if len(parts) >= 3:
            try:
                return round(int(float(parts[2])) / 1024, 1)
            except (TypeError, ValueError):
                pass
    return None


def apply_effective_vram(data):
    vg = nvidia_vram_gb(data)
    for g in as_list(data.get("gpu")):
        if not isinstance(g, dict):
            continue
        base = num(g.get("vram_gb"))
        if vg and "NVIDIA" in str(g.get("name", "")).upper():
            g["vram_gb_effective"] = vg
        else:
            g["vram_gb_effective"] = base


def gpu_tier(name):
    n = (name or "").upper()
    table = [
        ("RTX 5090", 100), ("RTX 5080", 95), ("RTX 5070", 85),
        ("RTX 4090", 100), ("RTX 4080", 90), ("RTX 4070", 80),
        ("RTX 4060", 62), ("RTX 3090", 88), ("RTX 3080", 80),
        ("RTX 3070", 70), ("RTX 3060", 55), ("RTX 3050", 38),
        ("RTX 2080", 58), ("RTX 2070", 50), ("RTX 2060", 40),
        ("GTX 1660", 28), ("GTX 1650", 22), ("GTX 1080", 30),
        ("GTX 1070", 24), ("GTX 1060", 18),
        ("ARC A770", 55), ("ARC A750", 50),
        ("RADEON RX 7900", 90), ("RADEON RX 7800", 80),
        ("RADEON RX 6900", 80), ("RADEON RX 6800", 70),
        ("RADEON RX 6700", 55), ("RADEON RX 6600", 42),
    ]
    for key, score in table:
        if key in n:
            return score
    if "INTEL" in n and "GRAPHICS" in n:
        return 10
    if "RADEON GRAPHICS" in n or "VEGA" in n:
        return 8
    return 30


# ---------------------------------------------------------------- rules
class Findings:
    def __init__(self):
        self.items = []

    def add(self, rid, sev, title, evidence, impact, action, confidence="中"):
        self.items.append({
            "id": rid,
            "severity": sev,
            "title": title,
            "evidence": evidence,
            "impact": impact,
            "action": action,
            "confidence": confidence,
        })

    def sorted(self):
        return sorted(self.items, key=lambda x: (SEVERITY_ORDER[x["severity"]], x["id"]))


def check_storage(data, f, profile):
    disks = as_list((data.get("disks") or {}).get("physical"))
    vols = as_list((data.get("disks") or {}).get("volumes"))
    smart = data.get("smart") or {}

    for v in vols:
        if not isinstance(v, dict):
            continue
        pct = num(v.get("free_pct"))
        drive = v.get("drive", "?")
        if pct is None:
            continue
        if pct < 5:
            f.add("STO-001", "P0", f"{drive} 空间即将耗尽",
                  f"剩余 {v.get('free_gb')}GB / 共 {v.get('size_gb')}GB（{pct}%）",
                  "系统盘写满会导致更新失败、临时文件写入失败，甚至整机卡死。",
                  "先清理或迁移数据；数据盘建议保持至少 15% 空闲。", "高")
        elif pct < 15:
            f.add("STO-002", "P2", f"{drive} 空间偏紧",
                  f"剩余 {v.get('free_gb')}GB / 共 {v.get('size_gb')}GB（{pct}%）",
                  "大文件渲染/回测写盘时可能触发空间不足。",
                  "清理或迁移；长期建议扩容。", "高")

    ev = (data.get("events") or {}).get("disk_errors") or {}
    if num(ev.get("count"), 0) > 0:
        by_id = "、".join(f"ID {x.get('id')}×{x.get('count')}" for x in as_list(ev.get("by_id")))
        f.add("STO-003", "P1", "磁盘 I/O 错误",
              f"近 30 天 {ev.get('count')} 条（{by_id}）",
              "可能是数据线松动、供电不足或硬盘本身老化；严重时会掉盘/卡死。",
              "换 SATA 线/供电口；用 CrystalDiskInfo 看 SMART；必要时备份后更换。", "高")

    if str(smart.get("status")) == "ok":
        for c in as_list(smart.get("counters")):
            wear = num(c.get("wear"))
            if wear is not None and wear >= 90:
                f.add("STO-004", "P0", f"{c.get('disk')} 寿命告急",
                      f"磨损度 {wear}%", "SSD 即将写满寿命，随时可能只读或掉盘。",
                      "立即备份，准备更换。", "高")
            elif wear is not None and wear >= 80:
                f.add("STO-004", "P1", f"{c.get('disk')} 磨损偏高",
                      f"磨损度 {wear}%", "剩余寿命有限。", "备份并计划更换。", "高")
            for key, label in (("read_uncorrected", "读取不可纠正错误"),
                               ("write_uncorrected", "写入不可纠正错误")):
                val = num(c.get(key))
                if val is not None and val > 0:
                    f.add("STO-005", "P1", f"{c.get('disk')} {label}",
                          f"{key}={int(val)}", "已发生数据损坏级错误。",
                          "立即备份并更换该盘。", "高")

    if disks:
        sys_disk = None
        for d in disks:
            if isinstance(d, dict) and str(d.get("health")) not in ("Healthy", "OK", "None"):
                f.add("STO-008", "P1", f"{d.get('friendly_name')} 健康状态异常",
                      f"health={d.get('health')} operational={d.get('operational')}",
                      "磁盘可能即将故障。", "备份并检查 SMART。", "高")
        # 系统盘是否为 SSD
        if not any(str(d.get("media_type", "")).upper() == "SSD" for d in disks if isinstance(d, dict)):
            f.add("STO-006", "P2", "系统盘不是 SSD",
                  "所有物理盘都不是 SSD", "开机与程序加载会明显偏慢。", "升级 NVMe/SATA SSD。", "高")


def check_memory(data, f, profile):
    mem = data.get("memory") or {}
    mods = [m for m in as_list(mem.get("modules")) if isinstance(m, dict)]
    if not mods:
        f.add("MEM-000", "P1", "未读到内存信息", "Win32_PhysicalMemory 为空",
              "无法判断容量与型号。", "确认 WMI 服务正常后重跑。", "低")
        return

    total = num(mem.get("total_gb"), 0)
    slots = num(mem.get("slots_total"))
    installed = len(mods)

    # 型号 vs 容量
    for m in mods:
        pn = m.get("part_number")
        cap = num(m.get("capacity_gb"))
        declared = samsung_pn_capacity(pn)
        if declared and cap and abs(declared - cap) > 0.5:
            f.add("MEM-001", "P1", "内存型号与容量不符（疑似改标条）",
                  f"{m.get('locator')}：型号 {pn} 按三星命名应为 {declared}GB，系统读到 {cap}GB",
                  "SPD 信息被改写过，来源不明；这类条子可能出现间歇性不稳定、插槽不认。",
                  "优先更换为型号与容量一致的正品；至少不要再与其它条混插。", "高")

    pns = {str(m.get("part_number")).strip() for m in mods if m.get("part_number")}
    if len(pns) > 1:
        f.add("MEM-002", "P2", "内存混插不同型号",
              "、".join(sorted(pns)),
              "不同型号/批次混插在 3200 及以上更容易出现训练不稳。",
              "尽量同型号成对；不稳定时把频率降到 2933 观察。", "中")

    if installed == 1 and (slots is None or slots >= 2):
        f.add("MEM-003", "P2", "单通道运行",
              f"只装了 {installed} 条，主板有 {int(slots) if slots else '?'} 个槽",
              "内存带宽减半，渲染/回测/多开会更慢。",
              "加装同型号内存组成双通道。", "高")

    for m in mods:
        rated = num(m.get("speed_mhz"))
        actual = num(m.get("configured_mhz"))
        if rated and actual and actual < rated:
            f.add("MEM-004", "P2", "内存未跑在额定频率",
                  f"{m.get('locator')}：额定 {int(rated)}，实际 {int(actual)}",
                  "可能是主板自动降频或混插保护。", "检查 BIOS 内存设置；混插时以较低者为准。", "中")
        volt = num(m.get("voltage_mv"))
        if volt and volt > 1250:
            f.add("MEM-005", "P2", "内存电压高于 JEDEC 标准",
                  f"{m.get('locator')}：{int(volt)} mV", "可能开了 XMP/手动超频。",
                  "不稳定时先关 XMP，回到 1.2V 标准。", "中")

    if total and total < 32 and profile in ("video", "quant"):
        sev = "P1" if total < 16 else "P2"
        f.add("MEM-006", sev, "内存容量偏小",
              f"{total:.0f}GB，画像={profile}", "视频渲染/量化回测容易吃满内存并触发换页。",
              "建议 32GB 起，重度视频可 64GB。", "高")
    elif total and total < 16:
        f.add("MEM-006", "P2", "内存容量偏小", f"{total:.0f}GB",
              "多开浏览器与 AI 工具时容易卡顿。", "建议 16GB 起，最好 32GB。", "中")

    if installed and slots and installed < slots:
        f.add("MEM-007", "P3", "还有空闲内存槽",
              f"已用 {installed}/{int(slots)}", "升级成本最低的一条路。",
              "加装同型号同频率内存。", "高")


def check_stability(data, f):
    ev = data.get("events") or {}
    p41 = ev.get("kernel_power_41") or {}
    n41 = int(num(p41.get("count"), 0) or 0)
    if n41 > 0:
        bugchecks = [x for x in as_list((ev.get("bugcheck") or {}).get("items"))]
        no_bug = len(bugchecks) == 0
        f.add("STB-001", "P1", "发生过非正常关机/硬挂",
              f"Kernel-Power 41 共 {n41} 次" + ("，且没有任何蓝屏记录" if no_bug else ""),
              ("整机直接失去响应或掉电，不是驱动蓝屏——常见于供电、主板、内存插槽接触、CPU 座。" if no_bug
               else "存在异常重启，需结合蓝屏代码定位。"),
              "先排除供电与接触问题；开启强制转储热键，下次死机时留 dump。", "高")

    bug = ev.get("bugcheck") or {}
    if int(num(bug.get("count"), 0) or 0) > 0:
        f.add("STB-002", "P1", "存在蓝屏记录",
              f"{bug.get('count')} 次",
              "蓝屏代码能直接指向驱动或硬件。", "用 BlueScreenView/WinDbg 读 dump 定位。", "高")

    whea = ev.get("whea") or {}
    if int(num(whea.get("fatal"), 0) or 0) > 0:
        f.add("STB-003", "P0", "致命 WHEA 硬件错误",
              f"{whea.get('fatal')} 条", "CPU/内存/PCIe 已报告不可纠正错误。",
              "立即备份；重点查内存、CPU 座、主板供电。", "高")
    elif int(num(whea.get("count"), 0) or 0) > 0:
        f.add("STB-004", "P2", "存在 WHEA 硬件事件",
              f"{whea.get('count')} 条（非致命）",
              "可能是固件启动记录，也可能是纠错事件。", "结合死机时间点核对；若同一时段反复出现则升级排查。", "低")

    tdr = ev.get("tdr") or {}
    if int(num(tdr.get("count"), 0) or 0) > 0:
        f.add("STB-005", "P2", "显卡驱动超时重置",
              f"{tdr.get('count')} 次", "画面卡顿/黑屏后恢复，通常是显卡驱动或供电问题。",
              "干净重装显卡驱动；仍复发则检查显卡供电。", "中")

    pnp = data.get("pnp_errors") or {}
    pnp_list = [p for p in as_list(pnp) if isinstance(p, dict)]
    if pnp_list:
        names = "、".join(str(p.get("name") or p.get("class") or "?") for p in pnp_list[:5])
        only_disabled = all("DISABLED" in str(p.get("problem", "")).upper() for p in pnp_list)
        f.add("STB-006", "P3" if only_disabled else "P2", "存在异常设备",
              f"{len(pnp_list)} 个：{names}",
              ("这些设备已被禁用，通常无害；若不需要可卸载。" if only_disabled
               else "驱动缺失或设备被禁用，可能影响功能或稳定性。"),
              "设备管理器逐项确认；无用设备可卸载，有用设备重装驱动。", "中")

    dumps = data.get("dumps") or {}
    if n41 > 0 and int(num(dumps.get("minidump_count"), 0) or 0) == 0 and not dumps.get("memory_dmp"):
        f.add("STB-007", "P2", "死机但没有任何转储",
              "无 minidump、无 MEMORY.DMP",
              "下次死机仍然无法定位。", "开启 CrashOnCtrlScroll，死机时按右 Ctrl+ScrollLock×2 强制留 dump。", "高")

    bios = data.get("bios") or {}
    bd = parse_date(bios.get("release_date"))
    if bd and (dt.datetime.now() - bd).days > 365 * 3:
        f.add("STB-008", "P2", "BIOS 较旧",
              f"{bios.get('version')}（{bios.get('release_date')}）",
              "新 CPU/内存兼容性与安全修复可能缺失。", "到主板官网查最新 BIOS，按官方步骤升级。", "中")

    gpu = primary_gpu(data.get("gpu"))
    if gpu:
        gd = parse_date(gpu.get("driver_date"))
        if gd and (dt.datetime.now() - gd).days > 365 * 2:
            f.add("STB-009", "P2", "显卡驱动较旧",
                  f"{gpu.get('name')} {gpu.get('driver_version')}（{gpu.get('driver_date')}）",
                  "新游戏/新软件可能异常或性能不佳。", "到 NVIDIA/AMD 官网更新驱动（选 Studio 或 Game Ready 按用途）。", "中")

    th = data.get("thermal") or {}
    hottest = None
    for z in as_list(th.get("acpi_zones")):
        t = num((z or {}).get("temperature_c"))
        if t and (hottest is None or t > hottest):
            hottest = t
    if hottest and hottest > 85:
        f.add("STB-010", "P1", "机箱内温度偏高",
              f"ACPI 温度 {hottest}°C", "长期高温会降频并缩短硬件寿命。",
              "清灰、检查风道与风扇曲线。", "中")

    vs = [v for v in as_list(data.get("vendor_software")) if isinstance(v, dict)]
    oc = [v.get("DisplayName") for v in vs if re.search(r"Afterburner|XTU|ThrottleStop|AI Suite|Armoury|Ryzen Master|EasyTune", str(v.get("DisplayName")), re.I)]
    if oc:
        f.add("STB-011", "P3", "检测到超频/调压工具",
              "、".join(oc), "可能与不稳定有关。", "排查期间先关闭或卸载，回到默认频率。", "中")

    md = as_list(ev.get("memory_diag"))
    for r in md:
        if not isinstance(r, dict) or not r.get("memory_mb"):
            continue
        bad = int(num(r.get("bad_pages"), 0) or 0)
        if bad > 0:
            f.add("STB-012", "P0", "内存诊断发现坏页",
                  f"{bad} 个坏页（{r.get('memory_mb')}MB，{r.get('time')}）",
                  "内存存在确定性错误。", "更换内存；若换条仍报错，查插槽/CPU 座。", "高")


def check_system(data, f, profile):
    osinfo = data.get("os") or {}
    cap = str(osinfo.get("caption") or "")
    if "Insider" in cap:
        f.add("SYS-001", "P2", "运行 Windows Insider 预览版",
              f"{cap} build {osinfo.get('build')}",
              "预览版本身可能有稳定性问题，会干扰硬件故障判断。",
              "排查期间可考虑回到正式版做对照。", "中")

    gpu = primary_gpu(data.get("gpu"))
    vram = num((gpu or {}).get("vram_gb_effective", (gpu or {}).get("vram_gb")))
    if gpu and vram is not None and vram < 6 and profile in ("video", "mixed"):
        f.add("PERF-001", "P2", "显存偏小",
              f"{gpu.get('name')} 约 {vram:.0f}GB", "4K 剪辑、降噪、AI 模型容易爆显存。",
              "视频/AI 用途建议 8GB 以上显存。", "中")

    cpu = data.get("cpu") or {}
    threads = int(num(cpu.get("threads"), 0) or 0)
    if threads and threads < 8 and profile in ("video", "quant"):
        f.add("PERF-002", "P2", "CPU 线程数偏少",
              f"{cpu.get('name')} / {threads} 线程", "渲染与批量计算会明显偏慢。",
              "按主板插槽升级更多核心的 CPU。", "中")


# ---------------------------------------------------------------- bottleneck
def score_cpu(cpu):
    threads = num(cpu.get("threads"), 4) or 4
    clock = num(cpu.get("max_clock_mhz"), 2500) or 2500
    return max(0, min(100, 0.6 * min(100, threads / 32 * 100) + 0.4 * min(100, clock / 5500 * 100)))


def score_ram(mem):
    total = num(mem.get("total_gb"), 8) or 8
    mods = as_list(mem.get("modules"))
    speed = max([num(m.get("configured_mhz"), 0) or 0 for m in mods if isinstance(m, dict)] or [0])
    channels = 1 if len(mods) < 2 else 2
    cap_score = min(100, total / 64 * 100)
    spd_score = min(100, speed / 6400 * 100)
    return max(0, min(100, (cap_score * 0.7 + spd_score * 0.3) * (0.75 if channels == 1 else 1.0)))


def score_gpu(gpu):
    if not gpu:
        return 10
    vram = num(gpu.get("vram_gb_effective", gpu.get("vram_gb")), 4) or 4
    return max(0, min(100, 0.6 * min(100, vram / 24 * 100) + 0.4 * gpu_tier(gpu.get("name"))))


def score_storage(disks, vols):
    best = 20
    for d in disks:
        if not isinstance(d, dict):
            continue
        mt = str(d.get("media_type", "")).upper()
        bus = str(d.get("bus_type", "")).upper()
        if mt == "SSD" and "NVME" in bus:
            best = max(best, 90)
        elif mt == "SSD":
            best = max(best, 60)
        else:
            best = max(best, 20)
    worst_free = min([num(v.get("free_pct"), 100) or 100 for v in vols if isinstance(v, dict)] or [100])
    if worst_free < 5:
        best -= 40
    elif worst_free < 15:
        best -= 20
    return max(0, min(100, best))


def score_thermal(data):
    th = data.get("thermal") or {}
    temps = [num((z or {}).get("temperature_c")) for z in as_list(th.get("acpi_zones"))]
    temps = [t for t in temps if t]
    if not temps:
        return 70
    return max(0, min(100, 100 - max(0, (max(temps) - 50) * 2)))


def bottlenecks(data, profile):
    cpu = data.get("cpu") or {}
    mem = data.get("memory") or {}
    disks = [d for d in as_list((data.get("disks") or {}).get("physical")) if isinstance(d, dict)]
    vols = [v for v in as_list((data.get("disks") or {}).get("volumes")) if isinstance(v, dict)]
    gpu = primary_gpu(data.get("gpu"))

    scores = {
        "cpu": score_cpu(cpu),
        "ram": score_ram(mem),
        "gpu": score_gpu(gpu),
        "storage": score_storage(disks, vols),
        "thermal": score_thermal(data),
    }
    weights = PROFILE_WEIGHTS.get(profile, PROFILE_WEIGHTS["mixed"])
    contrib = {k: (100 - v) * weights[k] for k, v in scores.items()}
    top = sorted(contrib.items(), key=lambda x: x[1], reverse=True)[:3]

    detail = {
        "cpu": f"{cpu.get('name', '未知')}，{int(num(cpu.get('threads'), 0) or 0)} 线程",
        "ram": f"{num(mem.get('total_gb'), 0):.0f}GB，{len(as_list(mem.get('modules')))} 条",
        "gpu": f"{(gpu or {}).get('name', '无独显')}，约 {num((gpu or {}).get('vram_gb_effective', (gpu or {}).get('vram_gb')), 0):.0f}GB 显存",
        "storage": " / ".join(f"{d.get('friendly_name')}({d.get('bus_type')})" for d in disks),
        "thermal": f"ACPI {max([num((z or {}).get('temperature_c'), 0) or 0 for z in as_list((data.get('thermal') or {}).get('acpi_zones'))] or [0]):.0f}°C",
    }
    upgrade = {
        "cpu": "按主板插槽与供电升级；先确认 BIOS 支持列表",
        "ram": "加装同型号同频率内存到双通道；容量优先于频率",
        "gpu": "按电源功率、机箱长度与用途选择；视频优先大显存",
        "storage": "系统盘换 NVMe；数据盘保持 ≥15% 空闲",
        "thermal": "清灰、检查风道与风扇曲线",
    }
    return [
        {
            "subsystem": k,
            "score": round(scores[k], 1),
            "deficit": round(100 - scores[k], 1),
            "weight": weights[k],
            "priority": round(contrib[k], 1),
            "detail": detail[k],
            "upgrade": upgrade[k],
        }
        for k, _ in top
    ], scores


# ---------------------------------------------------------------- render
def render_body(data, findings, bottles, scores, profile, generated):
    meta = data.get("meta") or {}
    osinfo = data.get("os") or {}
    cpu = data.get("cpu") or {}
    board = data.get("board") or {}
    bios = data.get("bios") or {}
    mem = data.get("memory") or {}
    gpu = primary_gpu(data.get("gpu")) or {}
    disks = [d for d in as_list((data.get("disks") or {}).get("physical")) if isinstance(d, dict)]
    vols = [v for v in as_list((data.get("disks") or {}).get("volumes")) if isinstance(v, dict)]
    usb = [u for u in as_list(data.get("usb")) if isinstance(u, dict)]
    monitors = [m for m in as_list(data.get("monitors")) if isinstance(m, dict)]
    thermal = data.get("thermal") or {}
    events = data.get("events") or {}
    dumps = data.get("dumps") or {}

    counts = {s: 0 for s in ("P0", "P1", "P2", "P3")}
    for x in findings:
        counts[x["severity"]] += 1

    if counts["P0"]:
        verdict = f"发现 {counts['P0']} 个高优先级问题，建议先处理数据与硬件风险，再谈性能优化。"
    elif counts["P1"]:
        verdict = f"发现 {counts['P1']} 个稳定性问题，硬件或配置需要进一步排查。"
    elif counts["P2"]:
        verdict = "没有致命问题，主要是性能与配置优化空间。"
    else:
        verdict = "未发现明显问题，配置状态良好。"

    def sev_badge(s):
        cls = {"P0": "p0", "P1": "p1", "P2": "p2", "P3": "p3"}[s]
        return f'<span class="badge {cls}">{s}</span>'

    cards = "".join(
        f'<div class="card {s.lower()}"><div class="n">{counts[s]}</div><div class="l">{SEVERITY_LABEL[s]}</div></div>'
        for s in ("P0", "P1", "P2", "P3")
    )

    # 硬件清单
    inv_rows = [
        ("系统", f"{esc(osinfo.get('caption'))} build {esc(osinfo.get('build'))}"),
        ("CPU", f"{esc(cpu.get('name'))}｜{esc(cpu.get('cores'))} 核 {esc(cpu.get('threads'))} 线程｜插槽 {esc(cpu.get('socket'))}"),
        ("主板", f"{esc(board.get('manufacturer'))} {esc(board.get('product'))}"),
        ("BIOS", f"{esc(bios.get('version'))}（{esc(bios.get('release_date'))}）"),
        ("内存", f"{num(mem.get('total_gb'), 0):.0f}GB / {len(as_list(mem.get('modules')))} 条 / {esc(mem.get('slots_total'))} 槽"),
        ("显卡", f"{esc(gpu.get('name'))}｜驱动 {esc(gpu.get('driver_version'))}（{esc(gpu.get('driver_date'))}）"),
        ("显示器", "；".join(f"{esc(m.get('manufacturer'))} {esc(m.get('model'))}" for m in monitors) or "未读到"),
        ("存储", "；".join(f"{esc(d.get('friendly_name'))} {esc(d.get('size_gb'))}GB {esc(d.get('bus_type'))}" for d in disks) or "未读到"),
        ("温度", f"ACPI {esc(max([num((z or {}).get('temperature_c'), 0) or 0 for z in as_list(thermal.get('acpi_zones'))] or [0]))}°C；{esc(thermal.get('nvidia_smi') or 'NVIDIA 未读到')}"),
    ]
    inv = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in inv_rows)

    mem_rows = "".join(
        f"<tr><td>{esc(m.get('locator'))}</td><td>{num(m.get('capacity_gb'), 0):.0f}GB</td>"
        f"<td>{esc(m.get('speed_mhz'))} / {esc(m.get('configured_mhz'))} MT/s</td>"
        f"<td>{esc(m.get('voltage_mv'))} mV</td><td>{esc(m.get('part_number'))}</td><td>{esc(m.get('manufacturer'))}</td></tr>"
        for m in as_list(mem.get("modules")) if isinstance(m, dict)
    ) or '<tr><td colspan="6">未读到</td></tr>'

    vol_rows = "".join(
        f"<tr><td>{esc(v.get('drive'))}</td><td>{esc(v.get('label'))}</td><td>{num(v.get('size_gb'), 0):.1f}GB</td>"
        f"<td>{num(v.get('free_gb'), 0):.1f}GB</td><td class=\"{'warn' if (num(v.get('free_pct'), 100) or 100) < 15 else ''}\">{esc(v.get('free_pct'))}%</td><td>{esc(v.get('health'))}</td></tr>"
        for v in vols
    ) or '<tr><td colspan="6">未读到</td></tr>'

    usb_rows = "".join(
        f"<tr><td>{esc(u.get('name'))}</td><td>{esc(u.get('class'))}</td><td>{esc(u.get('vid'))}:{esc(u.get('pid'))}</td><td>{esc(u.get('status'))}</td></tr>"
        for u in usb[:25]
    ) or '<tr><td colspan="4">未读到</td></tr>'

    findings_html = ""
    for sev in ("P0", "P1", "P2", "P3"):
        group = [x for x in findings if x["severity"] == sev]
        if not group:
            continue
        rows = "".join(
            f"<div class=\"finding {sev.lower()}\">"
            f"<div class=\"fh\">{sev_badge(sev)}<strong>{esc(x['title'])}</strong>"
            f"<span class=\"meta\">{esc(x['id'])} · 置信度 {esc(x['confidence'])}</span></div>"
            f"<div class=\"fl\"><b>证据：</b>{esc(x['evidence'])}</div>"
            f"<div class=\"fl\"><b>影响：</b>{esc(x['impact'])}</div>"
            f"<div class=\"fl\"><b>建议：</b>{esc(x['action'])}</div></div>"
            for x in group
        )
        findings_html += f"<h3>{SEVERITY_LABEL[sev]}（{len(group)}）</h3>{rows}"
    if not findings_html:
        findings_html = "<p>未发现需要处理的问题。</p>"

    b_rows = "".join(
        f"<tr><td>{ {'cpu':'CPU','ram':'内存','gpu':'显卡','storage':'存储','thermal':'散热'}[b['subsystem']] }</td>"
        f"<td>{b['score']}</td><td>{b['detail']}</td><td>{esc(b['upgrade'])}</td></tr>"
        for b in bottles
    )

    scale_rows = "".join(
        f"<tr><td>{ {'cpu':'CPU','ram':'内存','gpu':'显卡','storage':'存储','thermal':'散热'}[k] }</td><td>{v:.0f}</td>"
        f"<td><div class=\"bar\"><i style=\"width:{v:.0f}%\"></i></div></td></tr>"
        for k, v in scores.items()
    )

    ev_rows = [
        ("Kernel-Power 41", (events.get("kernel_power_41") or {}).get("count", 0)),
        ("蓝屏记录 1001", (events.get("bugcheck") or {}).get("count", 0)),
        ("意外关机 6008", (events.get("unexpected_shutdown") or {}).get("count", 0)),
        ("WHEA 硬件事件", (events.get("whea") or {}).get("count", 0)),
        ("磁盘错误", (events.get("disk_errors") or {}).get("count", 0)),
        ("显卡 TDR", (events.get("tdr") or {}).get("count", 0)),
        ("即插即用错误", (events.get("pnp") or {}).get("count", 0)),
    ]
    ev_html = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in ev_rows)
    dump_html = (
        f"minidump：{esc(dumps.get('minidump_count'))} 个；MEMORY.DMP：{'有' if dumps.get('memory_dmp') else '无'}；"
        f"LiveKernelReports：{esc(dumps.get('live_reports'))} 个"
    )

    return f"""
<header>
  <div class="eyebrow">Windows 硬件体检报告</div>
  <h1>{esc(meta.get('hostname'))}</h1>
  <div class="sub">采集时间 {esc(meta.get('collected_at'))} ｜ 画像 {esc(profile)} ｜ 工具 v{VERSION} ｜ {'管理员模式' if meta.get('elevated') else '普通权限'}</div>
</header>

<section class="verdict">
  <h2>结论</h2>
  <p class="lead">{esc(verdict)}</p>
  <div class="cards">{cards}</div>
</section>

<section>
  <h2>硬件清单</h2>
  <table class="kv">{inv}</table>
</section>

<section>
  <h2>内存明细</h2>
  <table><thead><tr><th>槽位</th><th>容量</th><th>额定/实际</th><th>电压</th><th>型号</th><th>厂商</th></tr></thead><tbody>{mem_rows}</tbody></table>
</section>

<section>
  <h2>磁盘与分区</h2>
  <table><thead><tr><th>盘符</th><th>卷标</th><th>容量</th><th>剩余</th><th>剩余比例</th><th>健康</th></tr></thead><tbody>{vol_rows}</tbody></table>
</section>

<section>
  <h2>问题清单</h2>
  {findings_html}
</section>

<section>
  <h2>瓶颈分析（{esc(profile)} 画像）</h2>
  <p class="note">评分为启发式相对分，用于排序，不代表绝对性能。分数越低，越可能成为瓶颈。</p>
  <table><thead><tr><th>子系统</th><th>得分</th><th>依据</th><th>升级方向</th></tr></thead><tbody>{b_rows}</tbody></table>
  <h3>各子系统得分</h3>
  <table class="bars"><tbody>{scale_rows}</tbody></table>
</section>

<section>
  <h2>故障证据</h2>
  <table class="kv">{ev_html}</table>
  <p class="note">{dump_html}</p>
</section>

<section>
  <h2>USB 与外设</h2>
  <table><thead><tr><th>设备</th><th>类别</th><th>VID:PID</th><th>状态</th></tr></thead><tbody>{usb_rows}</tbody></table>
</section>

<footer>
  <p>本报告为只读采集结果，未修改系统任何设置。升级/更换建议需结合预算与保修自行确认。</p>
  <p>生成时间 {esc(generated)} ｜ windows-hardware-audit v{VERSION}</p>
</footer>
"""


TEMPLATE_DEFAULT = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
<style>
  :root{--bg:#0b1220;--panel:#121a2b;--line:#24314a;--text:#e6edf3;--muted:#8b98ab;
        --p0:#ff6b6b;--p1:#ffa94d;--p2:#4dabf7;--p3:#8b98ab;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);font:15px/1.65 "Segoe UI","Microsoft YaHei",system-ui,sans-serif}
  .wrap{max-width:1080px;margin:0 auto;padding:32px 20px 80px}
  header{border-bottom:1px solid var(--line);padding-bottom:20px;margin-bottom:24px}
  .eyebrow{color:var(--muted);letter-spacing:.18em;font-size:12px;text-transform:uppercase}
  h1{font-size:30px;margin:6px 0 4px}
  .sub{color:var(--muted);font-size:13px}
  section{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px 22px;margin:18px 0}
  h2{font-size:19px;margin:0 0 14px}
  h3{font-size:15px;margin:18px 0 10px;color:#c9d4e3}
  table{width:100%;border-collapse:collapse;font-size:14px}
  th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
  th{color:var(--muted);font-weight:600;white-space:nowrap}
  table.kv th{width:120px}
  .warn{color:var(--p1);font-weight:600}
  .cards{display:flex;gap:12px;flex-wrap:wrap;margin-top:16px}
  .card{flex:1 1 140px;border-radius:10px;padding:12px 14px;background:#0f1728;border:1px solid var(--line)}
  .card .n{font-size:26px;font-weight:700}
  .card .l{color:var(--muted);font-size:12px}
  .card.p0 .n{color:var(--p0)} .card.p1 .n{color:var(--p1)}
  .card.p2 .n{color:var(--p2)} .card.p3 .n{color:#c9d4e3}
  .lead{font-size:16px;margin:0}
  .badge{display:inline-block;padding:1px 7px;border-radius:999px;font-size:12px;font-weight:700;color:#0b1220;margin-right:8px}
  .badge.p0{background:var(--p0)} .badge.p1{background:var(--p1)}
  .badge.p2{background:var(--p2)} .badge.p3{background:var(--p3)}
  .finding{border-left:3px solid var(--line);padding:10px 0 10px 14px;margin:10px 0}
  .finding.p0{border-color:var(--p0)} .finding.p1{border-color:var(--p1)}
  .finding.p2{border-color:var(--p2)} .finding.p3{border-color:var(--p3)}
  .fh{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
  .meta{color:var(--muted);font-size:12px;margin-left:auto}
  .fl{font-size:14px;color:#c9d4e3}
  .fl b{color:var(--muted);font-weight:600}
  .note{color:var(--muted);font-size:13px}
  .bar{background:#0f1728;border-radius:6px;height:10px;overflow:hidden;min-width:160px}
  .bar i{display:block;height:100%;background:linear-gradient(90deg,#4dabf7,#51cf66)}
  table.bars td:first-child{width:90px}
  footer{color:var(--muted);font-size:12px;text-align:center;margin-top:30px}
  @media(max-width:640px){h1{font-size:24px}section{padding:16px}}
</style>
</head>
<body><div class="wrap">
{{BODY}}
</div></body></html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="collect.ps1 生成的 JSON")
    ap.add_argument("--profile", default="mixed", choices=["video", "quant", "office", "mixed"])
    ap.add_argument("--out-html", default=None)
    ap.add_argument("--out-json", default=None)
    ap.add_argument("--template", default=None)
    args = ap.parse_args()

    src = Path(args.input)
    if not src.exists():
        print(f"ERROR: 找不到输入文件 {src}", file=sys.stderr)
        return 2
    data = json.loads(src.read_text(encoding="utf-8-sig"))
    apply_effective_vram(data)

    f = Findings()
    check_storage(data, f, args.profile)
    check_memory(data, f, args.profile)
    check_stability(data, f)
    check_system(data, f, args.profile)

    findings = f.sorted()
    bottles, scores = bottlenecks(data, args.profile)

    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = render_body(data, findings, bottles, scores, args.profile, now)

    tpl_path = Path(args.template) if args.template else Path(__file__).with_name("report_template.html")
    tpl = tpl_path.read_text(encoding="utf-8") if tpl_path.exists() else TEMPLATE_DEFAULT
    hostname = (data.get("meta") or {}).get("hostname", "PC")
    page = (tpl.replace("{{TITLE}}", f"硬件体检报告 · {hostname}")
               .replace("{{BODY}}", body)
               .replace("{{GENERATED}}", now)
               .replace("{{HOST}}", hostname))

    stem = src.with_suffix("")
    out_json = Path(args.out_json) if args.out_json else stem.with_suffix(".findings.json")
    out_html = Path(args.out_html) if args.out_html else stem.with_suffix(".html")

    payload = {
        "tool": "windows-hardware-audit",
        "version": VERSION,
        "generated_at": now,
        "profile": args.profile,
        "summary": {
            "counts": {s: sum(1 for x in findings if x["severity"] == s) for s in ("P0", "P1", "P2", "P3")},
            "verdict": "P0" if any(x["severity"] == "P0" for x in findings)
                       else "P1" if any(x["severity"] == "P1" for x in findings)
                       else "OK",
        },
        "findings": findings,
        "bottlenecks": bottles,
        "scores": {k: round(v, 1) for k, v in scores.items()},
    }
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    out_html.write_text(page, encoding="utf-8")

    c = payload["summary"]["counts"]
    print(f"报告: {out_html}")
    print(f"数据: {out_json}")
    print(f"P0={c['P0']} P1={c['P1']} P2={c['P2']} P3={c['P3']}")
    for x in findings:
        if x["severity"] in ("P0", "P1"):
            print(f"  [{x['severity']}] {x['title']} — {x['evidence']}")
    print("瓶颈 Top3: " + "；".join(f"{b['subsystem']}({b['score']})" for b in bottles))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
