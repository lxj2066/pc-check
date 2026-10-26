# 采集矩阵（probe-matrix）

每类硬件采什么、用什么命令、要不要管理员。**默认全部只读。**

## 1. 采集项总表

| 域 | 数据源 | 需要管理员 | 关键字段 |
|---|---|---|---|
| 操作系统 | `Win32_OperatingSystem` | 否 | Caption、Build、LastBootUpTime、可见内存 |
| CPU | `Win32_Processor` | 否 | Name、Cores、Threads、SocketDesignation、MaxClockSpeed |
| 主板 | `Win32_BaseBoard` | 否 | Manufacturer、Product、SerialNumber |
| BIOS | `Win32_BIOS` | 否 | SMBIOSBIOSVersion、ReleaseDate |
| 内存条 | `Win32_PhysicalMemory` | 否 | DeviceLocator、Capacity、Speed、ConfiguredClockSpeed、ConfiguredVoltage、PartNumber、Manufacturer、SerialNumber |
| 内存槽总数 | `Win32_PhysicalMemoryArray` | 否 | MemoryDevices |
| 显卡 | `Win32_VideoController` | 否 | Name、DriverVersion、DriverDate、AdapterRAM、CurrentHorizontalResolution、CurrentRefreshRate |
| 显示器 | `root\wmi` → `WmiMonitorID` | 否（个别机型拒绝） | ManufacturerName、UserFriendlyName、SerialNumberID、Year/Week |
| 物理磁盘 | `Get-PhysicalDisk` / `Win32_DiskDrive` | 否 | FriendlyName、MediaType、BusType、Size、HealthStatus、FirmwareRevision |
| 卷 | `Get-Volume` | 否 | DriveLetter、Size、SizeRemaining、HealthStatus |
| SMART | `Get-PhysicalDisk \| Get-StorageReliabilityCounter` | **是** | Wear、Temperature、PowerOnHours、Read/WriteErrorsUncorrected |
| ATA SMART 原始属性 | `root\wmi MSStorageDriver_FailurePredictData` | **是** | 05/C5/C6/C7 等 |
| 异常设备 | `Get-PnpDevice -PresentOnly` | 否 | Status、Problem、Class |
| USB 外设 | `Get-PnpDevice -PresentOnly` 过滤 `USB\*` | 否 | FriendlyName、Class、VID/PID、Status |
| 网络 | `Win32_NetworkAdapter`（PhysicalAdapter） | 否 | Name、MACAddress、Speed、DriverVersion |
| 音频 | `Win32_SoundDevice` | 否 | Name、Status |
| 电池 | `Win32_Battery` | 否 | Name、EstimatedChargeRemaining（设计容量需 WMI 电池类，通常要管理员） |
| 温度 | `Win32_PerfFormattedData_Counters_ThermalZoneInformation` | 否 | Temperature（开尔文） |
| 显卡温度 | `nvidia-smi --query-gpu=...` | 否 | temperature.gpu、memory.total、memory.used |
| 驱动 | `Win32_PnPSignedDriver` | 否 | DeviceName、DriverVersion、DriverDate、DriverProviderName |
| 厂商工具 | 卸载注册表 + 进程 | 否 | Armoury、MSI Center、XTU、Afterburner、Magician 等 |

## 2. 崩溃与故障证据

| 证据 | 数据源 | 需要管理员 | 说明 |
|---|---|---|---|
| Kernel-Power 41 | System 日志，ID 41 | 否 | 取 EventData 的 BugcheckCode / PowerButtonTimestamp |
| 蓝屏 | ID 1001（BugCheck 来源） | 否 | 无 1001 说明没走蓝屏流程 |
| 意外关机 | ID 6008 | 否 | 上次关机是否正常 |
| WHEA | 提供程序 `Microsoft-Windows-WHEA-Logger` | 否 | 1=致命，3/17/19/47=纠正类 |
| 磁盘错误 | 提供程序 `disk`，ID 7/11/51/153/157 | 否 | 按 ID 聚合 |
| 显卡 TDR | ID 4101/4102 | 否 | 驱动超时重置 |
| 即插即用 | ID 219/411/442 | 否 | 驱动加载/启动/移除失败 |
| 内存诊断 | `Microsoft-Windows-MemoryDiagnostics-Results` | 否 | 读 CompletionType / NumBadPages / MemorySize |
| 转储 | `C:\Windows\Minidump`、`MEMORY.DMP`、`LiveKernelReports` | 否 | 存在性与时间 |

## 3. 明确不做的事

- 不读 PSU 型号（软件层拿不到，写 UNKNOWN）
- 不读主板 VRM 温度（无标准接口）
- 不读机箱风扇转速（除主板 EC 暴露）
- 不读硬盘 S.M.A.R.T. 之外的物理参数
- 不执行任何 `chkdsk`、`sfc`、驱动安装、BIOS 刷写

## 4. 提权边界

`-Deep` 只多做三类事：SMART、ATA 原始属性、可选的 CPU-Z SPD 报告。

提权前必须说清：

1. 要读什么（SMART 磨损/温度/通电时长）
2. 为什么普通权限读不到（Windows 保护存储类 CIM）
3. 不做什么（不改任何设置）

## 5. 采集失败的处理

| 现象 | 处理 |
|---|---|
| 某个 CIM 类返回空 | 写 `null`，在 `errors[]` 记录原因 |
| `Get-StorageReliabilityCounter` 拒绝访问 | `smart.status = unavailable`，提示需 `-Deep` |
| 事件日志读取异常 | `events.*.count = 0` 且注明「日志不可读」，不要当成「没有故障」 |
| 显示器 EDID 读不到 | `monitors = []`，不推断型号 |
| 无独显 / 笔记本 | GPU 项正常为空，瓶颈模型自动降权 |
