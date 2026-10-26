# 故障签名与事件判读（fault-signatures）

## 1. 先分形态：蓝屏 vs 硬挂 vs 掉电

| 形态 | 证据 | 常见原因 |
|---|---|---|
| **蓝屏（bugcheck）** | Event 1001 + minidump/MEMORY.DMP + BugcheckCode≠0 | 驱动、内存、系统文件 |
| **硬挂（完全卡死）** | **无 1001、无 dump**，Kernel-Power 41 BugcheckCode=0，PowerButtonTimestamp=0 | 供电、主板、CPU 座、内存插槽接触、存储/PCIe 设备挂死 |
| **长按电源关机** | 41 + PowerButtonTimestamp 有值 | 用户手动断电（说明当时已完全无响应） |
| **瞬时掉电** | 41 + 无任何前置日志 | PSU、市电、主板供电 |
| **睡眠唤醒失败** | 41 + SleepInProgress≠0 | 驱动/固件睡眠问题 |

**结论优先级**：没有 1001 和 dump 时，不要下「内存坏了」的结论；先按硬挂查供电与接触。

## 2. 关键事件 ID

| 来源 | ID | 含义 | 处理 |
|---|---|---|---|
| Kernel-Power | 41 | 未正常关机 | 看 BugcheckCode 与 PowerButtonTimestamp |
| Kernel-Power | 42 | 进入睡眠 | 与唤醒失败交叉核对 |
| WER-SystemErrorReporting | 1001 | 蓝屏记录 | 读 BugcheckCode 和参数 |
| EventLog | 6008 | 上次关机异常 | 时间点用于对齐 |
| EventLog | 6005/6006 | 日志服务启动/停止 | 用于重建重启时间线 |
| User32 | 1074 | 有人/程序发起关机 | 判断是否计划内 |
| WHEA-Logger | 1 | **致命硬件错误** | P0：CPU/内存/PCIe 不可纠正 |
| WHEA-Logger | 3 | 硬件事件（多为信息级） | 结合时间点；单独一条通常无指向性 |
| WHEA-Logger | 17/18/19 | 已纠正机器检查 | 反复出现才升级排查 |
| WHEA-Logger | 47 | 已纠正 PCIe 错误 | 查显卡/转接卡/延长线 |
| disk | 7 | 坏块 | 备份，查 SMART |
| disk | 11 | 控制器错误 | 数据线/供电/硬盘 |
| disk | 51 | 分页操作出错 | 常见于 USB 盘、掉线盘 |
| disk | 153 | I/O 重试 | 盘或链路不稳 |
| disk | 157 | 磁盘被意外移除 | 线材/供电/热插拔 |
| stornvme | 129 | NVMe 复位/超时 | 固件、散热、PCIe 链路 |
| Display | 4101/4102 | 显卡驱动超时重置（TDR） | 驱动重装，仍复发查供电 |
| Kernel-PnP | 219 | 驱动加载失败 | 设备驱动缺失 |
| Kernel-PnP | 411 | 设备启动失败 | 驱动/固件 |
| Kernel-PnP | 442 | 设备移除失败 | 热插拔/驱动 |

## 3. SMART 阈值

| 属性 | 含义 | 关注阈值 |
|---|---|---|
| 05 Reallocated Sectors | 已重映射扇区 | **>0 就记录**；持续增长 = 盘在坏 |
| C5 Current Pending | 待映射扇区 | **>0 立即备份** |
| C6 Uncorrectable | 不可纠正扇区 | **>0 立即备份/更换** |
| C7 UDMA CRC | 数据线传输错误 | >0 先换 SATA 线，再复测 |
| 09 Power-On Hours | 通电小时 | 参考寿命，不是故障 |
| C2 Temperature | 温度 | 机械盘 >50°C、SSD >70°C 需改善散热 |
| NVMe Percentage Used | 寿命消耗 | ≥80% 计划更换，≥90% P0 |
| NVMe Media Errors | 介质错误 | >0 记录并备份 |
| NVMe Unsafe Shutdowns | 异常断电次数 | 与 41 事件交叉核对 |

> 消费级 SATA/NVMe 的 SMART 厂商实现不同，**不同字段名要按厂商解释**，不要硬套。

## 4. PnP 问题码（设备管理器）

| 码 | 含义 | 处理 |
|---|---|---|
| 1 | 未配置 | 装驱动 |
| 3 | 驱动加载失败 | 重装/换版本 |
| 10 | 无法启动 | 查资源冲突/驱动 |
| 12 | 资源不足 | 关闭冲突设备 |
| 18 | 需重装驱动 | 卸载后重扫 |
| 19 | 注册表损坏 | 卸载设备后重扫 |
| 22 | **已禁用** | 无害；不用就卸载 |
| 28 | 未安装驱动 | 装官方驱动 |
| 43 | 驱动报告设备失败 | 硬件或驱动故障 |
| 45 | 当前未连接 | 幽灵设备，忽略 |

采集脚本已过滤 `CM_PROB_PHANTOM`，不要把它当故障。

## 5. 内存诊断怎么读

事件 `Microsoft-Windows-MemoryDiagnostics-Results` 的 XML 里：

| 字段 | 含义 |
|---|---|
| `CompletionType` | `Pass` / `Fail` |
| `MemorySize` | 本次实际测试的 MB（**只测了插着的条**） |
| `NumBadPages` | 坏页数，>0 即不合格 |
| `TestCount` | 测试项数（标准档通常 12） |
| `TestDuration` | 秒 |

**注意**：通过 ≠ 内存绝对没问题。标准档强度有限，抓不到边际时序和接触问题。

## 6. 下次死机怎么留证据

```powershell
reg add "HKLM\SYSTEM\CurrentControlSet\Services\i8042prt\Parameters" /v CrashOnCtrlScroll /t REG_DWORD /d 1 /f
```

重启后，卡死时按 **右 Ctrl + ScrollLock 两次** → 强制蓝屏 0xE2 并生成 dump。

回滚：

```powershell
reg delete "HKLM\SYSTEM\CurrentControlSet\Services\i8042prt\Parameters" /v CrashOnCtrlScroll /f
```

> 完全硬挂（键盘都无响应）时热键可能无效，此时只能长按电源。

## 7. 读 dump 的工具

| 工具 | 用途 |
|---|---|
| WinDbg（微软商店） | `!analyze -v`，最权威 |
| BlueScreenView | 快速看 bugcheck 代码与嫌疑驱动 |
| WhoCrashed | 同上，界面更简单 |

## 8. USB 外设故障

| 现象 | 先查 |
|---|---|
| 键鼠间歇失灵 | 换 USB 口（避开前置面板）、换线、查 `disk 51/157` 是否指向该设备 |
| U 盘/移动盘掉线 | 供电不足（用带供电 Hub）、数据线、`CM_PROB_*` |
| 设备认不出 | VID/PID 是否被识别、是否需要厂商驱动 |
| 大量 51 分页错误 | 该 USB 存储被系统当成分页/缓存盘，拔掉观察 |

**不要**用第三方「USB 修复工具」；先换口、换线、看事件日志。
