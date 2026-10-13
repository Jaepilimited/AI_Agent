param(
    [Parameter(Mandatory = $true)]
    [string]$SourcePptx,

    [Parameter(Mandatory = $true)]
    [string]$OutputDir
)

$ErrorActionPreference = 'Stop'
$source = [System.IO.Path]::GetFullPath($SourcePptx)
$output = [System.IO.Path]::GetFullPath($OutputDir)

if (-not [System.IO.File]::Exists($source)) {
    throw "PowerPoint file not found: $source"
}

[System.IO.Directory]::CreateDirectory($output) | Out-Null

$powerPoint = $null
$presentation = $null

try {
    $powerPoint = New-Object -ComObject PowerPoint.Application
    $powerPoint.AutomationSecurity = 3
    $presentation = $powerPoint.Presentations.Open($source, $true, $true, $false)

    $slides = @()
    foreach ($slide in $presentation.Slides) {
        $slideNumber = [int]$slide.SlideIndex
        $imagePath = Join-Path $output ("slide-{0}.png" -f $slideNumber)
        $slide.Export($imagePath, 'PNG', 1600, 900)

        $shapes = @()
        foreach ($shape in $slide.Shapes) {
            $text = $null
            try {
                if ($shape.HasTextFrame -and $shape.TextFrame.HasText) {
                    $text = [string]$shape.TextFrame.TextRange.Text
                }
            }
            catch {
                $text = $null
            }

            $shapes += [ordered]@{
                name = [string]$shape.Name
                type = [int]$shape.Type
                left = [double]$shape.Left
                top = [double]$shape.Top
                width = [double]$shape.Width
                height = [double]$shape.Height
                text = $text
            }
        }

        $slides += [ordered]@{
            number = $slideNumber
            image = $imagePath
            shape_count = [int]$slide.Shapes.Count
            shapes = $shapes
        }
    }

    $metadata = [ordered]@{
        source = $source
        slide_count = [int]$presentation.Slides.Count
        slide_width_points = [double]$presentation.PageSetup.SlideWidth
        slide_height_points = [double]$presentation.PageSetup.SlideHeight
        slides = $slides
    }

    $metadataPath = Join-Path $output 'deck-metadata.json'
    $metadata | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $metadataPath -Encoding utf8
    Write-Output $metadataPath
}
finally {
    if ($null -ne $presentation) {
        try { $presentation.Close() } catch { Write-Warning ("Presentation close failed: " + $_.Exception.Message) }
        try { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($presentation) } catch { }
    }
    if ($null -ne $powerPoint) {
        try { $powerPoint.Quit() } catch { Write-Warning ("PowerPoint quit failed: " + $_.Exception.Message) }
        try { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($powerPoint) } catch { }
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
