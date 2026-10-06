# Fork every drpwchen repo relevant to research / notes / web into your account (Windows PowerShell).
# Requires: GitHub CLI, logged in.   winget install GitHub.cli ; gh auth login
$repos = @(
  "textbook-to-note","paper-radar","paper-fetch","paper-review-and-digest",
  "vault-search","note-supplement","openevidence-tools","ytscribe","asr-benchmark",
  "exam-practice","chart-scrub","evernote-rescue","codex-pacer"
)
$me = gh api user -q .login
foreach ($r in $repos) {
  gh repo view "$me/$r" *> $null
  if ($LASTEXITCODE -eq 0) { Write-Host "skip  $r (already in your account)" }
  else { Write-Host "fork  drpwchen/$r"; gh repo fork "drpwchen/$r" --clone=false --remote=false }
}
