$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $PSScriptRoot '.runtime\python'
    $env:UV_CACHE_DIR = Join-Path $PSScriptRoot '.runtime\uv-cache'
    $projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $projectPython)) {
        uv venv --managed-python --python cpython-3.13-windows-x86 .venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the x86 Python environment.' }
    }
    uv pip install --python $projectPython -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Could not install the Python dependencies.' }
    & $projectPython main.py --init
    if ($LASTEXITCODE -ne 0) { throw 'Could not initialize the Oracle project schema.' }
} finally {
    Pop-Location
}
