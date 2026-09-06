param(
    [string]$CMake = "cmake",
    [string]$Ninja = "ninja",
    [string]$ArmToolchainBin = ""
)
$ErrorActionPreference = "Stop"
if ($ArmToolchainBin) { $env:PATH = "$ArmToolchainBin;$env:PATH" }
$cmake = (Get-Command $CMake -ErrorAction Stop).Source
$ninja = (Get-Command $Ninja -ErrorAction Stop).Source
$toolchain = Join-Path $PSScriptRoot "cubeide-gcc.cmake"

& $cmake -S $PSScriptRoot -B (Join-Path $PSScriptRoot "build") -G Ninja "-DCMAKE_MAKE_PROGRAM=$ninja" "-DCMAKE_TOOLCHAIN_FILE=$toolchain" -DCMAKE_BUILD_TYPE=Debug
if ($LASTEXITCODE -ne 0) { throw "CMake configuration failed" }
& $cmake --build (Join-Path $PSScriptRoot "build") -j 8
if ($LASTEXITCODE -ne 0) { throw "Firmware build failed" }
