param([string]$Python = "$PSScriptRoot\work\venv\Scripts\python.exe", [string]$Destination = "$PSScriptRoot\dist")
$ErrorActionPreference = 'Stop'
$originalSearchPath = $env:PATH
try {
    # Qt uses the Windows ICU DLL. Unrelated runtimes on PATH can contain an
    # incompatible icuuc.dll (e.g. Poppler with renamed ICU entry points).
    $env:PATH = "$env:WINDIR\System32;$env:WINDIR;$([IO.Path]::GetDirectoryName($Python))"
    & $Python -m PyInstaller --noconfirm --onedir --windowed --name AemeathPet --paths "$PSScriptRoot\src" --add-data "$PSScriptRoot\assets\spritesheet.png;assets" --add-data "$PSScriptRoot\assets\eating.png;assets" --add-data "$PSScriptRoot\assets\sleep.png;assets" --add-data "$PSScriptRoot\assets\celebrate.png;assets" --add-data "$PSScriptRoot\assets\workbench.png;assets" --add-data "$PSScriptRoot\assets\thinking.png;assets" --add-data "$PSScriptRoot\assets\completion.wav;assets" --icon "$PSScriptRoot\assets\pet.ico" --workpath "$PSScriptRoot\work\build-isolated" --specpath "$PSScriptRoot\work" --distpath $Destination "$PSScriptRoot\launch.py"
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
} finally {
    $env:PATH = $originalSearchPath
}
Copy-Item -LiteralPath "$PSScriptRoot\README.md","$PSScriptRoot\NOTICE.md","$PSScriptRoot\asset-prompts.txt","$PSScriptRoot\activity-prompts.txt" -Destination "$Destination\AemeathPet"
Copy-Item -LiteralPath "$PSScriptRoot\licenses" -Destination "$Destination\AemeathPet" -Recurse -Force
