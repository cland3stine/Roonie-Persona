param(
  [Parameter(Mandatory = $true)]
  [string]$Path,

  [Parameter(Mandatory = $false)]
  [string]$SessionEntryMarkdown,

  [Parameter(Mandatory = $false)]
  [string]$SessionEntryPath,

  [Parameter(Mandatory = $false)]
  [int]$AheadCommits = 0
)

$ErrorActionPreference = "Stop"

function Get-UtcStamp {
  return (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}

$now = Get-UtcStamp

if (-not (Test-Path -LiteralPath $Path)) {
  throw "File not found: $Path"
}

$content = Get-Content -LiteralPath $Path -Raw -Encoding UTF8

$entry = ""
if ($SessionEntryPath) {
  if (-not (Test-Path -LiteralPath $SessionEntryPath)) {
    throw "Session entry file not found: $SessionEntryPath"
  }
  $entry = Get-Content -LiteralPath $SessionEntryPath -Raw -Encoding UTF8
} elseif ($SessionEntryMarkdown) {
  $entry = $SessionEntryMarkdown
} else {
  throw "Provide -SessionEntryMarkdown or -SessionEntryPath."
}

$entry = $entry -replace '\{\{UTC\}\}', $now

# Update header Last Updated.
$content = [regex]::Replace(
  $content,
  'Last Updated \(UTC\): .*',
  ('Last Updated (UTC): ' + $now),
  1
)

# Update RISK-001 line for ahead commits if provided.
if ($AheadCommits -gt 0) {
  $content = [regex]::Replace(
    $content,
    '\[RISK-001\] Local repo is ahead of `origin/main` by \d+ commits; push currently blocked due to network connectivity\.',
    ('[RISK-001] Local repo is ahead of `origin/main` by ' + $AheadCommits + ' commits; push currently blocked due to network connectivity.'),
    1
  )
}

# Update RISK-001 Last Reviewed stamp (first occurrence only).
$content = [regex]::Replace(
  $content,
  '(1\. \[RISK-001\][^\r\n]*\r?\n\s*Opened \(UTC\):[^\r\n]*\r?\n\s*Last Reviewed \(UTC\): )([^\r\n]+)',
  ('$1' + $now),
  1
)

# Append session entry if it isn't already present.
if ($content -notmatch [regex]::Escape($entry.Trim())) {
  $content = $content.TrimEnd() + "`r`n`r`n" + $entry.Trim() + "`r`n"
}

Set-Content -LiteralPath $Path -Value $content -Encoding UTF8

Write-Host ("Updated: " + $Path)
Write-Host ("UTC: " + $now)
