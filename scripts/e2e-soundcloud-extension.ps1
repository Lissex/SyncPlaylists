<#
.SYNOPSIS
  Ручной e2e 4c-3: Яндекс → новый сет SoundCloud через браузерное расширение.

.DESCRIPTION
  Входит под пользователем SyncPlaylists, к которому привязано расширение (пароль
  спрашивается и не печатается). Подключает Яндекс токеном YANDEX_LIVE_TOKEN из .env, если
  он ещё не подключён. Проверяет, что SoundCloud подключён через расширение
  (transport=extension), и запускает перенос по ссылке в новый приватный сет SoundCloud.
  Дальше раз в 3 с печатает статус, а на паузе — и её причину (paused_client: offline,
  no_permission, logged_out, captcha, ...). Скрипт ждёт и дальше: так можно посреди
  записи закрыть браузер или отозвать разрешение и увидеть, как перенос встал и продолжился.

  Приложение должно быть запущено (make up), расширение — собрано, загружено и привязано.

.EXAMPLE
  .\scripts\e2e-soundcloud-extension.ps1 -Email me@example.com -Link "https://music.yandex.ru/users/<login>/playlists/<n>"
.EXAMPLE
  .\scripts\e2e-soundcloud-extension.ps1 -Email me@example.com -Link "..." -AcceptUncertain -Title "SP test 4c-3 #2"
#>
param(
  [Parameter(Mandatory = $true)][string]$Email,
  [Parameter(Mandatory = $true)][string]$Link,
  [string]$Title = "",
  [switch]$AcceptUncertain,
  [string]$Api = "http://localhost:8000",
  [int]$TimeoutMinutes = 60
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession

function Step($text) { Write-Host "`n=== $text" -ForegroundColor Cyan }

function Get-EnvValue($name) {
  $line = Get-Content (Join-Path $root ".env") -Encoding UTF8 |
    Where-Object { $_ -match "^\s*$name=" } | Select-Object -First 1
  if (-not $line) { return $null }
  return ($line -replace "^\s*$name=", "").Trim().Trim('"')
}

# PowerShell 5.1 портит кириллицу в запросах и ответах — шлём и читаем UTF-8 явно.
function Api($method, $path, $body = $null) {
  $params = @{
    Method = $method; Uri = "$Api$path"; WebSession = $session; UseBasicParsing = $true
    ContentType = "application/json; charset=utf-8"
  }
  if ($null -ne $body) {
    $params.Body = [Text.Encoding]::UTF8.GetBytes(($body | ConvertTo-Json -Depth 10))
  }
  try {
    $response = Invoke-WebRequest @params
  } catch {
    $status = $null
    if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
    throw "HTTP $status на $method $path : $($_.ErrorDetails.Message)"
  }
  $text = [Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray())
  if ($text) { return $text | ConvertFrom-Json }
  return $null
}

# PowerShell 5.1: ConvertFrom-Json отдаёт JSON-массив одним объектом, а не списком —
# фильтры по его элементам тогда сравнивают весь массив. Раскладываем в плоский список.
function Expand-Items($items) {
  foreach ($item in $items) {
    if ($item -is [array]) { Expand-Items $item } else { $item }
  }
}

function Show-Transfer($transfer) {
  $p = $transfer.progress
  $line = "{0}  {1,-14}" -f (Get-Date -Format HH:mm:ss), $transfer.status
  if ($p) {
    $line += " всего $($p.total): найдено $($p.matched), сомнит. $($p.uncertain), нет $($p.not_found), записано $($p.added), ошибок $($p.failed)"
    if ($p.pause_reason) { $line += "  [пауза: $($p.pause_reason)]" }
  }
  $color = "Gray"
  if ($transfer.status -eq "paused_client") { $color = "Yellow" }
  Write-Host $line -ForegroundColor $color
}

function Wait-Transfer($transferId, [string[]]$activeStatuses) {
  $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
  $last = ""
  do {
    $transfer = Api GET "/transfers/$transferId"
    $state = "$($transfer.status) $($transfer.progress.added) $($transfer.progress.pending) $($transfer.progress.pause_reason)"
    if ($state -ne $last) {
      Show-Transfer $transfer
      if ($transfer.status -eq "paused_client") {
        Write-Host "  Перенос ждёт расширение. Продолжится сам, когда браузер/площадка снова готовы." -ForegroundColor Yellow
      }
      $last = $state
    }
    if ($transfer.status -notin $activeStatuses) { return $transfer }
    Start-Sleep 3
  } while ((Get-Date) -lt $deadline)
  throw "Перенос не закончился за $TimeoutMinutes мин. Логи: docker compose logs --tail 200 api worker-extension"
}

# --- 1. Вход ------------------------------------------------------------------------
Step "Вход в SyncPlaylists ($Email)"
Invoke-WebRequest "$Api/health" -UseBasicParsing -TimeoutSec 5 | Out-Null
$secure = Read-Host "Пароль SyncPlaylists" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
  $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
  Api POST "/auth/login" @{ email = $Email; password = $password } | Out-Null
} finally {
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
  Remove-Variable password -ErrorAction SilentlyContinue
}
Write-Host "вошли"

# --- 2. Аккаунты --------------------------------------------------------------------
Step "Аккаунты площадок"
$accounts = @(Expand-Items (Api GET "/accounts"))
$yandex = $accounts | Where-Object { $_.platform -eq "yandex" -and $_.status -eq "active" } | Select-Object -First 1
if (-not $yandex) {
  $token = Get-EnvValue "YANDEX_LIVE_TOKEN"
  if (-not $token) { throw "Яндекс не подключён, и в .env нет YANDEX_LIVE_TOKEN (см. docs/YANDEX_TOKEN.md)" }
  try {
    $yandex = Api POST "/accounts" @{ platform = "yandex"; access_token = $token }
  } finally {
    Remove-Variable token
  }
}
Write-Host "Яндекс: $($yandex.display_name) ($($yandex.transport))"
$soundcloud = $accounts | Where-Object { $_.platform -eq "soundcloud" -and $_.status -eq "active" } | Select-Object -First 1
if (-not $soundcloud -or $soundcloud.transport -ne "extension") {
  throw "SoundCloud не подключён через расширение. В окне расширения включите SoundCloud, выдайте доступ и дождитесь «Подключено к SyncPlaylists»."
}
Write-Host "SoundCloud: $($soundcloud.display_name) (id $($soundcloud.external_user_id), через расширение)"

# --- 3. Источник --------------------------------------------------------------------
Step "Источник"
$resolved = Api POST "/links/resolve" @{ url = $Link }
Write-Host "$($resolved.platform) / $($resolved.kind): «$($resolved.title)», треков: $($resolved.track_count)"
if ($resolved.track_count -gt 20) {
  Write-Host "  Внимание: больше 20 треков — для проверки лучше маленький плейлист." -ForegroundColor Yellow
}

# --- 4. Перенос ---------------------------------------------------------------------
Step "Перенос в новый сет SoundCloud"
if (-not $Title) { $Title = "SP test 4c-3 $(Get-Date -Format 'dd.MM HH:mm')" }
$transfer = Api POST "/transfers" @{
  source      = @{ kind = "link"; url = $Link }
  destination = @{ kind = "new"; platform = "soundcloud"; title = $Title }
}
$transferId = $transfer.id
Write-Host "перенос: $transferId → «$Title»"
$transfer = Wait-Transfer $transferId @("queued", "running", "writing", "paused_quota", "paused_client")

if ($transfer.status -eq "review") {
  $uncertain = @($transfer.items | Where-Object status -eq "uncertain")
  if (-not $AcceptUncertain) {
    Write-Host "Ждёт ревью ($($uncertain.Count) uncertain). Перезапустите с -AcceptUncertain или решите вручную:" -ForegroundColor Yellow
    Write-Host "  POST $Api/transfers/$transferId/items/<#>/resolve  {chosen_platform, chosen_external_id} или {}"
    exit 0
  }
  Step "Принимаю первого кандидата у $($uncertain.Count) uncertain"
  foreach ($item in $uncertain) {
    $body = @{}
    if ($item.candidates.Count -gt 0) {
      $body = @{ chosen_platform = $item.candidates[0].platform; chosen_external_id = $item.candidates[0].external_id }
    }
    Api POST "/transfers/$transferId/items/$($item.position)/resolve" $body | Out-Null
  }
  $transfer = Wait-Transfer $transferId @("review", "writing", "paused_quota", "paused_client")
}

# --- 5. Итог ------------------------------------------------------------------------
Step "Итог"
Show-Transfer $transfer
Write-Host "сеты: $($transfer.resolved_targets -join ', ')"
if ($transfer.status -eq "done") {
  Write-Host "Проверьте на https://soundcloud.com/you/sets: один приватный сет «$Title», без дублей." -ForegroundColor Green
} else {
  Write-Host "Перенос не завершён ($($transfer.status)). Логи: docker compose logs --tail 200 api worker-extension" -ForegroundColor Red
  exit 1
}
