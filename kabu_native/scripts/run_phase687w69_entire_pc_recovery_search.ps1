#Requires -Version 5.1
<#
Phase687W69 — Entire PC Lost Paper Session Recovery Search (read-only).
Does NOT modify/delete/restore source files. Writes reports only under kabu_native/results/reports.
#>
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

$OutDir = 'C:\Users\yhach\Documents\tradebotfile\kabu_native\results\reports'
$RecoveryStaging = 'C:\Users\yhach\Documents\tradebotfile_recovery'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$StartedAt = Get-Date
$SearchLog = [System.Collections.Generic.List[object]]::new()
$Errors = [System.Collections.Generic.List[object]]::new()
$FilenameHits = [System.Collections.Generic.List[object]]::new()
$PathHits = [System.Collections.Generic.List[object]]::new()
$ContentHits = [System.Collections.Generic.List[object]]::new()
$ArchiveHits = [System.Collections.Generic.List[object]]::new()
$RecycleHits = [System.Collections.Generic.List[object]]::new()
$GitCopies = [System.Collections.Generic.List[object]]::new()
$Skipped = [System.Collections.Generic.List[object]]::new()

$ExactNames = @(
  'small_paper_events.csv',
  'small_paper_events.jsonl',
  'small_paper_summary.json',
  'small_paper_positions.csv',
  'small_paper_rejects.csv',
  'structural_trades.csv',
  'structural_events.csv',
  'quality_top_debug.csv',
  'live_session_config.json',
  'daily_runner_summary_20260528.json',
  'daily_runner_summary_20260529.json',
  'phase148_am_pm_daily_runner_20260528.json',
  'phase265_structural_trades_backfill_by_session.csv',
  'phase300_board_live_payload_availability_report.json'
)

$DateTokens = @(
  '20260528','20260529','20260601','20260602','20260603','20260604','20260605',
  '20260608','20260609','20260610','20260611','20260612'
)

$SessionTokens = @(
  'live_session_082247','live_session_122515','live_session_075135','live_session_122541',
  'live_session_075940','live_session_122524','live_session_103014','live_session_080544',
  'live_session_122534','live_session_082928','live_session_122530'
)

$ContentPattern = 'live_session_082247|live_session_122515|live_session_080544|live_session_122534|2026-05-28|20260528|2026-06-04|20260604|small_paper_events|observer_exit|entry_order_book_imbalance'

# Skip roots that cause infinite recursion / virtual FS noise
$SkipRootRegex = '(?i)\\(System Volume Information|\$WinREAgent|Windows\\WinSxS|Windows\\Servicing|Windows\\Installer|Proc|Dev|WindowsApps)\\'

function Add-SearchLog {
  param($Drive,$Root,$Accessible,$Started,$Finished,$Files=$null,$Dirs=$null,$ErrorsCount=0,$SkippedReason='')
  $SearchLog.Add([pscustomobject]@{
    drive = $Drive
    root_path = $Root
    search_started_at = $Started
    search_finished_at = $Finished
    accessible = $Accessible
    files_scanned = $Files
    directories_scanned = $Dirs
    errors = $ErrorsCount
    skipped_reason = $SkippedReason
  }) | Out-Null
}

function Add-ErrorRow {
  param($Root,$Path,$Message)
  $Errors.Add([pscustomobject]@{
    root = $Root
    path = $Path
    message = "$Message"
    at = (Get-Date).ToString('o')
  }) | Out-Null
}

function Test-ShouldSkipPath([string]$Path) {
  if ([string]::IsNullOrWhiteSpace($Path)) { return $true }
  if ($Path -match $SkipRootRegex) { return $true }
  # Avoid reparse points that loop (best-effort)
  try {
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
      # allow known dirs; skip junctions under Users\All Users etc. when named junction
      if ($item.LinkType -eq 'Junction' -or $item.LinkType -eq 'SymbolicLink') {
        return $true
      }
    }
  } catch { }
  return $false
}

function Get-DriveInventory {
  $rows = @()
  foreach ($d in Get-CimInstance Win32_LogicalDisk) {
    $root = "$($d.DeviceID)\"
    $acc = $false
    try { $acc = Test-Path -LiteralPath $root } catch { $acc = $false }
    $typeName = switch ([int]$d.DriveType) {
      2 { 'Removable' }
      3 { 'Local Fixed' }
      4 { 'Network' }
      5 { 'CD-ROM' }
      default { "Type$($d.DriveType)" }
    }
    $rows += [pscustomobject]@{
      drive = $d.DeviceID
      type = $typeName
      volume_label = $d.VolumeName
      filesystem = $d.FileSystem
      total_size = $d.Size
      free_size = $d.FreeSpace
      accessible = $acc
      root = $root
    }
  }
  return $rows
}

function Search-ExactFilenames {
  param([string[]]$Roots, [string[]]$Names, [int]$MaxHitsPerName = 5000)
  foreach ($root in $Roots) {
    if (-not (Test-Path -LiteralPath $root)) {
      Add-SearchLog -Drive ($root.Substring(0,2)) -Root $root -Accessible $false -Started (Get-Date).ToString('o') -Finished (Get-Date).ToString('o') -SkippedReason 'root_missing'
      continue
    }
    $t0 = Get-Date
    $errN = 0
    foreach ($name in $Names) {
      try {
        Get-ChildItem -LiteralPath $root -Recurse -Force -File -Filter $name -ErrorAction SilentlyContinue |
          ForEach-Object {
            if ($FilenameHits.Count -ge 200000) { return }
            $FilenameHits.Add([pscustomobject]@{
              full_path = $_.FullName
              file_name = $_.Name
              length = $_.Length
              creation_time = $_.CreationTime.ToString('o')
              last_write_time = $_.LastWriteTime.ToString('o')
              drive = ($_.FullName.Substring(0,2))
              search_pattern = $name
              hit_kind = 'exact_filename'
            }) | Out-Null
          }
      } catch {
        $errN++
        Add-ErrorRow -Root $root -Path $name -Message $_.Exception.Message
      }
    }
    Add-SearchLog -Drive ($root.Substring(0,[Math]::Min(2,$root.Length))) -Root $root -Accessible $true -Started $t0.ToString('o') -Finished (Get-Date).ToString('o') -ErrorsCount $errN -SkippedReason "exact_filename_scan:$($Names.Count)_patterns"
  }
}

function Search-DirectoryNameTokens {
  param([string[]]$Roots, [string[]]$Tokens)
  foreach ($root in $Roots) {
    if (-not (Test-Path -LiteralPath $root)) { continue }
    $t0 = Get-Date
    foreach ($tok in $Tokens) {
      try {
        Get-ChildItem -LiteralPath $root -Recurse -Force -Directory -Filter $tok -ErrorAction SilentlyContinue |
          ForEach-Object {
            $PathHits.Add([pscustomobject]@{
              full_path = $_.FullName
              item_type = 'directory'
              size = $null
              creation_time = $_.CreationTime.ToString('o')
              last_write_time = $_.LastWriteTime.ToString('o')
              drive = $_.FullName.Substring(0,2)
              token = $tok
            }) | Out-Null
          }
      } catch {
        Add-ErrorRow -Root $root -Path $tok -Message $_.Exception.Message
      }
    }
    Add-SearchLog -Drive ($root.Substring(0,2)) -Root $root -Accessible $true -Started $t0.ToString('o') -Finished (Get-Date).ToString('o') -SkippedReason 'directory_token_scan'
  }
}

function Search-PathSubstringDirs {
  param([string[]]$Roots, [string[]]$Needles, [int]$MaxDepth = 8)
  # Breadth-limited walk for path tokens (small_paper, live_session, etc.) without full-disk name scan of every file
  foreach ($root in $Roots) {
    if (-not (Test-Path -LiteralPath $root)) { continue }
    $t0 = Get-Date
    $queue = [System.Collections.Generic.Queue[object]]::new()
    $queue.Enqueue([pscustomobject]@{ Path = $root; Depth = 0 })
    $seen = 0
    while ($queue.Count -gt 0 -and $seen -lt 500000) {
      $cur = $queue.Dequeue()
      $seen++
      if (Test-ShouldSkipPath $cur.Path) {
        $Skipped.Add([pscustomobject]@{ path = $cur.Path; reason = 'reparse_or_system_skip' }) | Out-Null
        continue
      }
      $leaf = Split-Path -Leaf $cur.Path
      foreach ($n in $Needles) {
        if ($leaf -like "*$n*" -or $cur.Path -like "*\$n\*" -or $cur.Path -like "*\$n") {
          try {
            $it = Get-Item -LiteralPath $cur.Path -Force -ErrorAction Stop
            $PathHits.Add([pscustomobject]@{
              full_path = $it.FullName
              item_type = $(if ($it.PSIsContainer) { 'directory' } else { 'file' })
              size = $(if ($it.PSIsContainer) { $null } else { $it.Length })
              creation_time = $it.CreationTime.ToString('o')
              last_write_time = $it.LastWriteTime.ToString('o')
              drive = $it.FullName.Substring(0,2)
              token = $n
            }) | Out-Null
          } catch {
            Add-ErrorRow -Root $root -Path $cur.Path -Message $_.Exception.Message
          }
          break
        }
      }
      if ($cur.Depth -ge $MaxDepth) { continue }
      try {
        Get-ChildItem -LiteralPath $cur.Path -Force -Directory -ErrorAction SilentlyContinue |
          ForEach-Object { $queue.Enqueue([pscustomobject]@{ Path = $_.FullName; Depth = $cur.Depth + 1 }) }
      } catch {
        Add-ErrorRow -Root $root -Path $cur.Path -Message $_.Exception.Message
      }
    }
    Add-SearchLog -Drive ($root.Substring(0,2)) -Root $root -Accessible $true -Started $t0.ToString('o') -Finished (Get-Date).ToString('o') -DirectoriesScanned $seen -SkippedReason "path_token_bfs_maxdepth_$MaxDepth"
  }
}

function Search-Archives {
  param([string[]]$Roots)
  $exts = @('*.zip','*.7z','*.rar','*.tar','*.tgz','*.gz','*.bz2')
  $nameNeedles = @('small_paper','paper','session','tradebot','kabu','202605','202606','backup','archive','results')
  foreach ($root in $Roots) {
    if (-not (Test-Path -LiteralPath $root)) { continue }
    $t0 = Get-Date
    foreach ($ext in $exts) {
      try {
        Get-ChildItem -LiteralPath $root -Recurse -Force -File -Filter $ext -ErrorAction SilentlyContinue |
          ForEach-Object {
            $nameHit = $false
            foreach ($n in $nameNeedles) {
              if ($_.Name -match [regex]::Escape($n) -or $_.FullName -match [regex]::Escape($n)) { $nameHit = $true; break }
            }
            # Always record archives under tradebot / user home that look relevant; for full disk only name hits + large
            $underUser = $_.FullName -like 'C:\Users\yhach\*'
            $underTrade = $_.FullName -match 'tradebot|kabu_native'
            if (-not ($nameHit -or $underTrade -or ($underUser -and $_.Length -gt 1MB))) { return }
            $row = [pscustomobject]@{
              full_path = $_.FullName
              length = $_.Length
              creation_time = $_.CreationTime.ToString('o')
              last_write_time = $_.LastWriteTime.ToString('o')
              drive = $_.FullName.Substring(0,2)
              name_hit = $nameHit
              listing_note = ''
              inner_hits = ''
            }
            if ($_.Extension -eq '.zip' -and $_.Length -lt 2GB) {
              try {
                Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction SilentlyContinue
                $zip = [System.IO.Compression.ZipFile]::OpenRead($_.FullName)
                $inner = @()
                foreach ($e in $zip.Entries) {
                  $en = $e.FullName
                  if ($en -match 'small_paper|small_paper_events|live_session|20260528|20260604|20260612|20260529|20260601') {
                    $inner += $en
                    if ($inner.Count -ge 40) { break }
                  }
                }
                $zip.Dispose()
                $row.listing_note = 'zip_listed'
                $row.inner_hits = ($inner -join '|')
              } catch {
                $row.listing_note = "zip_list_failed:$($_.Exception.Message)"
              }
            } else {
              $row.listing_note = 'not_auto_listed'
            }
            $ArchiveHits.Add($row) | Out-Null
          }
      } catch {
        Add-ErrorRow -Root $root -Path $ext -Message $_.Exception.Message
      }
    }
    Add-SearchLog -Drive ($root.Substring(0,2)) -Root $root -Accessible $true -Started $t0.ToString('o') -Finished (Get-Date).ToString('o') -SkippedReason 'archive_scan'
  }
}

function Search-ContentRg {
  param([string[]]$Roots)
  $rg = Get-Command rg -ErrorAction SilentlyContinue
  if (-not $rg) {
    $Skipped.Add([pscustomobject]@{ path = '(rg)'; reason = 'rg_not_found' }) | Out-Null
    return
  }
  foreach ($root in $Roots) {
    if (-not (Test-Path -LiteralPath $root)) { continue }
    $t0 = Get-Date
    $tmp = Join-Path $env:TEMP ("w69_rg_{0}.txt" -f ([guid]::NewGuid().ToString('N')))
    try {
      & rg --hidden --no-ignore -n --max-count 20 `
        --glob '*.csv' --glob '*.json' --glob '*.jsonl' --glob '*.txt' --glob '*.log' --glob '*.md' `
        --glob '*.yaml' --glob '*.yml' --glob '*.ps1' --glob '*.bat' --glob '*.cmd' --glob '*.py' `
        $ContentPattern $root 2>$null | Select-Object -First 5000 | Set-Content -Encoding UTF8 $tmp
      if (Test-Path $tmp) {
        Get-Content $tmp -ErrorAction SilentlyContinue | ForEach-Object {
          if ($_ -match '^(.*?):(\d+):(.*)$') {
            $ContentHits.Add([pscustomobject]@{
              full_path = $Matches[1]
              line = [int]$Matches[2]
              snippet = $Matches[3].Substring(0, [Math]::Min(240, $Matches[3].Length))
              drive = $(if ($Matches[1].Length -ge 2) { $Matches[1].Substring(0,2) } else { '' })
            }) | Out-Null
          }
        }
      }
    } catch {
      Add-ErrorRow -Root $root -Path 'rg' -Message $_.Exception.Message
    } finally {
      Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
    Add-SearchLog -Drive ($root.Substring(0,2)) -Root $root -Accessible $true -Started $t0.ToString('o') -Finished (Get-Date).ToString('o') -SkippedReason 'rg_content_scan'
  }
}

function Search-RecycleBin {
  foreach ($drive in @('C:','D:','E:','F:')) {
    $rb = "$drive\`$Recycle.Bin"
    if (-not (Test-Path -LiteralPath $rb)) {
      Add-SearchLog -Drive $drive -Root $rb -Accessible $false -Started (Get-Date).ToString('o') -Finished (Get-Date).ToString('o') -SkippedReason 'recycle_missing'
      continue
    }
    $t0 = Get-Date
    try {
      Get-ChildItem -LiteralPath $rb -Recurse -Force -ErrorAction SilentlyContinue |
        Where-Object {
          $_.Name -match 'small_paper|live_session|2026052|2026060|2026061|structural_trades|events' -or
          $_.FullName -match 'small_paper|live_session'
        } |
        Select-Object -First 2000 |
        ForEach-Object {
          $RecycleHits.Add([pscustomobject]@{
            full_path = $_.FullName
            file_name = $_.Name
            length = $(if ($_.PSIsContainer) { $null } else { $_.Length })
            creation_time = $_.CreationTime.ToString('o')
            last_write_time = $_.LastWriteTime.ToString('o')
            drive = $drive
            note = 'recycle_bin_name_match_original_path_unknown_without_shell_api'
          }) | Out-Null
        }
    } catch {
      Add-ErrorRow -Root $rb -Path $rb -Message $_.Exception.Message
    }
    Add-SearchLog -Drive $drive -Root $rb -Accessible $true -Started $t0.ToString('o') -Finished (Get-Date).ToString('o') -SkippedReason 'recycle_scan'
  }
}

function Search-GitRepos {
  param([string[]]$Roots)
  foreach ($root in $Roots) {
    if (-not (Test-Path -LiteralPath $root)) { continue }
    $t0 = Get-Date
    try {
      Get-ChildItem -LiteralPath $root -Recurse -Force -Directory -Filter '.git' -ErrorAction SilentlyContinue |
        Select-Object -First 300 |
        ForEach-Object {
          $repo = Split-Path $_.FullName -Parent
          $sp = Join-Path $repo 'kabu_native\results\small_paper'
          if (-not (Test-Path $sp)) { $sp = Join-Path $repo 'results\small_paper' }
          $hasSp = Test-Path -LiteralPath $sp
          $events = @()
          if ($hasSp) {
            $events = @(Get-ChildItem -LiteralPath $sp -Recurse -Force -File -Filter 'small_paper_events.*' -ErrorAction SilentlyContinue | Select-Object -First 50)
          }
          $GitCopies.Add([pscustomobject]@{
            repo_root = $repo
            has_small_paper = $hasSp
            small_paper_path = $(if ($hasSp) { $sp } else { '' })
            events_count_sample = $events.Count
            sample_event = $(if ($events) { $events[0].FullName } else { '' })
            dated_dirs = $(if ($hasSp) { ((Get-ChildItem $sp -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^2026052|^2026060|^2026061' }).Name -join '|') } else { '' })
          }) | Out-Null
        }
    } catch {
      Add-ErrorRow -Root $root -Path '.git' -Message $_.Exception.Message
    }
    Add-SearchLog -Drive ($root.Substring(0,2)) -Root $root -Accessible $true -Started $t0.ToString('o') -Finished (Get-Date).ToString('o') -SkippedReason 'git_repo_scan'
  }
}

function Get-VssInfo {
  $rows = @()
  try {
    $out = & vssadmin list shadows 2>&1 | Out-String
    $rows += [pscustomobject]@{ tool = 'vssadmin'; ok = $true; output = $out.Substring(0, [Math]::Min(8000, $out.Length)) }
  } catch {
    $rows += [pscustomobject]@{ tool = 'vssadmin'; ok = $false; output = $_.Exception.Message }
  }
  try {
    $out = & wbadmin get versions 2>&1 | Out-String
    $rows += [pscustomobject]@{ tool = 'wbadmin'; ok = $true; output = $out.Substring(0, [Math]::Min(4000, $out.Length)) }
  } catch {
    $rows += [pscustomobject]@{ tool = 'wbadmin'; ok = $false; output = $_.Exception.Message }
  }
  return $rows
}

# ---------------- main ----------------
Write-Host "W69 start $($StartedAt.ToString('o'))"

$DriveInv = @(Get-DriveInventory)
$DriveInv | Export-Csv -NoTypeInformation -Encoding UTF8 (Join-Path $OutDir 'phase687w69_drives.csv')

$AccessibleRoots = @($DriveInv | Where-Object { $_.accessible } | ForEach-Object { $_.root })

# Priority roots (deep)
$PriorityRoots = @(
  'C:\Users\yhach\Documents\tradebotfile',
  'C:\Users\yhach\Documents',
  'C:\Users\yhach\Desktop',
  'C:\Users\yhach\Downloads',
  'C:\Users\yhach\OneDrive',
  'C:\Users\yhach\AppData\Local\Temp',
  'C:\Users\yhach\AppData\Roaming\Microsoft\Windows\Recent',
  'C:\Users\yhach\AppData\Local\Cursor',
  'C:\Users\yhach\AppData\Roaming\Cursor',
  'C:\Users\yhach\.cursor',
  'C:\Windows\Temp'
)
Get-ChildItem 'C:\Users\yhach' -Directory -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -match 'OneDrive|Dropbox|Google' } |
  ForEach-Object { $PriorityRoots += $_.FullName }
$PriorityRoots = $PriorityRoots | Select-Object -Unique | Where-Object { Test-Path -LiteralPath $_ }

Write-Host "Priority roots: $($PriorityRoots.Count)"
Write-Host '=== Phase A: exact filenames on priority + each drive root (can take long) ==='

# Exact filenames: priority first (deep), then each accessible drive root
Search-ExactFilenames -Roots $PriorityRoots -Names $ExactNames

# Also search exact names of session-critical files with date tokens via filter small_paper_events.*
foreach ($root in $PriorityRoots) {
  $t0 = Get-Date
  foreach ($filt in @('small_paper_events.csv','small_paper_events.jsonl','structural_trades.csv','small_paper_summary.json')) {
    Get-ChildItem -LiteralPath $root -Recurse -Force -File -Filter $filt -ErrorAction SilentlyContinue |
      ForEach-Object {
        $FilenameHits.Add([pscustomobject]@{
          full_path = $_.FullName
          file_name = $_.Name
          length = $_.Length
          creation_time = $_.CreationTime.ToString('o')
          last_write_time = $_.LastWriteTime.ToString('o')
          drive = $_.FullName.Substring(0,2)
          search_pattern = $filt
          hit_kind = 'priority_exact'
        }) | Out-Null
      }
  }
  Add-SearchLog -Drive 'C:' -Root $root -Accessible $true -Started $t0.ToString('o') -Finished (Get-Date).ToString('o') -SkippedReason 'priority_events_rescan'
}

Write-Host '=== Phase B: directory tokens (sessions/dates) on priority + C:\Users ==='
$dirRoots = @($PriorityRoots + @('C:\Users\yhach')) | Select-Object -Unique
Search-DirectoryNameTokens -Roots $dirRoots -Tokens ($SessionTokens + $DateTokens + @('small_paper','paper_sessions','live_session_*'))

# live_session_* wildcard via -Filter
foreach ($root in $dirRoots) {
  if (-not (Test-Path $root)) { continue }
  Get-ChildItem -LiteralPath $root -Recurse -Force -Directory -Filter 'live_session_*' -ErrorAction SilentlyContinue |
    ForEach-Object {
      $PathHits.Add([pscustomobject]@{
        full_path = $_.FullName
        item_type = 'directory'
        size = $null
        creation_time = $_.CreationTime.ToString('o')
        last_write_time = $_.LastWriteTime.ToString('o')
        drive = $_.FullName.Substring(0,2)
        token = 'live_session_*'
      }) | Out-Null
    }
}

Write-Host '=== Phase C: path token BFS on other accessible drives (depth-limited) ==='
$pathNeedles = @('small_paper','paper_trade','papertrade','live_session','tradebotfile','kabu_native','paper_session','paper_sessions','archive','archives','backup','backups','recovery','restore')
$otherRoots = @()
foreach ($r in $AccessibleRoots) {
  if ($r -eq 'C:\') {
    # C:\ full BFS too heavy; already covered Users/priority. Do shallow C:\ top-level only.
    Get-ChildItem 'C:\' -Force -Directory -ErrorAction SilentlyContinue | ForEach-Object {
      if ($_.Name -notmatch '^(Windows|Program Files|Program Files \(x86\)|ProgramData|\$Recycle\.Bin|System Volume Information)$') {
        $otherRoots += $_.FullName
      }
    }
  } else {
    $otherRoots += $r
  }
}
Search-PathSubstringDirs -Roots ($otherRoots | Select-Object -Unique) -Needles $pathNeedles -MaxDepth 6

Write-Host '=== Phase D: full-drive exact filename for critical artifacts (all accessible drives) ==='
# Critical short list for full drive scan
$Critical = @('small_paper_events.csv','small_paper_events.jsonl','structural_trades.csv','small_paper_summary.json')
Search-ExactFilenames -Roots $AccessibleRoots -Names $Critical

Write-Host '=== Phase E: archives ==='
Search-Archives -Roots (@($PriorityRoots + $AccessibleRoots) | Select-Object -Unique)

Write-Host '=== Phase F: content rg ==='
Search-ContentRg -Roots $PriorityRoots

Write-Host '=== Phase G: recycle / git / vss ==='
Search-RecycleBin
Search-GitRepos -Roots @('C:\Users\yhach\Documents','C:\Users\yhach\Desktop','C:\Users\yhach\Downloads','C:\Users\yhach')
$Vss = @(Get-VssInfo)

# Export raw CSVs
$FilenameHits | Sort-Object full_path -Unique | Export-Csv -NoTypeInformation -Encoding UTF8 (Join-Path $OutDir 'phase687w69_filename_hits.csv')
$ContentHits | Export-Csv -NoTypeInformation -Encoding UTF8 (Join-Path $OutDir 'phase687w69_content_hits.csv')
$ArchiveHits | Export-Csv -NoTypeInformation -Encoding UTF8 (Join-Path $OutDir 'phase687w69_archive_hits.csv')
$Errors | Export-Csv -NoTypeInformation -Encoding UTF8 (Join-Path $OutDir 'phase687w69_search_errors.csv')
$SearchLog | Export-Csv -NoTypeInformation -Encoding UTF8 (Join-Path $OutDir 'phase687w69_search_coverage.csv')
$PathHits | Sort-Object full_path -Unique | Export-Csv -NoTypeInformation -Encoding UTF8 (Join-Path $OutDir 'phase687w69_path_hits.csv')
$RecycleHits | Export-Csv -NoTypeInformation -Encoding UTF8 (Join-Path $OutDir 'phase687w69_recycle_hits.csv')
$GitCopies | Export-Csv -NoTypeInformation -Encoding UTF8 (Join-Path $OutDir 'phase687w69_git_copies.csv')
$Vss | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 (Join-Path $OutDir 'phase687w69_vss_wbadmin.json')

$FinishedAt = Get-Date
[pscustomobject]@{
  started_at = $StartedAt.ToString('o')
  finished_at = $FinishedAt.ToString('o')
  duration_sec = [int]($FinishedAt - $StartedAt).TotalSeconds
  filename_hits = $FilenameHits.Count
  path_hits = $PathHits.Count
  content_hits = $ContentHits.Count
  archive_hits = $ArchiveHits.Count
  recycle_hits = $RecycleHits.Count
  git_copies = $GitCopies.Count
  errors = $Errors.Count
  recovery_staging = $RecoveryStaging
} | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $OutDir 'phase687w69_search_meta.json')

Write-Host "W69 search done in $([int]($FinishedAt - $StartedAt).TotalSeconds)s"
Write-Host "filename=$($FilenameHits.Count) path=$($PathHits.Count) content=$($ContentHits.Count) archive=$($ArchiveHits.Count)"
