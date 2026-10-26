# PC Check — Windows 硬件体检

**只读的 Windows 硬件体检：采集 → 判读 → 瓶颈 → 升级建议。**

不修改系统任何设置，只拿证据，然后给一份带优先级的报告。

## 做什么

| 步骤 | 内容 |
|---|---|
| 采集 | CPU、主板、BIOS、内存条、显卡、显示器、磁盘、分区、USB、网络、音频、电池、温度、驱动、厂商工具、事件日志、转储、内存诊断结果 |
| 判读 | 约 25 条确定性规则：故障、型号不匹配、劣化 |
| 评分 | 按用途画像（视频/量化/办公/混合）加权的瓶颈模型 |
| 报告 | 自包含 HTML + JSON 基线 + 聊天摘要 |

## 能查出什么

- **无蓝屏的硬挂**：Kernel-Power 41 且 BugcheckCode=0、无 minidump
- **磁盘故障**：SMART 磨损、不可纠正错误、disk 事件 51/153/157
- **改标内存**：型号与容量/rank 自相矛盾
- **混插**：不同型号、rank、频率
- **超频痕迹**：实际频率 vs 额定频率、电压高于 JEDEC
- **空间风险**：低于 5% 判 P0，低于 15% 判 P2
- **驱动/BIOS 过旧**：按日期判断
- **异常设备**：过滤幽灵设备后只剩真实问题
- **温度压力**：ACPI 温区 + nvidia-smi

## 依赖

- Windows 10/11 + PowerShell 5.1+
- Python 3.9+（仅分析用；采集不需要）
- 管理员权限**仅** `-Deep` 读取 SMART 时需要

## 安装

```bash
git clone https://github.com/lxj2066/pc-check.git
cp -r pc-check/skills/windows-hardware-audit ~/.claude/skills/
```

## 使用

```powershell
powershell -NoProfile -ExecutionPolicy RemoteSigned -File scripts\collect.ps1
powershell -NoProfile -ExecutionPolicy RemoteSigned -File scripts\collect.ps1 -Deep
python scripts\analyze.py out\hardware_audit_HOST_TS.json --profile mixed
```

画像：`video` / `quant` / `office` / `mixed`。

## 安全边界

- 默认纯只读：不改注册表、不装软件、不动驱动、不清垃圾
- `-Deep` 只读 SMART，提权前会说明原因
- 所有建议都只是建议，执行需要另行确认
- 读不到的字段写 `UNKNOWN`，不猜

## 局限

- 电源型号与功率软件读不到，永远写 `UNKNOWN`
- 消费级 SATA/NVMe 的 SMART 字段厂商之间不一致
- 内存 rank 可能需要 SPD 访问（v0.2 计划接入 CPU-Z）
- 瓶颈分是相对启发式，用于排序，不是绝对跑分
- 不报价、不推荐店铺、不承诺"换了就一定好"

## 许可

[PolyForm Noncommercial License 1.0.0](LICENSE)。商用需单独授权，见 [COMMERCIAL.md](COMMERCIAL.md)。

作者：李献杰 Lyon
