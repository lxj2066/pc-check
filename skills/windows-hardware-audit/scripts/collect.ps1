#Requires -Version 5.1
<#
.SYNOPSIS
  Windows 硬件体检采集器（只读）
.DESCRIPTION
  采集 CPU / 主板 / BIOS / 内存 / 显卡 / 显示器 / 磁盘 / USB / 网络 / 音频 / 电池 /
  温度 / 驱动 / 厂商软件 / 崩溃事件 / 转储 / 内存诊断结果，输出单个 JSON。
  默认 -ScanOnly 无需管理员；-Deep 会尝试读取 SMART（需要管理员）。
.EXAMPLE
  powershell -NoProfile -ExecutionPolicy RemoteSigned -File scripts\collect.ps1
  powershell -NoProfile -ExecutionPolicy RemoteSigned -File scripts\collect.ps1 -Deep
#>
param(
    [switch]$Deep,
    [string]$OutFile,
    [string]$Profile = "mixed"
)

$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$script:Errors = New-Object System.Collections.ArrayList

function Add-Err([string]$where, [string]$msg) {
    [void]$script:Errors.Add([ordered]@{ area = $where; message = $msg })
}

function Safe([string]$area, [scriptblock]$block) {
    try { return & $block }
    catch { Add-Err $area $_.Exception.Message; return $null }
}

function CimDate([string]$s) {
    if (-not $s) { return $null }
    if ($s.Length -ge 8 -and $s -match '^\d{8}') {
        return ($s.Substring(0,4) + '-' + $s.Substring(4,2) + '-' + $s.Substring(6,2))
    }
    return $s
}

function ToGB($bytes) {
    if ($null -eq $bytes) { return $null }
    return [math]::Round([double]$bytes / 1GB, 2)
}

function Get-EventList {
    param([int[]]$Ids, [int]$Days = 30, [int]$Max = 50)
    $start = (Get-Date).AddDays(-$Days)
    $filter = @{ LogName = 'System'; StartTime = $start }
    if ($Ids) { $filter['Id'] = $Ids }
    try {
        return @(Get-WinEvent -FilterHashtable $filter -MaxEvents $Max -ErrorAction Stop)
    } catch { return @() }
}

function Get-EventData($evt) {
    $d = [ordered]@{}
    try {
        $xml = [xml]$evt.ToXml()
        foreach ($n in $xml.Event.EventData.Data) { $d[$n.Name] = $n.'#text' }
    } catch { }
    return $d
}

$isAdmin = $false
try {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
} catch { }

$result = [ordered]@{}
$result['meta'] = [ordered]@{
    hostname      = $env:COMPUTERNAME
    collected_at  = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    elevated      = $isAdmin
    deep          = [bool]$Deep
    profile       = $Profile
    ps_version    = $PSVersionTable.PSVersion.ToString()
    script_version = '0.1.0'
}

# ---------- OS ----------
$result['os'] = Safe 'os' {
    $os = Get-CimInstance Win32_OperatingSystem
    [ordered]@{
        caption        = $os.Caption
        version        = $os.Version
        build          = $os.BuildNumber
        install_date   = CimDate $os.InstallDate.ToString('yyyyMMdd')
        last_boot      = $os.LastBootUpTime.ToString('yyyy-MM-dd HH:mm:ss')
        uptime_hours   = [math]::Round(((Get-Date) - $os.LastBootUpTime).TotalHours, 1)
        total_ram_gb   = ToGB ($os.TotalVisibleMemorySize * 1KB)
        free_ram_gb    = ToGB ($os.FreePhysicalMemory * 1KB)
    }
}

# ---------- CPU ----------
$result['cpu'] = Safe 'cpu' {
    $c = Get-CimInstance Win32_Processor | Select-Object -First 1
    [ordered]@{
        name            = $c.Name.Trim()
        manufacturer    = $c.Manufacturer
        socket          = $c.SocketDesignation
        cores           = $c.NumberOfCores
        threads         = $c.NumberOfLogicalProcessors
        max_clock_mhz   = $c.MaxClockSpeed
        current_clock_mhz = $c.CurrentClockSpeed
        l2_cache_kb     = $c.L2CacheSize
        l3_cache_kb     = $c.L3CacheSize
    }
}

# ---------- Board / BIOS ----------
$result['board'] = Safe 'board' {
    $b = Get-CimInstance Win32_BaseBoard
    [ordered]@{ manufacturer = $b.Manufacturer; product = $b.Product; version = $b.Version; serial = $b.SerialNumber }
}
$result['bios'] = Safe 'bios' {
    $b = Get-CimInstance Win32_BIOS
    [ordered]@{ manufacturer = $b.Manufacturer; version = $b.SMBIOSBIOSVersion; release_date = if ($b.ReleaseDate -is [datetime]) { $b.ReleaseDate.ToString('yyyy-MM-dd') } else { CimDate $b.ReleaseDate }; serial = $b.SerialNumber }
}

# ---------- Memory ----------
$result['memory'] = Safe 'memory' {
    $mods = @(Get-CimInstance Win32_PhysicalMemory | ForEach-Object {
        [ordered]@{
            locator           = $_.DeviceLocator
            bank              = $_.BankLabel
            capacity_gb       = ToGB $_.Capacity
            speed_mhz         = $_.Speed
            configured_mhz    = $_.ConfiguredClockSpeed
            voltage_mv        = $_.ConfiguredVoltage
            part_number       = if ($_.PartNumber) { $_.PartNumber.Trim() } else { $null }
            serial            = $_.SerialNumber
            manufacturer      = $_.Manufacturer
            form_factor       = $_.FormFactor
            smbios_type       = $_.SMBIOSMemoryType
            data_width        = $_.DataWidth
        }
    })
    $arr = Get-CimInstance Win32_PhysicalMemoryArray | Select-Object -First 1
    $totalGb = 0
    foreach ($m in $mods) { if ($m.capacity_gb) { $totalGb += [double]$m.capacity_gb } }
    [ordered]@{
        modules_installed = $mods.Count
        slots_total       = $arr.MemoryDevices
        total_gb          = [math]::Round($totalGb, 2)
        modules           = $mods
    }
}

# ---------- GPU / Monitors ----------
$result['gpu'] = Safe 'gpu' {
    @(Get-CimInstance Win32_VideoController | ForEach-Object {
        [ordered]@{
            name              = $_.Name
            driver_version    = $_.DriverVersion
            driver_date       = CimDate $_.DriverDate
            vram_gb           = if ($_.AdapterRAM -and $_.AdapterRAM -gt 0) { ToGB $_.AdapterRAM } else { $null }
            status            = $_.Status
            current_width     = $_.CurrentHorizontalResolution
            current_height    = $_.CurrentVerticalResolution
            refresh_hz        = $_.CurrentRefreshRate
            video_processor   = $_.VideoProcessor
        }
    })
}
$result['monitors'] = Safe 'monitors' {
    @(Get-CimInstance -Namespace root\wmi -ClassName WmiMonitorID | ForEach-Object {
        [ordered]@{
            manufacturer = (-join [char[]]($_.ManufacturerName | Where-Object { $_ -ne 0 }))
            model        = (-join [char[]]($_.UserFriendlyName | Where-Object { $_ -ne 0 }))
            serial       = (-join [char[]]($_.SerialNumberID | Where-Object { $_ -ne 0 }))
            year         = $_.YearOfManufacture
            week         = $_.WeekOfManufacture
            instance     = $_.InstanceName
        }
    })
}

# ---------- Disks / Volumes ----------
$result['disks'] = Safe 'disks' {
    $phys = @(Get-PhysicalDisk | ForEach-Object {
        $d = $_
        [ordered]@{
            number          = $d.DeviceId
            friendly_name   = $d.FriendlyName
            serial          = $d.SerialNumber
            media_type      = "$($d.MediaType)"
            bus_type        = "$($d.BusType)"
            size_gb         = ToGB $d.Size
            health          = "$($d.HealthStatus)"
            operational     = "$($d.OperationalStatus)"
            firmware        = (Get-CimInstance Win32_DiskDrive | Where-Object { [int]$_.Index -eq [int]$d.DeviceId } | Select-Object -ExpandProperty FirmwareRevision -ErrorAction SilentlyContinue)
        }
    })
    $vols = @(Get-Volume | Where-Object DriveLetter | ForEach-Object {
        [ordered]@{
            drive       = "$($_.DriveLetter):"
            label       = $_.FileSystemLabel
            fs          = $_.FileSystem
            size_gb     = ToGB $_.Size
            free_gb     = ToGB $_.SizeRemaining
            free_pct    = if ($_.Size -gt 0) { [math]::Round(($_.SizeRemaining / $_.Size) * 100, 1) } else { $null }
            health      = "$($_.HealthStatus)"
        }
    })
    [ordered]@{ physical = $phys; volumes = $vols }
}

$result['smart'] = Safe 'smart' {
    if (-not $Deep) { return [ordered]@{ status = 'skipped'; reason = 'use -Deep (requires admin)' } }
    if (-not $isAdmin) { return [ordered]@{ status = 'unavailable'; reason = 'not elevated' } }
    $out = @()
    foreach ($d in Get-PhysicalDisk) {
        try {
            $c = $d | Get-StorageReliabilityCounter
            $out += [ordered]@{
                disk            = $d.FriendlyName
                wear            = $c.Wear
                temperature_c   = $c.Temperature
                power_on_hours  = $c.PowerOnHours
                read_errors     = $c.ReadErrorsTotal
                write_errors    = $c.WriteErrorsTotal
                read_uncorrected  = $c.ReadErrorsUncorrected
                write_uncorrected = $c.WriteErrorsUncorrected
            }
        } catch { $out += [ordered]@{ disk = $d.FriendlyName; error = $_.Exception.Message } }
    }
    [ordered]@{ status = 'ok'; counters = $out }
}

# ---------- PnP / USB / Network / Audio / Battery ----------
$result['pnp_errors'] = Safe 'pnp' {
    @(Get-PnpDevice -PresentOnly | Where-Object {
        $_.Status -eq 'Error' -or
        ($_.Problem -and $_.Problem -ne 'CM_PROB_NONE' -and $_.Problem -ne 'CM_PROB_PHANTOM')
    } | ForEach-Object {
        [ordered]@{
            name        = $_.FriendlyName
            instance_id = $_.InstanceId
            class       = $_.Class
            status      = "$($_.Status)"
            problem     = "$($_.Problem)"
        }
    })
}
$result['usb'] = Safe 'usb' {
    @(Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like 'USB\*' } | ForEach-Object {
        $vid = $null; $pid2 = $null
        if ($_.InstanceId -match 'VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})') { $vid = $Matches[1].ToUpper(); $pid2 = $Matches[2].ToUpper() }
        [ordered]@{
            name        = $_.FriendlyName
            class       = $_.Class
            vid         = $vid
            pid         = $pid2
            status      = "$($_.Status)"
            instance_id = $_.InstanceId
        }
    })
}
$result['network'] = Safe 'network' {
    @(Get-CimInstance Win32_NetworkAdapter | Where-Object { $_.PhysicalAdapter -eq $true } | ForEach-Object {
        [ordered]@{
            name = $_.Name
            mac  = $_.MACAddress
            speed_mbps = if ($_.Speed) { [math]::Round($_.Speed / 1MB, 0) } else { $null }
            status = "$($_.NetConnectionStatus)"
            driver_version = $_.DriverVersion
        }
    })
}
$result['audio'] = Safe 'audio' {
    @(Get-CimInstance Win32_SoundDevice | ForEach-Object { [ordered]@{ name = $_.Name; status = "$($_.Status)" } })
}
$result['battery'] = Safe 'battery' {
    $b = Get-CimInstance Win32_Battery | Select-Object -First 1
    if (-not $b) { return [ordered]@{ present = $false } }
    [ordered]@{ present = $true; name = $b.Name; status = "$($b.BatteryStatus)"; charge_pct = $b.EstimatedChargeRemaining }
}

# ---------- Thermal ----------
$result['thermal'] = Safe 'thermal' {
    $zones = @(Get-CimInstance Win32_PerfFormattedData_Counters_ThermalZoneInformation | ForEach-Object {
        [ordered]@{
            zone = $_.Name
            temperature_c = if ($_.Temperature) { [math]::Round([double]$_.Temperature - 273.15, 1) } else { $null }
        }
    })
    $gpuTemp = $null
    try {
        $smi = & nvidia-smi --query-gpu=name,temperature.gpu,memory.total,memory.used --format=csv,noheader,nounits 2>$null
        if ($smi) { $gpuTemp = ($smi -join ' | ') }
    } catch { }
    [ordered]@{ acpi_zones = $zones; nvidia_smi = $gpuTemp }
}

# ---------- Drivers ----------
$result['drivers'] = Safe 'drivers' {
    $classes = @('DISPLAY', 'NET', 'HDC', 'MEDIA', 'USB', 'SYSTEM')
    @(Get-CimInstance Win32_PnPSignedDriver | Where-Object { $classes -contains $_.DeviceClass } | ForEach-Object {
        [ordered]@{
            device   = $_.DeviceName
            class    = $_.DeviceClass
            version  = $_.DriverVersion
            date     = CimDate $_.DriverDate
            provider = $_.DriverProviderName
        }
    } | Select-Object -First 120)
}

# ---------- Vendor software ----------
$result['vendor_software'] = Safe 'vendor' {
    $pat = 'Armoury|AI Suite|MSI Center|Mystic Light|Afterburner|RivaTuner|XTU|Extreme Tuning|ThrottleStop|Ryzen Master|EasyTune|Precision X|Samsung Magician|CrystalDiskInfo|HWiNFO|AIDA64|GPU-Z|CPU-Z'
    $keys = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    @(Get-ItemProperty $keys -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -match $pat } |
        Select-Object -Unique DisplayName, DisplayVersion, InstallDate)
}

# ---------- Events ----------
$result['events'] = [ordered]@{}

$ev41 = Get-EventList -Ids 41 -Days 30 -Max 100
$result['events']['kernel_power_41'] = [ordered]@{
    count = $ev41.Count
    items = @($ev41 | ForEach-Object {
        $d = Get-EventData $_
        [ordered]@{ time = $_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss'); bugcheck = $d['BugcheckCode']; power_button = $d['PowerButtonTimestamp'] }
    })
}
$evBug = Get-EventList -Ids 1001 -Days 30 -Max 50 | Where-Object { $_.ProviderName -match 'BugCheck|WER-SystemErrorReporting' }
$result['events']['bugcheck'] = [ordered]@{
    count = @($evBug).Count
    items = @($evBug | ForEach-Object { [ordered]@{ time = $_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss'); message = (($_.Message -split "`r?`n")[0]) } })
}
$ev8 = Get-EventList -Ids 6008 -Days 30 -Max 50
$result['events']['unexpected_shutdown'] = [ordered]@{
    count = $ev8.Count
    items = @($ev8 | ForEach-Object { [ordered]@{ time = $_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss'); message = (($_.Message -split "`r?`n")[0]) } })
}
$whea = @()
try { $whea = @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-WHEA-Logger'; StartTime = (Get-Date).AddDays(-30) } -MaxEvents 50 -ErrorAction Stop) } catch { }
$result['events']['whea'] = [ordered]@{
    count = @($whea).Count
    fatal = @($whea | Where-Object { $_.Id -eq 1 }).Count
    items = @($whea | ForEach-Object { [ordered]@{ time = $_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss'); id = $_.Id; level = "$($_.LevelDisplayName)" } })
}
$diskEv = @()
try { $diskEv = @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'disk'; StartTime = (Get-Date).AddDays(-30) } -MaxEvents 5000 -ErrorAction Stop) } catch { }
$result['events']['disk_errors'] = [ordered]@{
    count = @($diskEv).Count
    by_id = @($diskEv | Group-Object Id | ForEach-Object { [ordered]@{ id = $_.Name; count = $_.Count } })
    items = @($diskEv | Select-Object -First 20 | ForEach-Object { [ordered]@{ time = $_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss'); id = $_.Id; message = (($_.Message -split "`r?`n")[0]) } })
}
$tdr = Get-EventList -Ids @(4101, 4102) -Days 30 -Max 30
$result['events']['tdr'] = [ordered]@{ count = $tdr.Count; items = @($tdr | ForEach-Object { [ordered]@{ time = $_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss'); id = $_.Id } }) }

$pnpEv = Get-EventList -Ids @(219, 442, 411) -Days 30 -Max 50
$result['events']['pnp'] = [ordered]@{ count = $pnpEv.Count; items = @($pnpEv | Select-Object -First 20 | ForEach-Object { [ordered]@{ time = $_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss'); id = $_.Id; provider = $_.ProviderName } }) }

# ---------- Memory diagnostic results ----------
$result['events']['memory_diag'] = Safe 'memdiag' {
    $m = @()
    try { $m = @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-MemoryDiagnostics-Results' } -MaxEvents 10 -ErrorAction Stop) } catch { }
    @($m | ForEach-Object {
        $x = [xml]$_.ToXml()
        $r = $x.Event.UserData.Results
        [ordered]@{
            time          = $_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss')
            completion    = "$($r.CompletionType)"
            memory_mb     = $r.MemorySize
            bad_pages     = $r.NumBadPages
            test_count    = $r.TestCount
            duration_sec  = $r.TestDuration
        }
    })
}

# ---------- Dumps ----------
$result['dumps'] = Safe 'dumps' {
    $md = @(Get-ChildItem 'C:\Windows\Minidump' -ErrorAction SilentlyContinue)
    $live = @(Get-ChildItem 'C:\Windows\LiveKernelReports' -Recurse -File -ErrorAction SilentlyContinue)
    [ordered]@{
        minidump_count = $md.Count
        minidump_latest = if ($md.Count -gt 0) { ($md | Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss') } else { $null }
        memory_dmp     = Test-Path 'C:\Windows\MEMORY.DMP'
        live_reports   = $live.Count
    }
}

$result['errors'] = @($script:Errors)

# ---------- Output ----------
if (-not $OutFile) {
    $outDir = Join-Path (Split-Path $PSScriptRoot -Parent) 'out'
    if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }
    $OutFile = Join-Path $outDir ("hardware_audit_{0}_{1}.json" -f $env:COMPUTERNAME, (Get-Date -Format 'yyyyMMdd_HHmmss'))
}
$json = $result | ConvertTo-Json -Depth 10
[System.IO.File]::WriteAllText($OutFile, $json, (New-Object System.Text.UTF8Encoding($false)))
Write-Output $OutFile
