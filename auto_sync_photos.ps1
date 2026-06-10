param(
    [switch]$ResolveLocations
)

$photosDir = Join-Path $PSScriptRoot "photos"
$pythonScript = Join-Path $PSScriptRoot "extract_exif.py"
$imageExtensions = @(".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif")

function Get-PhotoState {
    if (-not (Test-Path $photosDir)) {
        return ""
    }

    $items = Get-ChildItem -LiteralPath $photosDir -File |
        Where-Object { $imageExtensions -contains $_.Extension.ToLowerInvariant() } |
        Sort-Object Name

    return ($items | ForEach-Object {
        "$($_.Name)|$($_.Length)|$($_.LastWriteTimeUtc.Ticks)"
    }) -join "`n"
}

function Update-PhotoList {
    Write-Host "Photo change detected. Updating data..." -ForegroundColor Cyan

    $arguments = @($pythonScript)
    if ($ResolveLocations) {
        $arguments += "--resolve-locations"
    }

    & python @arguments
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Update failed. Auto-Sync will retry." -ForegroundColor Red
        return $false
    }

    Write-Host "Photo data updated. Refresh the webpage to view changes." -ForegroundColor Green
    return $true
}

if (-not (Test-Path $photosDir)) {
    New-Item -ItemType Directory -Force -Path $photosDir | Out-Null
}

Write-Host "------------------------------------------------" -ForegroundColor Yellow
Write-Host "Photo Wall Auto-Sync is running" -ForegroundColor Yellow
Write-Host "Watching: $photosDir"
Write-Host "Automatic location lookup: $($ResolveLocations.IsPresent)"
Write-Host "Add, replace, or delete photos to rebuild photos.js/json"
Write-Host "Keep this window open. Press Ctrl+C to stop."
Write-Host "------------------------------------------------" -ForegroundColor Yellow

$lastState = Get-PhotoState
Update-PhotoList | Out-Null

while ($true) {
    Start-Sleep -Seconds 1
    $currentState = Get-PhotoState

    if ($currentState -eq $lastState) {
        continue
    }

    # Wait until file copying has settled before reading EXIF.
    Start-Sleep -Seconds 1
    $stableState = Get-PhotoState
    if ($stableState -ne $currentState) {
        continue
    }

    if (Update-PhotoList) {
        $lastState = $stableState
    }
}
