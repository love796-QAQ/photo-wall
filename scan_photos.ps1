$photosDir = Join-Path $PSScriptRoot "photos"
$outputFile = Join-Path $PSScriptRoot "photos.js"
$pythonScript = Join-Path $PSScriptRoot "extract_exif.py"

if (Test-Path $pythonScript) {
    Write-Host "使用 Python EXIF 扫描脚本..."
    python $pythonScript
} else {
    Write-Host "Python 脚本不可用，使用基础扫描..."
    if (-not (Test-Path $photosDir)) {
        New-Item -ItemType Directory -Force -Path $photosDir
    }
    $images = Get-ChildItem -Path $photosDir -Include *.jpg, *.jpeg, *.png, *.gif, *.webp, *.bmp -Recurse
    $photoList = @()
    foreach ($img in $images) {
        $photoList += @{ name = $img.Name; path = "photos/$($img.Name)" }
    }
    $jsonContent = $photoList | ConvertTo-Json -Depth 2
    "const photoData = $jsonContent;" | Out-File -FilePath $outputFile -Encoding utf8
    Write-Host "已扫描 $($photoList.Count) 张照片 → photos.js"
}
