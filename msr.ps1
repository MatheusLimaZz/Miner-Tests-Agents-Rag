[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ArgsList
)

$PythonExe = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    Write-Error "Ambiente virtual .venv não encontrado em $PSScriptRoot\.venv"
    exit 1
}

if ($ArgsList.Count -eq 0) {
    & $PythonExe -m msrkit.cli menu
} else {
    & $PythonExe -m msrkit.cli @ArgsList
}
