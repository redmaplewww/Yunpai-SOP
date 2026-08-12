param(
    [string]$InputPath,
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
if (-not $InputPath) { $InputPath = $env:SOP_PREVIEW_INPUT_PATH }
if (-not $OutputDirectory) { $OutputDirectory = $env:SOP_PREVIEW_OUTPUT_DIR }
if (-not $InputPath -or -not $OutputDirectory) {
    throw 'InputPath and OutputDirectory are required.'
}
$inputFile = (Resolve-Path -LiteralPath $InputPath).Path
$outputRoot = [System.IO.Path]::GetFullPath($OutputDirectory)
[System.IO.Directory]::CreateDirectory($outputRoot) | Out-Null
$stem = [System.IO.Path]::GetFileNameWithoutExtension($inputFile)
$pdfPath = Join-Path $outputRoot ($stem + '.pdf')

$word = $null
$document = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $document = $word.Documents.Open($inputFile, $false, $true)
    $document.ExportAsFixedFormat($pdfPath, 17)
}
finally {
    if ($document) { try { $document.Close($false) } catch { } }
    if ($word) { try { $word.Quit() } catch { } }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

if (-not (Test-Path -LiteralPath $pdfPath)) {
    throw "Word did not create PDF preview: $pdfPath"
}

Get-ChildItem -LiteralPath $outputRoot -Filter 'page-*.png' -ErrorAction SilentlyContinue | Remove-Item -Force
$pdftocairo = (Get-Command pdftocairo.exe -ErrorAction SilentlyContinue).Source
if ($pdftocairo) {
    & $pdftocairo -png -r 110 $pdfPath (Join-Path $outputRoot 'page')
    if ($LASTEXITCODE -ne 0) { throw "pdftocairo failed with exit code $LASTEXITCODE" }
}
else {
    $pdftoppm = (Get-Command pdftoppm.cmd -ErrorAction Stop).Source
    & $pdftoppm -png -r 110 $pdfPath (Join-Path $outputRoot 'page')
    if ($LASTEXITCODE -ne 0) { throw "pdftoppm failed with exit code $LASTEXITCODE" }
}

$pages = @(Get-ChildItem -LiteralPath $outputRoot -Filter 'page-*.png' | Sort-Object Name)
if ($pages.Count -eq 0) { throw 'No DOCX preview pages were generated.' }

[pscustomobject]@{
    pdf_path = $pdfPath
    page_count = $pages.Count
    page_paths = @($pages.FullName)
} | ConvertTo-Json -Depth 3
