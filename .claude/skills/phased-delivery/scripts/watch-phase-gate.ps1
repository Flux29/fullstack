<#
.SYNOPSIS
Phase gate for the phased-delivery skill (see that SKILL.md for the workflow):
zero-token automation between "PR opened" and "next phase ready".

.DESCRIPTION
  Kickoff — copy phase N's prompt to the clipboard and print it:
      pwsh watch-phase-gate.ps1 -PlanPath docs/plans/001-x.md -Phase 1 -Kickoff
  Gate    — poll PR checks; merge on green; sync local main; stage phase N+1:
      pwsh watch-phase-gate.ps1 -PlanPath docs/plans/001-x.md -Phase 1 -Pr 42

Run gate mode from the repository root of the main checkout, on branch main,
with -PlanPath repo-relative (it is quoted into agent prompts). Requires gh
(authenticated) and git. Ctrl+C is always safe: nothing merges until every
check has succeeded.
#>
param(
    [Parameter(Mandatory)][string]$PlanPath,
    [Parameter(Mandatory)][int]$Phase,
    [int]$Pr,
    [switch]$Kickoff,
    [int]$PollSeconds = 60,
    [int]$TimeoutMinutes = 90
)

$ErrorActionPreference = 'Stop'

# The plan contract the skill declares: one '## Phase N' heading per phase. A
# phase's ```prompt fence overrides the synthesized default; no fence needed.
function Get-PhasePrompt([string]$Text, [string]$Path, [int]$N) {
    $section = [regex]::Match($Text, "(?ms)^## Phase $N\b.*?(?=^## |\z)")
    if (-not $section.Success) { return $null }  # no such phase: plan complete
    $fence = [regex]::Match($section.Value, '(?ms)^```prompt\s*\r?\n(.*?)^```')
    if ($fence.Success) { return $fence.Groups[1].Value.TrimEnd() }
    return ("You are executing Phase $N of $Path in this workspace.`n" +
        "Run ``git pull --ff-only origin main``, read that plan file, and complete " +
        "Phase $N exactly per its standing rules.")
}

function Publish-PhasePrompt([string]$Text, [string]$Path, [int]$N) {
    $prompt = Get-PhasePrompt $Text $Path $N
    if ($null -eq $prompt) {
        Write-Host "No Phase $N in $Path - the plan is complete." -ForegroundColor Green
        return
    }
    Set-Clipboard -Value $prompt
    [console]::beep(880, 300)
    Write-Host ''
    Write-Host ('=' * 72) -ForegroundColor Cyan
    Write-Host " PHASE $N PROMPT IS ON YOUR CLIPBOARD" -ForegroundColor Cyan
    Write-Host ' Paste it into a fullstack chat with the workspace attached.' -ForegroundColor Cyan
    Write-Host ('=' * 72) -ForegroundColor Cyan
    Write-Host $prompt
}

function Stop-WithAlarm([string]$Message) {
    [console]::beep(220, 700)
    [console]::beep(220, 700)
    throw $Message
}

if ($Kickoff) {
    Publish-PhasePrompt (Get-Content -Path $PlanPath -Raw) $PlanPath $Phase
    return
}

if (-not $Pr) { throw 'Gate mode needs -Pr <number> (or pass -Kickoff).' }

$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
Write-Host "Gating phase $Phase on PR #${Pr}: polling checks every ${PollSeconds}s (timeout ${TimeoutMinutes}m)."

while ($true) {
    # One gh round trip per poll: state, mergeability, branch, and checks together.
    $view = gh pr view $Pr --json state,mergeable,headRefName,statusCheckRollup | ConvertFrom-Json

    if ($view.state -eq 'MERGED') {
        Write-Host "PR #$Pr is already merged - skipping to sync." -ForegroundColor Yellow
        break
    }
    if ($view.state -eq 'CLOSED') {
        Stop-WithAlarm "PR #$Pr was closed without merging - the phase did not land."
    }
    if ($view.mergeable -eq 'CONFLICTING') {
        Stop-WithAlarm "PR #$Pr conflicts with main. Rebase branch '$($view.headRefName)' host-side, force-push the branch (never main), and rerun the gate."
    }

    # Normalize the rollup: CheckRun rows carry status/conclusion, StatusContext
    # rows carry state; fold both into SUCCESS-ish / bad / pending buckets.
    $states = @(@($view.statusCheckRollup) | ForEach-Object {
            if ($_.state) { $_.state }
            elseif ($_.status -and $_.status -ne 'COMPLETED') { 'PENDING' }
            elseif ($_.conclusion) { $_.conclusion }
            else { 'PENDING' }
        })
    $bad = @($states | Where-Object { $_ -in @('FAILURE', 'ERROR', 'CANCELLED', 'TIMED_OUT', 'ACTION_REQUIRED') })
    if ($bad.Count -gt 0) {
        Stop-WithAlarm "CI failed on PR #$Pr - nothing merged. Review the failed run, fix on the branch, and rerun the gate."
    }
    $unsettled = @($states | Where-Object { $_ -notin @('SUCCESS', 'SKIPPED', 'NEUTRAL') })
    if ($states.Count -gt 0 -and $unsettled.Count -eq 0) {
        Write-Host 'All checks green - merging.' -ForegroundColor Green
        gh pr merge $Pr --merge --delete-branch
        break
    }

    if ((Get-Date) -gt $deadline) {
        Stop-WithAlarm "Timed out after ${TimeoutMinutes}m waiting on PR #$Pr checks - nothing merged."
    }
    Start-Sleep -Seconds $PollSeconds
}

# Sync the local checkout with what GitHub just merged.
git pull --ff-only origin main
if ($LASTEXITCODE -ne 0) {
    Stop-WithAlarm 'git pull --ff-only failed - local main has diverged; reconcile it by hand before the next phase.'
}
if ($view.headRefName -and (git branch --list $view.headRefName)) {
    git branch -d $view.headRefName
}

# Record progress in the plan so its Status line tracks reality.
$plan = Get-Content -Path $PlanPath -Raw
$total = ([regex]::Matches($plan, '(?m)^## Phase \d+')).Count
$updated = [regex]::Replace($plan, '(?m)^Status:.*$', "Status: phase $Phase of $total complete")
if ($updated -ne $plan) {
    Set-Content -Path $PlanPath -Value $updated -NoNewline
    Write-Host "Plan status bumped to 'phase $Phase of $total complete' (commit it with the next phase's review)."
}

Publish-PhasePrompt $updated $PlanPath ($Phase + 1)
