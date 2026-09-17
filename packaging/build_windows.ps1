[CmdletBinding()]
param(
    [string]$Python = "python",
    [ValidatePattern('^v[0-9]+\.[0-9]+-test[0-9]+$')]
    [string]$Version = "v0.17-test2"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BuildBase = Join-Path $ProjectRoot "build"
$BuildRoot = Join-Path $BuildBase $Version
$DistRoot = Join-Path $ProjectRoot "dist"
$PackageName = "TJU_Info_Retrieval_$Version"
$PackageDir = Join-Path $DistRoot $PackageName
$ZipPath = Join-Path $DistRoot "TJU_Info_Retrieval_${Version}_win64.zip"
$SpecPath = Join-Path $PSScriptRoot "tju_info_retrieval.spec"

function Assert-ChildPath([string]$Path, [string]$Parent) {
    $full = [System.IO.Path]::GetFullPath($Path)
    $parentFull = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    if (-not $full.StartsWith($parentFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing operation outside project root: $full"
    }
}

& $Python -c "import platform,struct,sys; assert sys.platform == 'win32'; assert struct.calcsize('P') == 8; print(platform.python_version(), 'Windows x64')"
if ($LASTEXITCODE -ne 0) { throw "Python must be Windows x64" }
& $Python -c "import PyInstaller,playwright,PySide6; print('PyInstaller',PyInstaller.__version__); print('Playwright',playwright.__version__ if hasattr(playwright,'__version__') else 'installed'); print('PySide6',PySide6.__version__)"
if ($LASTEXITCODE -ne 0) { throw "Packaging dependencies are missing" }

Assert-ChildPath $BuildRoot $ProjectRoot
Assert-ChildPath $PackageDir $DistRoot
Assert-ChildPath $ZipPath $DistRoot
if (Test-Path -LiteralPath $BuildRoot) { Remove-Item -LiteralPath $BuildRoot -Recurse -Force }
if (Test-Path -LiteralPath $PackageDir) { Remove-Item -LiteralPath $PackageDir -Recurse -Force }
if (Test-Path -LiteralPath $ZipPath) { Remove-Item -LiteralPath $ZipPath -Force }
if (-not (Test-Path -LiteralPath $DistRoot)) {
    New-Item -ItemType Directory -Path $DistRoot | Out-Null
}

$env:TJU_PACKAGE_VERSION = $Version
& $Python -m PyInstaller --noconfirm --clean --workpath $BuildRoot --distpath $DistRoot $SpecPath
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$Commit = (& git -C $ProjectRoot rev-parse HEAD 2>$null)
if ($LASTEXITCODE -ne 0) { $Commit = "unavailable" }
$BuildTime = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$VersionText = @"
Version:
$Version

Build type:
Windows x64 onedir test build

Packaging:
PyInstaller 6.16.0

UI:
Responsive / HiDPI hotfix included

Baseline:
1096 passed
20 subtests passed
0 failed
0 errors
(current UI Hotfix official clean run)

Build time UTC:
$BuildTime

Git commit:
$Commit
"@
Set-Content -LiteralPath (Join-Path $PackageDir "version.txt") -Value $VersionText -Encoding utf8
$ReadmeSource = Get-ChildItem -LiteralPath $ProjectRoot -Filter "README_*.txt" -File |
    Select-Object -First 1
if ($null -eq $ReadmeSource) { throw "README distribution file not found" }
Copy-Item -LiteralPath $ReadmeSource.FullName -Destination $PackageDir

& $Python (Join-Path $PSScriptRoot "verify_dist.py") $PackageDir --development-root $ProjectRoot
if ($LASTEXITCODE -ne 0) { throw "Sensitive data scan failed" }

Compress-Archive -LiteralPath $PackageDir -DestinationPath $ZipPath -CompressionLevel Optimal
& $Python (Join-Path $PSScriptRoot "verify_dist.py") $PackageDir --development-root $ProjectRoot --zip $ZipPath --expected-root $PackageName
if ($LASTEXITCODE -ne 0) { throw "ZIP validation failed" }

$ExePath = (Get-ChildItem -LiteralPath $PackageDir -Filter "*.exe" -File |
    Select-Object -First 1).FullName
Write-Host "EXE: $ExePath"
Write-Host "ZIP: $ZipPath"
