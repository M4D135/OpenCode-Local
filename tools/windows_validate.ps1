#requires -Version 7.0
<#
.SYNOPSIS
Validate the exact Windows artifacts without downloading or loading a model.
.DESCRIPTION
Native verifies the launcher, assembler and uninstaller for each selected pack,
runs the production assembler and launcher diagnostic, and executes small C++
fixtures plus the production NSIS path/64-bit-size harnesses.
Setup verifies each final Setup.exe, tests its NSIS archive and checks that the
three embedded home-built PEs exactly match the supplied native artifacts.

RequireSigned requires a trusted Authenticode signature, the exact configured
publisher and certificate SHA-256, a timestamp certificate, and a successful
signtool verify /pa /all /tw /v (exit 0). No trust stores, execution policies,
antivirus settings or firewall settings are changed.

The launcher diagnostic runs in an isolated copy without a model. Exit 2 is
expected and is reported as an incomplete-package diagnostic, never as a
successful model or RAM qualification. Existing application user profiles are
left alone; that diagnostic refuses to run if one already exists.
.EXAMPLE
pwsh -File tools/windows_validate.ps1 -Phase Native -NativeRoot signing/native -ReportPath validation/windows-native-unsigned.json
.EXAMPLE
pwsh -File tools/windows_validate.ps1 -Phase Setup -NativeRoot signed/native -SetupRoot signed/setup -RequireSigned -ExpectedPublisher 'CN=the approved publisher' -ExpectedSignerSha256 '<64 hexadecimal characters>' -ReportPath validation/windows-setup-signed.json
.NOTES
Run on a standard x64 Windows runner with Python 3, NSIS, 7-Zip and Visual
Studio C++ Build Tools. RequireSigned also needs the Windows SDK SignTool.
The script discovers installed tools or accepts their explicit paths.
No complete production install/uninstall or model inference is claimed.
#>
[CmdletBinding()]
param(
    [ValidateSet('Native', 'Setup', 'All')][string]$Phase = 'All',
    [string]$NativeRoot = 'signing/native',
    [Alias('OutputRoot')][string]$SetupRoot = 'signing/setup',
    [string]$WorkRoot = 'build/signing',
    [ValidateSet('16go', '32go')][string[]]$Pack = @('16go', '32go'),
    [string]$ReportPath = 'validation/windows-validation.json',
    [switch]$RequireSigned,
    [string]$ExpectedPublisher = '',
    [string]$ExpectedSignerSha256 = '',
    [string]$SignToolPath = '',
    [string]$SevenZipPath = '',
    [string]$MakensisPath = '',
    [string]$PythonPath = '',
    [string]$CxxPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:Repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$script:Checks = [Collections.Generic.List[object]]::new()
$script:CleanupNotes = [Collections.Generic.List[object]]::new()
$script:SignatureTool = $null
$script:OwnerToken = [Guid]::NewGuid().ToString('N')
$script:TempRoot = $null
$script:Configuration = $null
$started = [DateTime]::UtcNow
$exitCode = 1

function Resolve-InputPath([string]$Value) {
    if ([IO.Path]::IsPathRooted($Value)) { return [IO.Path]::GetFullPath($Value) }
    return [IO.Path]::GetFullPath((Join-Path $script:Repository $Value))
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-BytesSha256([byte[]]$Bytes) {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return [Convert]::ToHexString($algorithm.ComputeHash($Bytes)).ToLowerInvariant() }
    finally { $algorithm.Dispose() }
}

function Invoke-External {
    param([string]$FilePath, [string[]]$Arguments = @(), [int]$TimeoutSeconds = 90,
          [string]$WorkingDirectory = $script:Repository)
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $FilePath
    $start.WorkingDirectory = $WorkingDirectory
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    foreach ($argument in $Arguments) { $start.ArgumentList.Add($argument) }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    $timedOut = $false
    try {
        [void]$process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $timedOut = $true
            $process.Kill($true)
            $process.WaitForExit()
        }
        $peak = $null
        try { $peak = $process.PeakWorkingSet64 } catch { }
        return [ordered]@{
            exit_code = $process.ExitCode; timed_out = $timedOut
            stdout = $stdout.GetAwaiter().GetResult(); stderr = $stderr.GetAwaiter().GetResult()
            seconds = [Math]::Round($watch.Elapsed.TotalSeconds, 3); peak_working_set_bytes = $peak
        }
    }
    catch {
        return [ordered]@{ exit_code = $null; timed_out = $timedOut; stdout = ''
            stderr = $_.Exception.Message; seconds = [Math]::Round($watch.Elapsed.TotalSeconds, 3) }
    }
    finally { $process.Dispose(); $watch.Stop() }
}

function Invoke-Check([string]$CheckName, [scriptblock]$Body) {
    try {
        $detail = & $Body
        if ($null -eq $detail -or $detail -isnot [Collections.IDictionary] -or -not $detail.Contains('passed')) {
            throw "The validation check did not produce an explicit pass/fail result."
        }
        $passed = [bool]$detail.passed
        $script:Checks.Add([ordered]@{ name = $CheckName; passed = $passed; details = $detail })
    }
    catch {
        $passed = $false
        $script:Checks.Add([ordered]@{ name = $CheckName; passed = $false
            error = $_.Exception.Message; position = $_.InvocationInfo.PositionMessage })
    }
    $label = if ($passed) { 'PASS ' } else { 'FAIL ' }
    Write-Host ($label + $CheckName)
    return $passed
}

function Find-Tool([string]$Requested, [string[]]$Names, [string[]]$Candidates = @()) {
    if ($Requested) {
        $command = Get-Command $Requested -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command) { return $command.Source }
        $path = Resolve-InputPath $Requested
        if (Test-Path -LiteralPath $path -PathType Leaf) { return $path }
        throw "Tool not found: $Requested"
    }
    foreach ($name in $Names) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command) { return $command.Source }
    }
    foreach ($path in $Candidates) {
        if ($path -and (Test-Path -LiteralPath $path -PathType Leaf)) { return $path }
    }
    throw ('Required tool not found: ' + ($Names -join ', '))
}

function Find-SignTool {
    $candidates = @()
    $sdk = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits/10/bin'
    if (Test-Path -LiteralPath $sdk -PathType Container) {
        $versions = Get-ChildItem -LiteralPath $sdk -Directory | Sort-Object Name -Descending
        $candidates = @($versions | ForEach-Object { Join-Path $_.FullName 'x64/signtool.exe' })
    }
    return Find-Tool $SignToolPath @('signtool.exe') $candidates
}

function Get-PeMetadata([string]$Path) {
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $reader = [IO.BinaryReader]::new($stream)
    try {
        if ($stream.Length -lt 128 -or $reader.ReadUInt16() -ne 0x5A4D) { throw "Not a DOS/PE executable: $Path" }
        $stream.Position = 0x3C
        $offset = $reader.ReadUInt32()
        if ($offset -gt $stream.Length - 88) { throw "Invalid PE header offset: $Path" }
        $stream.Position = $offset
        if ($reader.ReadUInt32() -ne 0x00004550) { throw "Missing PE signature: $Path" }
        $machine = $reader.ReadUInt16()
        $sections = $reader.ReadUInt16()
        $stream.Position = $offset + 20
        $optionalSize = $reader.ReadUInt16()
        $characteristics = $reader.ReadUInt16()
        $optional = $offset + 24
        $stream.Position = $optional
        $magic = $reader.ReadUInt16()
        if ($magic -notin @(0x10B, 0x20B) -or $optionalSize -lt 144) { throw "Invalid PE optional header: $Path" }
        $stream.Position = $optional + 68
        $subsystem = $reader.ReadUInt16()
        $stream.Position = $optional + $(if ($magic -eq 0x20B) { 144 } else { 128 })
        $certificateOffset = $reader.ReadUInt32()
        $certificateBytes = $reader.ReadUInt32()
        if ([uint64]$certificateOffset + [uint64]$certificateBytes -gt [uint64]$stream.Length) {
            throw "Invalid PE certificate table: $Path"
        }
        $version = [Diagnostics.FileVersionInfo]::GetVersionInfo($Path)
        return [ordered]@{
            machine = ('0x{0:X4}' -f $machine)
            architecture = $(switch ($machine) { 0x8664 { 'x64' } 0x014C { 'x86' } default { 'other' } })
            optional_header = $(if ($magic -eq 0x20B) { 'PE32+' } else { 'PE32' })
            subsystem = $subsystem; sections = $sections; characteristics = $characteristics
            file_version = $version.FileVersion; product_version = $version.ProductVersion
            product_name = $version.ProductName; original_filename = $version.OriginalFilename
            file_description = $version.FileDescription; company_name = $version.CompanyName
            certificate_table_bytes = $certificateBytes; file_bytes = $stream.Length
        }
    }
    finally { $reader.Dispose(); $stream.Dispose() }
}

function Get-SignatureResult([string]$Path) {
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    $status = $signature.Status.ToString()
    $signer = $signature.SignerCertificate
    $timestamp = $signature.TimeStamperCertificate
    $publisher = if ($signer) { $signer.Subject } else { $null }
    $certificateHash = if ($signer) { Get-BytesSha256 $signer.RawData } else { $null }
    $verification = $null
    $problems = [Collections.Generic.List[string]]::new()
    if ($RequireSigned) {
        if ($status -ne 'Valid') { $problems.Add("Authenticode status is $status") }
        if ($signature.SignatureType.ToString() -ne 'Authenticode') { $problems.Add('An embedded Authenticode signature is required') }
        if (-not $signer -or -not [string]::Equals($publisher, $ExpectedPublisher, [StringComparison]::Ordinal)) {
            $problems.Add('Publisher does not match ExpectedPublisher exactly')
        }
        if (-not $certificateHash -or $certificateHash -ne $ExpectedSignerSha256.ToLowerInvariant()) {
            $problems.Add('Signing certificate DER SHA-256 does not match ExpectedSignerSha256')
        }
        if (-not $timestamp) { $problems.Add('No Authenticode timestamp certificate was found') }
        if (-not $script:SignatureTool) { $problems.Add('Windows SDK SignTool was not found') }
        else {
            $verification = Invoke-External $script:SignatureTool @('verify', '/pa', '/all', '/tw', '/v', $Path) 120
            if ($verification.exit_code -ne 0 -or $verification.timed_out) {
                $problems.Add('signtool verify /pa /all /tw /v did not exit successfully')
            }
        }
    }
    elseif ($status -notin @('NotSigned', 'Valid')) {
        $problems.Add("An existing signature is invalid: $status")
    }
    return [ordered]@{
        required = [bool]$RequireSigned; status = $status; status_message = $signature.StatusMessage
        signature_type = $signature.SignatureType.ToString(); publisher = $publisher
        signer_certificate_sha256 = $certificateHash
        timestamp_present = [bool]$timestamp
        timestamp_subject = $(if ($timestamp) { $timestamp.Subject } else { $null })
        signtool = $verification; passed = $problems.Count -eq 0; problems = @($problems)
    }
}

function Inspect-Artifact([string]$Path, [string]$ExpectedName, [string]$Architecture) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Missing artifact: $Path" }
    $metadata = Get-PeMetadata $Path
    $signature = Get-SignatureResult $Path
    $problems = [Collections.Generic.List[string]]::new()
    if ($metadata.architecture -ne $Architecture) { $problems.Add("Expected $Architecture, found $($metadata.architecture)") }
    if ($metadata.original_filename -ne $ExpectedName) { $problems.Add("OriginalFilename must be $ExpectedName") }
    if ($metadata.product_name -ne 'OpenCode Local') { $problems.Add('ProductName must be OpenCode Local') }
    $expectedVersion = [string]$script:Configuration.app_version
    if ($metadata.product_version -notin @($expectedVersion, "$expectedVersion.0")) { $problems.Add('ProductVersion does not match config/packs.json') }
    if ($metadata.file_version -notin @($expectedVersion, "$expectedVersion.0")) { $problems.Add('FileVersion does not match config/packs.json') }
    $expectedSubsystem = if ($ExpectedName -eq 'AssembleModel.exe') { 3 } else { 2 }
    if ($metadata.subsystem -ne $expectedSubsystem) { $problems.Add("Unexpected PE subsystem $($metadata.subsystem)") }
    return [ordered]@{ path = $Path; sha256 = Get-Sha256 $Path; metadata = $metadata
        authenticode = $signature; problems = @($problems)
        passed = $problems.Count -eq 0 -and $signature.passed }
}

function Assert-NoReparsePath([string]$Path) {
    $cursor = [IO.Path]::GetFullPath($Path)
    while ($cursor) {
        if ([IO.File]::Exists($cursor) -or [IO.Directory]::Exists($cursor)) {
            if (([IO.File]::GetAttributes($cursor) -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Refusing a reparse point in a test path: $cursor"
            }
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
}

function Remove-OwnedTestDirectory {
    if (-not $script:TempRoot -or -not (Test-Path -LiteralPath $script:TempRoot)) { return }
    Assert-NoReparsePath $script:TempRoot
    $marker = Join-Path $script:TempRoot 'test-owner.txt'
    if (-not (Test-Path -LiteralPath $marker -PathType Leaf) -or
        [IO.File]::ReadAllText($marker) -ne $script:OwnerToken) {
        throw 'Temporary test directory ownership could not be verified; it was preserved.'
    }
    # Explicit traversal does not descend into junctions. Reject them before
    # asking .NET to remove the directory created exclusively by this run.
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($script:TempRoot)
    while ($pending.Count) {
        foreach ($item in [IO.Directory]::EnumerateFileSystemEntries($pending.Pop())) {
            $attributes = [IO.File]::GetAttributes($item)
            if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Test directory contains a reparse point; preserved: $item" }
            if (($attributes -band [IO.FileAttributes]::Directory) -ne 0) { $pending.Push($item) }
        }
    }
    [IO.Directory]::Delete($script:TempRoot, $true)
}

function Invoke-LauncherDiagnostic([string]$Name) {
    $profile = $script:Configuration.packs[$Name]
    $userDirectory = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) $profile.profile_name
    $documents = [Environment]::GetFolderPath([Environment+SpecialFolder]::MyDocuments, [Environment+SpecialFolderOption]::DoNotVerify)
    $projectDirectory = Join-Path $documents $profile.profile_name
    foreach ($path in @($userDirectory, $projectDirectory)) {
        Assert-NoReparsePath $path
        if (Test-Path -LiteralPath $path) { throw "Diagnostic requires a fresh runner profile; preserving existing directory: $path" }
    }
    $folder = Join-Path $script:TempRoot "$Name/launcher-only"
    [void][IO.Directory]::CreateDirectory($folder)
    $exe = Join-Path $folder 'OpenCodeLocal.exe'
    $original = Join-Path $NativeRoot "$Name/OpenCodeLocal.exe"
    [IO.File]::Copy($original, $exe, $false)
    $diagnostic = Join-Path $userDirectory 'logs/diagnostic.txt'
    $result = $null
    try {
        $execution = Invoke-External $exe @('/diagnostic') 60 $folder
        $text = if (Test-Path -LiteralPath $diagnostic -PathType Leaf) { [IO.File]::ReadAllText($diagnostic) } else { '' }
        $passed = ($execution.exit_code -eq 2 -and -not $execution.timed_out -and
            $text.Contains([string]$profile.model.filename) -and $text.Contains([string]$profile.model.sha256) -and
            $text.Contains('[ERREUR]') -and $text.Contains('aucun moteur API actif') -and
            (Get-Sha256 $exe) -eq (Get-Sha256 $original))
        $result = [ordered]@{ passed = $passed; execution = $execution; diagnostic = $text
            expected_exit = 2; outcome = 'Incomplete-package diagnostic; model intentionally absent'
            model_loaded = $false; model_or_memory_qualified = $false
            directories_created_by_launcher = @($userDirectory, $projectDirectory)
            cleanup = 'Only the new diagnostic file and empty new directories are removed' }
    }
    finally {
        # These locations were absent before this call. Never recurse, and
        # preserve any unexpected files a concurrent process might have added.
        try {
            Assert-NoReparsePath $diagnostic
            if ([IO.File]::Exists($diagnostic)) { [IO.File]::Delete($diagnostic) }
            foreach ($path in @((Join-Path $userDirectory 'logs'), (Join-Path $userDirectory 'Projets'), $userDirectory, $projectDirectory)) {
                Assert-NoReparsePath $path
                if ([IO.Directory]::Exists($path)) { [IO.Directory]::Delete($path, $false) }
            }
        }
        catch { $script:CleanupNotes.Add([ordered]@{ path = $userDirectory; preserved = $true; reason = $_.Exception.Message }) }
    }
    return $result
}

function Initialize-Cxx {
    if ($CxxPath) { return Find-Tool $CxxPath @('cl.exe', 'g++.exe') }
    $current = Get-Command cl.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($current) { return $current.Source }
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
    if (-not (Test-Path -LiteralPath $vswhere)) { throw 'Visual Studio C++ Build Tools or -CxxPath is required for real assembler fixtures' }
    $located = Invoke-External $vswhere @('-latest', '-products', '*', '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath')
    if ($located.exit_code -ne 0 -or -not $located.stdout.Trim()) { throw 'An x64 Visual Studio C++ toolchain was not found' }
    $developerCommand = Join-Path $located.stdout.Trim() 'Common7/Tools/VsDevCmd.bat'
    if ($developerCommand -match '["&|<>\r\n]') { throw 'Unexpected Visual Studio installation path' }
    # A private batch file avoids cmd.exe's different quoting rules for a
    # nested /c command string. Its only interpolation is the installed VS
    # path, checked above; no user-provided compiler arguments enter it.
    $batch = Join-Path $script:TempRoot 'msvc-environment.cmd'
    $batchText = "@echo off`r`ncall `"$developerCommand`" -no_logo -arch=x64 -host_arch=x64 >nul`r`nif errorlevel 1 exit /b %errorlevel%`r`nset`r`n"
    [IO.File]::WriteAllText($batch, $batchText, [Text.UTF8Encoding]::new($false))
    $initialized = Invoke-External $env:ComSpec @('/d', '/c', $batch)
    if ($initialized.exit_code -ne 0) { throw ('VsDevCmd could not initialize MSVC: ' + $initialized.stderr) }
    foreach ($line in ($initialized.stdout -split "`r?`n")) {
        $separator = $line.IndexOf('=')
        if ($separator -gt 0) { [Environment]::SetEnvironmentVariable($line.Substring(0, $separator), $line.Substring($separator + 1), 'Process') }
    }
    return Find-Tool '' @('cl.exe')
}

function Invoke-PythonValidation([string]$Tool, [string[]]$Arguments, [string]$ComponentReport) {
    $commandArguments = @((Join-Path $PSScriptRoot $Tool)) + $Arguments + @('--report', $ComponentReport)
    $execution = Invoke-External $PythonPath $commandArguments 420
    $data = if (Test-Path -LiteralPath $ComponentReport -PathType Leaf) {
        Get-Content -LiteralPath $ComponentReport -Raw | ConvertFrom-Json -AsHashtable
    } else { $null }
    return [ordered]@{ passed = $execution.exit_code -eq 0 -and -not $execution.timed_out -and $null -ne $data -and $data.passed
        execution = $execution; report_path = $ComponentReport; results = $data }
}

function Get-NsisMembers([string]$Path) {
    $listing = Invoke-External $SevenZipPath @('l', '-slt', '-sccUTF-8', $Path) 120
    if ($listing.exit_code -ne 0 -or $listing.timed_out) { throw ('7-Zip could not list the installer: ' + $listing.stderr) }
    if ($listing.stdout -notmatch '(?m)^Type = Nsis\s*$') { throw 'The file is not recognized as an NSIS installer' }
    $boundary = $listing.stdout.IndexOf('----------')
    if ($boundary -lt 0) { throw 'NSIS file listing separator is missing' }
    $members = [Collections.Generic.List[string]]::new()
    foreach ($match in [regex]::Matches($listing.stdout.Substring($boundary), '(?m)^Path = (.+?)\r?$')) {
        $path = $match.Groups[1].Value.Replace('\', '/')
        if ($path.StartsWith('/') -or $path -match '^[A-Za-z]:' -or ($path.Split('/') -contains '..')) {
            throw "Unsafe archive member: $path"
        }
        $members.Add($path)
    }
    return @($members)
}

function Inspect-SetupArchive([string]$Name) {
    $setup = Join-Path $SetupRoot "$Name/Setup.exe"
    $integrity = Invoke-External $SevenZipPath @('t', '-sccUTF-8', $setup) 300
    if ($integrity.exit_code -ne 0 -or $integrity.timed_out) {
        return [ordered]@{ passed = $false; integrity = $integrity }
    }
    $members = @(Get-NsisMembers $setup)
    $expected = [ordered]@{
        'OpenCodeLocal.exe' = '(^|/)OpenCodeLocal\.exe$'
        'AssembleModel.exe' = '^\$PLUGINSDIR/AssembleModel\.exe$'
        'Uninstall.exe' = '/wad/support/Uninstall\.exe$'
    }
    $rows = [Collections.Generic.List[object]]::new()
    foreach ($entry in $expected.GetEnumerator()) {
        $foundMembers = @($members | Where-Object { $_ -match $entry.Value })
        if ($foundMembers.Count -ne 1) { throw "Expected exactly one archive member for $($entry.Key), found $($foundMembers.Count)" }
        $destination = Join-Path $script:TempRoot "$Name/extracted/$($entry.Key)"
        [void][IO.Directory]::CreateDirectory($destination)
        $extraction = Invoke-External $SevenZipPath @('e', '-y', '-sccUTF-8', "-o$destination", $setup, $foundMembers[0]) 120
        $file = Join-Path $destination $entry.Key
        if ($extraction.exit_code -ne 0 -or $extraction.timed_out -or -not (Test-Path -LiteralPath $file -PathType Leaf)) {
            throw "Cannot extract the exact $($entry.Key) from $setup"
        }
        $original = Join-Path $NativeRoot "$Name/$($entry.Key)"
        $actualHash = Get-Sha256 $file
        $expectedHash = Get-Sha256 $original
        $architecture = if ($entry.Key -eq 'Uninstall.exe') { 'x86' } else { 'x64' }
        $inspection = Inspect-Artifact $file $entry.Key $architecture
        $rows.Add([ordered]@{ member = $foundMembers[0]; extracted_sha256 = $actualHash
            native_sha256 = $expectedHash; exact_native_match = $actualHash -eq $expectedHash
            inspection = $inspection; passed = $actualHash -eq $expectedHash -and $inspection.passed })
    }
    return [ordered]@{ passed = @($rows | Where-Object { -not $_.passed }).Count -eq 0
        archive_sha256 = Get-Sha256 $setup; archive_entries = $members.Count
        integrity = $integrity; embedded_home_built_executables = @($rows)
        production_installation_executed = $false; model_loaded = $false }
}

$ReportPath = Resolve-InputPath $ReportPath
$NativeRoot = Resolve-InputPath $NativeRoot
$SetupRoot = Resolve-InputPath $SetupRoot
$WorkRoot = Resolve-InputPath $WorkRoot
[void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($ReportPath))
try {
    if (-not $IsWindows) { throw 'This validation entry point must run on Windows; use the Python --fixtures-only/--build-only checks on Linux' }
    if (-not [Environment]::Is64BitProcess -or -not [Environment]::Is64BitOperatingSystem) { throw 'Use x64 PowerShell on an x64 Windows runner' }
    $script:Configuration = Get-Content -LiteralPath (Join-Path $script:Repository 'config/packs.json') -Raw | ConvertFrom-Json -AsHashtable
    if ($RequireSigned) {
        if (-not $ExpectedPublisher) { throw '-RequireSigned requires the exact approved -ExpectedPublisher subject' }
        if ($ExpectedSignerSha256 -notmatch '^[0-9A-Fa-f]{64}$') { throw '-RequireSigned requires -ExpectedSignerSha256 (64 hex characters, SHA-256 of the DER certificate)' }
        $script:SignatureTool = Find-SignTool
    }
    $script:TempRoot = Join-Path ([IO.Path]::GetTempPath()) ('OpenCodeLocalValidation-' + $script:OwnerToken)
    if (Test-Path -LiteralPath $script:TempRoot) { throw 'Exclusive validation directory unexpectedly exists' }
    Assert-NoReparsePath $script:TempRoot
    [void][IO.Directory]::CreateDirectory($script:TempRoot)
    [IO.File]::WriteAllText((Join-Path $script:TempRoot 'test-owner.txt'), $script:OwnerToken)
    $nativePassed = $true
    if ($Phase -in @('Native', 'All')) {
        foreach ($name in $Pack) {
            foreach ($file in @('OpenCodeLocal.exe', 'AssembleModel.exe', 'Uninstall.exe')) {
                $architecture = if ($file -eq 'Uninstall.exe') { 'x86' } else { 'x64' }
                $ok = Invoke-Check "$name/native/$file" { Inspect-Artifact (Join-Path $NativeRoot "$name/$file") $file $architecture }
                if (-not $ok) { $nativePassed = $false }
            }
        }
        if ($nativePassed) {
            foreach ($name in $Pack) { [void](Invoke-Check "$name/launcher-diagnostic" { Invoke-LauncherDiagnostic $name }) }
            $PythonPath = Find-Tool $PythonPath @('python.exe', 'python')
            $CxxPath = Initialize-Cxx
            $MakensisPath = Find-Tool $MakensisPath @('makensis.exe', 'makensis') @((Join-Path ${env:ProgramFiles(x86)} 'NSIS/makensis.exe'))
            $arguments = @('--native-root', $NativeRoot, '--cxx', $CxxPath)
            foreach ($name in $Pack) { $arguments += @('--pack', $name) }
            $assemblerReport = [IO.Path]::ChangeExtension($ReportPath, 'assembler.json')
            [void](Invoke-Check 'assembler/real-fixtures-and-production-metadata' { Invoke-PythonValidation 'windows_assembler_tests.py' $arguments $assemblerReport })
            $arguments = @('--makensis', $MakensisPath)
            foreach ($name in $Pack) { $arguments += @('--pack', $name) }
            $pathReport = [IO.Path]::ChangeExtension($ReportPath, 'installer-paths.json')
            [void](Invoke-Check 'installer/production-path-and-size-functions' { Invoke-PythonValidation 'test_installer_paths.py' $arguments $pathReport })
        }
        else {
            [void](Invoke-Check 'native/runtime-gate' { return [ordered]@{ passed = $false; reason = 'Runtime tests blocked because native metadata or signatures did not pass' } })
        }
    }
    if ($Phase -in @('Setup', 'All')) {
        $SevenZipPath = Find-Tool $SevenZipPath @('7z.exe', '7zz.exe', '7z') @((Join-Path $env:ProgramFiles '7-Zip/7z.exe'))
        foreach ($name in $Pack) {
            $ok = Invoke-Check "$name/setup/metadata-and-signature" { Inspect-Artifact (Join-Path $SetupRoot "$name/Setup.exe") 'Setup.exe' 'x86' }
            if ($ok) { [void](Invoke-Check "$name/setup/archive-and-exact-native-payload" { Inspect-SetupArchive $name }) }
        }
    }
    if ($script:Checks.Count -gt 0 -and @($script:Checks | Where-Object { -not $_.passed }).Count -eq 0) { $exitCode = 0 }
}
catch {
    $script:Checks.Add([ordered]@{ name = 'validation/prerequisite-or-orchestration'; passed = $false
        error = $_.Exception.Message; position = $_.InvocationInfo.PositionMessage })
    Write-Host ('FAIL ' + $_.Exception.Message)
}
finally {
    try { Remove-OwnedTestDirectory }
    catch { $exitCode = 1; $script:CleanupNotes.Add([ordered]@{ path = $script:TempRoot; preserved = $true; reason = $_.Exception.Message }) }
    $report = [ordered]@{
        schema_version = 1; phase = $Phase; packs = @($Pack); passed = $exitCode -eq 0
        started_utc = $started.ToString('o'); finished_utc = [DateTime]::UtcNow.ToString('o')
        platform = [Environment]::OSVersion.VersionString; powershell = $PSVersionTable.PSVersion.ToString()
        native_root = $NativeRoot; setup_root = $SetupRoot; build_root = $WorkRoot
        signature_policy = $(if ($RequireSigned) { 'Trusted, timestamped Authenticode required; exact publisher and certificate SHA-256' } else { 'Signature optional; exact observed status reported; invalid signatures fail' })
        expected_publisher = $ExpectedPublisher; expected_signer_sha256 = $ExpectedSignerSha256
        signtool = $script:SignatureTool; model_downloaded = $false; model_loaded = $false
        production_installation_executed = $false; model_or_ram_qualification = $false
        checks = @($script:Checks); cleanup_notes = @($script:CleanupNotes)
    }
    [IO.File]::WriteAllText($ReportPath, ($report | ConvertTo-Json -Depth 40) + "`n", [Text.UTF8Encoding]::new($false))
    Write-Host "Validation report: $ReportPath"
}
exit $exitCode
