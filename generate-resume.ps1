param(
    [switch]$NoPause,
    [string]$Source = '',
    [string]$Output = ''
)

$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$resumeUtf8 = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = $resumeUtf8
$OutputEncoding = $resumeUtf8
$resumeExitCode = 1

try {
    $resumePython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
    $resumeRequirements = Join-Path $PSScriptRoot 'requirements.txt'
    if (-not (Test-Path -LiteralPath $resumePython -PathType Leaf)) {
        $resumeBase = Get-Command python.exe -ErrorAction SilentlyContinue
        $resumeBaseArguments = @()
        if (-not $resumeBase) {
            $resumeBase = Get-Command py.exe -ErrorAction SilentlyContinue
            $resumeBaseArguments = @('-3')
        }
        if (-not $resumeBase) {
            throw 'Install Python 3.10 or later and enable its PATH option.'
        }
        & $resumeBase.Source @resumeBaseArguments -m venv (Join-Path $PSScriptRoot '.venv')
        if ($LASTEXITCODE -ne 0) {
            throw 'Could not create the local Python environment.'
        }
    }
    $resumeProbe = "import sys,importlib.util; sys.exit(1) if importlib.util.find_spec('reportlab') is None else None; import reportlab; sys.exit(0 if reportlab.Version == '4.4.9' else 1)"
    & $resumePython -c $resumeProbe 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Installing dependencies into .venv...'
        & $resumePython -m pip install --disable-pip-version-check -r $resumeRequirements
        if ($LASTEXITCODE -ne 0) {
            throw 'Dependency installation failed.'
        }
    }
    $resumeArguments = @((Join-Path $PSScriptRoot 'resume_pdf.py'))
    if ($Source) { $resumeArguments += $Source }
    if ($Output) { $resumeArguments += @('--output', $Output) }
    & $resumePython @resumeArguments
    if ($LASTEXITCODE -ne 0) {
        throw 'PDF generation failed. Review the message above.'
    }
    Write-Host 'PDF generated successfully.' -ForegroundColor Green
    $resumeExitCode = 0
} catch {
    Write-Host ('Generation failed: {0}' -f $_.Exception.Message) -ForegroundColor Red
} finally {
    if (-not $NoPause) { [void](Read-Host 'Press Enter to close') }
}
exit $resumeExitCode
