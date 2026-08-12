param(
    [Parameter(Mandatory = $true)]
    [string]$DocumentPath,

    [string]$OutputDir,

    [int]$ExpectedPageCount = 2
)

$resolvedDocument = (Resolve-Path -LiteralPath $DocumentPath).Path
if (-not $OutputDir) {
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $OutputDir = Join-Path (Split-Path -Parent $resolvedDocument) "qa_render_$stamp"
}

$qaDirectory = [System.IO.Path]::GetFullPath($OutputDir)
if (Test-Path -LiteralPath $qaDirectory) {
    $existing = Get-ChildItem -LiteralPath $qaDirectory -Force -ErrorAction SilentlyContinue
    if ($existing) {
        throw "QA output directory must be empty: $qaDirectory"
    }
} else {
    New-Item -ItemType Directory -Path $qaDirectory | Out-Null
}

$pdfPath = Join-Path $qaDirectory 'sop_template_qa.pdf'
$word = $null
$document = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $document = $word.Documents.Open($resolvedDocument, $false, $true)
    $document.ExportAsFixedFormat($pdfPath, 17)
} finally {
    if ($document -ne $null) {
        try { $document.Close($false) } catch {}
    }
    if ($word -ne $null) {
        try { $word.Quit() } catch {}
    }
}

$pdftoppm = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\native\poppler\Library\bin\pdftoppm.exe'
if (-not (Test-Path -LiteralPath $pdftoppm)) {
    $command = Get-Command pdftoppm -ErrorAction SilentlyContinue
    if ($command) {
        $pdftoppm = $command.Source
    } else {
        throw 'pdftoppm was not found. Load Codex workspace dependencies before rendering.'
    }
}

$pagePrefix = Join-Path $qaDirectory 'page'
& $pdftoppm -png -r 150 $pdfPath $pagePrefix
if ($LASTEXITCODE -ne 0) {
    throw "pdftoppm failed with exit code $LASTEXITCODE"
}

$pages = @(Get-ChildItem -LiteralPath $qaDirectory -Filter 'page-*.png' | Sort-Object Name)
if ($pages.Count -ne $ExpectedPageCount) {
    throw "Expected $ExpectedPageCount rendered pages, found $($pages.Count)."
}

$manifest = [ordered]@{
    status = 'rendered_pending_visual_inspection'
    document = $resolvedDocument
    pdf = $pdfPath
    expected_page_count = $ExpectedPageCount
    rendered_page_count = $pages.Count
    pages = @($pages | ForEach-Object { $_.FullName })
    required_review = @(
        'inspect every PNG at 100 percent zoom'
        'confirm no clipping or overlap'
        'confirm page 1 is portrait and page 2 is landscape'
        'confirm all six IE rows and the bottom signoff area remain on page 2'
        'confirm approval, audit, and author cells are blank'
    )
}
$manifestPath = Join-Path $qaDirectory 'visual_qa_manifest.json'
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding utf8
$manifest | ConvertTo-Json -Depth 5
