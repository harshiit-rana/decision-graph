#Requires -RunAsAdministrator
# Copies the PostgreSQL data directory out of the OLD Docker Desktop data disk, read-only.
#
# What it does NOT do: it never mounts the old disk read-write, never starts Postgres on it,
# never deletes or modifies anything on it. The disk is attached with ext4 options ro,noload
# (noload: do not replay the journal, which would be a write). The only thing written is a tar
# of the Postgres data directory into D:\pg-recovery, which is the backup everything else uses.
#
# Before running: quit Docker Desktop completely (tray icon > Quit), so nothing else has the
# disk open. This script does not touch the NEW Docker disk under C:\Users\ASUS\AppData\Local\Docker.

$ErrorActionPreference = 'Stop'
$vhd = 'D:\Docker\DockerDesktopWSL\disk\docker_data.vhdx'
$out = 'D:\pg-recovery'
$name = 'dgold'
$maxGB = 14

if (-not (Test-Path $vhd)) { throw "old data disk not found: $vhd" }
New-Item -ItemType Directory -Force $out | Out-Null
$before = Get-Item $vhd | Select-Object Length, LastWriteTime
"old disk before: $($before.Length) bytes, last written $($before.LastWriteTime)"

wsl --shutdown
wsl --mount --vhd $vhd --name $name --options "ro,noload"
if ($LASTEXITCODE -ne 0) { throw "wsl --mount failed (exit $LASTEXITCODE)" }

try {
    $mnt = "/mnt/wsl/$name"
    "mounted read-only at $mnt; looking for Postgres data directories..."
    $found = wsl -d Ubuntu -u root -- bash -c "find $mnt -maxdepth 7 -name PG_VERSION -not -path '*/proc/*' 2>/dev/null"
    $found = @($found | Where-Object { $_ -and $_.Trim() })
    if ($found.Count -eq 0) {
        "No PG_VERSION found. Top of the disk:"
        wsl -d Ubuntu -u root -- bash -c "ls -la $mnt; ls -la $mnt/docker 2>/dev/null; ls -la $mnt/docker/volumes 2>/dev/null"
        throw "no Postgres data directory found on the old disk"
    }
    $report = @()
    foreach ($f in $found) {
        $dir = ($f -replace '/PG_VERSION$', '')
        $ver = (wsl -d Ubuntu -u root -- cat "$f").Trim()
        $size = (wsl -d Ubuntu -u root -- bash -c "du -sm '$dir' | cut -f1").Trim()
        $ctl = (wsl -d Ubuntu -u root -- bash -c "test -f '$dir/global/pg_control' && echo yes || echo no").Trim()
        $report += [pscustomobject]@{ Dir = $dir; PgVersion = $ver; SizeMB = [int]$size; HasPgControl = $ctl }
    }
    $report | Format-Table -AutoSize | Out-String -Width 220
    $report | ConvertTo-Json | Set-Content -Encoding ascii "$out\found.json"

    $pick = $report | Where-Object { $_.HasPgControl -eq 'yes' } | Sort-Object SizeMB -Descending | Select-Object -First 1
    if (-not $pick) { throw "found PG_VERSION but no global/pg_control; not copying" }
    if ($pick.SizeMB -gt ($maxGB * 1024)) { throw "$($pick.SizeMB) MB is over the $maxGB GB cap; free space or raise the cap" }
    "copying $($pick.Dir) ($($pick.SizeMB) MB, PG $($pick.PgVersion)) to $out\pgdata.tar"

    wsl -d Ubuntu -u root -- bash -c "tar --numeric-owner -C '$($pick.Dir)' -cf /mnt/d/pg-recovery/pgdata.tar ."
    if ($LASTEXITCODE -ne 0) { throw "tar failed (exit $LASTEXITCODE)" }
    $hash = (wsl -d Ubuntu -u root -- bash -c "sha256sum /mnt/d/pg-recovery/pgdata.tar | cut -d' ' -f1").Trim()
    $entries = (wsl -d Ubuntu -u root -- bash -c "tar -tf /mnt/d/pg-recovery/pgdata.tar | wc -l").Trim()
    "tar sha256 $hash, $entries entries"
    "tar sha256 $hash`nentries $entries`nsource $($pick.Dir)`npg_version $($pick.PgVersion)" | Set-Content -Encoding ascii "$out\pgdata.tar.sha256.txt"
}
finally {
    wsl --unmount $vhd
    $after = Get-Item $vhd | Select-Object Length, LastWriteTime
    "old disk after:  $($after.Length) bytes, last written $($after.LastWriteTime)"
    if ($after.LastWriteTime -ne $before.LastWriteTime -or $after.Length -ne $before.Length) {
        Write-Warning "the old disk's size or timestamp changed; tell Claude before doing anything else"
    } else { "old disk unchanged." }
}
"done. Backup: $out\pgdata.tar (+ pgdata.tar.sha256.txt, found.json)"
