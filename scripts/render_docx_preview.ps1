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
$pageCount = 0
$conversionBackend = $null
$wordError = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $document = $word.Documents.Open($inputFile, $false, $true)
    $document.ExportAsFixedFormat($pdfPath, 17)
    $pageCount = [int]$document.ComputeStatistics(2)
}
catch {
    # Word is optional: use LibreOffice below when its COM component is absent or unusable.
    $wordError = $_.Exception.Message
}
finally {
    if ($document) { try { $document.Close($false) } catch { } }
    if ($word) { try { $word.Quit() } catch { } }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

if (-not (Test-Path -LiteralPath $pdfPath)) {
    $sofficeCandidates = @(
        (Get-Command soffice.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source),
        (Join-Path $env:ProgramFiles 'LibreOffice\program\soffice.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'LibreOffice\program\soffice.exe')
    )
    $soffice = $sofficeCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
    if (-not $soffice) {
        $reason = if ($wordError) { " Word: $wordError" } else { '' }
        throw "No DOCX preview converter is available.$reason"
    }

    $profileDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("sop-preview-" + [Guid]::NewGuid().ToString('N'))
    $profileUri = [System.Uri]::new($profileDirectory).AbsoluteUri
    try {
        $process = Start-Process -FilePath $soffice -ArgumentList @(
            '--headless', '--nologo', '--nodefault', '--nolockcheck',
            "-env:UserInstallation=$profileUri", '--convert-to', 'pdf:writer_pdf_Export',
            '--outdir', $outputRoot, $inputFile
        ) -Wait -PassThru -WindowStyle Hidden
        if ($process.ExitCode -ne 0) {
            throw "LibreOffice failed with exit code $($process.ExitCode)."
        }
    }
    finally {
        if (Test-Path -LiteralPath $profileDirectory) {
            Remove-Item -LiteralPath $profileDirectory -Recurse -Force
        }
    }
    if (-not (Test-Path -LiteralPath $pdfPath)) {
        throw "LibreOffice did not create PDF preview: $pdfPath"
    }
    $conversionBackend = 'LibreOffice'
}
else {
    $conversionBackend = 'Microsoft Word'
}

Get-ChildItem -LiteralPath $outputRoot -Filter 'page-*.png' -ErrorAction SilentlyContinue | Remove-Item -Force
$pdftocairo = (Get-Command pdftocairo.exe -ErrorAction SilentlyContinue).Source
if ($pdftocairo) {
    & $pdftocairo -png -r 110 $pdfPath (Join-Path $outputRoot 'page')
    if ($LASTEXITCODE -ne 0) { throw "pdftocairo failed with exit code $LASTEXITCODE" }
}
else {
    $pdftoppm = Get-Command pdftoppm.exe -ErrorAction SilentlyContinue
    if ($pdftoppm) {
        & $pdftoppm.Source -png -r 110 $pdfPath (Join-Path $outputRoot 'page')
        if ($LASTEXITCODE -ne 0) { throw "pdftoppm failed with exit code $LASTEXITCODE" }
    }
}

$pages = @(Get-ChildItem -LiteralPath $outputRoot -Filter 'page-*.png' | Sort-Object Name)
if ($pages.Count -eq 0) {
    $projectRoot = Split-Path -Parent $PSScriptRoot
    $pythonCandidates = @(
        $env:SOP_PREVIEW_PYTHON,
        (Join-Path $projectRoot '.venv\Scripts\python.exe'),
        (Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source)
    )
    $python = $pythonCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
    if (-not $python) {
        throw 'No PDF page renderer is available.'
    }
    $rendererScript = Join-Path $PSScriptRoot 'render_pdf_pages.py'
    & $python $rendererScript '--input' $pdfPath '--output-directory' $outputRoot '--dpi' '110'
    if ($LASTEXITCODE -ne 0) {
        throw "PyMuPDF page rendering failed with exit code $LASTEXITCODE."
    }
    $pages = @(Get-ChildItem -LiteralPath $outputRoot -Filter 'page-*.png' | Sort-Object Name)
}
if ($pages.Count -eq 0) {
    throw 'PDF preview did not produce page images.'
}

[pscustomobject]@{
    pdf_path = $pdfPath
    page_count = if ($pages.Count -gt 0) { $pages.Count } else { $pageCount }
    page_paths = @($pages.FullName)
    conversion_backend = $conversionBackend
} | ConvertTo-Json -Depth 3
