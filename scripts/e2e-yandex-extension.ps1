<#
.SYNOPSIS
  Ручной e2e 4c-4: лайки SoundCloud → новый плейлист Яндекс Музыки через браузерное расширение.

.DESCRIPTION
  Входит под пользователем SyncPlaylists, к которому привязано расширение (пароль
  спрашивается и не печатается). Проверяет, что и SoundCloud, и Яндекс подключены через
  расширение (transport=extension): лайки читаются из браузера, поиск и запись Яндекса —
  тоже из браузера (общий серверный лимит Яндекса не расходуется). Источник — свои лайки
  SoundCloud (медиатека аккаунта) или ссылка -Link.

  Дальше раз в 3 с печатает статус, а на паузе — и её причину (paused_client: offline,
  no_permission, logged_out, captcha, session_mismatch). Скрипт ждёт и дальше: посреди
  записи можно закрыть браузер или отозвать доступ к music.yandex.ru и увидеть, как
  перенос встал и продолжился сам.

  Чтобы запись шла несколькими пачками (и браузер было когда закрыть), на время проверки
  поставьте в .env QUEUE__CLIENT_WRITE_BATCH=10 и пересоздайте контейнеры.

.EXAMPLE
  .\scripts\e2e-yandex-extension.ps1 -Email me@example.com
.EXAMPLE
  .\scripts\e2e-yandex-extension.ps1 -Email me@example.com -Link "https://soundcloud.com/<user>/likes" -AcceptUncertain
#>
param(
  [Parameter(Mandatory = $true)][string]$Email,
  [string]$Link = "",
  [string]$Title = "",
  [switch]$AcceptUncertain,
  [string]$Api = "http://localhost:8000",
  [int]$TimeoutMinutes = 90
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession

function Step($text) { Write-Host "`n=== $text" -ForegroundColor Cyan }

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

# PowerShell 5.1: ConvertFrom-Json отдаёт JSON-массив одним объектом — раскладываем.
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
  if ($transfer.status -eq "paused_quota") { $color = "Magenta" }
  Write-Host $line -ForegroundColor $color
}

function Wait-Transfer($transferId, [string[]]$activeStatuses) {
  $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
  $last = ""
  do {
    $transfer = Api GET "/transfers/$transferId"
    $state = "$($transfer.status) $($transfer.progress.matched) $($transfer.progress.added) $($transfer.progress.pause_reason)"
    if ($state -ne $last) {
      Show-Transfer $transfer
      if ($transfer.status -eq "paused_client") {
        Write-Host "  Перенос ждёт расширение. Продолжится сам, когда браузер/площадка снова готовы." -ForegroundColor Yellow
      }
      if ($transfer.status -eq "paused_quota") {
        Write-Host "  Пауза по квоте — для переноса через расширение её быть не должно. Пришлите логи." -ForegroundColor Magenta
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
Step "Аккаунты площадок (обе — через расширение)"
$accounts = @(Expand-Items (Api GET "/accounts"))
foreach ($platform in @("soundcloud", "yandex")) {
  $all = @($accounts | Where-Object { $_.platform -eq $platform -and $_.status -eq "active" })
  foreach ($account in $all) {
    Write-Host ("{0,-10} {1} (id {2}, {3})" -f $platform, $account.display_name, $account.external_user_id, $account.transport)
  }
}
$soundcloud = $accounts | Where-Object { $_.platform -eq "soundcloud" -and $_.status -eq "active" -and $_.transport -eq "extension" } | Select-Object -First 1
$yandex = $accounts | Where-Object { $_.platform -eq "yandex" -and $_.status -eq "active" -and $_.transport -eq "extension" } | Select-Object -First 1
if (-not $soundcloud) {
  throw "SoundCloud не подключён через расширение. В окне расширения включите SoundCloud и нажмите «Проверить вход»."
}
if (-not $yandex) {
  throw "Яндекс не подключён через расширение. В окне расширения включите «Яндекс Музыка», выдайте доступ к music.yandex.ru и нажмите «Проверить вход»."
}

# --- 3. Источник --------------------------------------------------------------------
Step "Источник"
if ($Link) {
  $resolved = Api POST "/links/resolve" @{ url = $Link }
  Write-Host "$($resolved.platform) / $($resolved.kind): «$($resolved.title)», треков: $($resolved.track_count)"
  $source = @{ kind = "link"; url = $Link }
} else {
  Write-Host "лайки SoundCloud аккаунта $($soundcloud.display_name)"
  $source = @{ kind = "library"; platform = "soundcloud"; account_id = $soundcloud.id }
}

# --- 4. Перенос ---------------------------------------------------------------------
Step "Перенос в новый плейлист Яндекса"
if (-not $Title) { $Title = "SP test 4c-4 $(Get-Date -Format 'dd.MM HH:mm')" }
$transfer = Api POST "/transfers" @{
  source      = $source
  destination = @{ kind = "new"; platform = "yandex"; title = $Title }
}
$transferId = $transfer.id
Write-Host "перенос: $transferId → «$Title»"
$transfer = Wait-Transfer $transferId @("queued", "running", "writing", "paused_quota", "paused_client")

if ($transfer.status -eq "review") {
  $uncertain = @($transfer.items | Where-Object status -eq "uncertain")
  # Запись после ревью — только явной командой POST /transfers/{id}/write (этап 5-0):
  # не решённые сомнительные она пропустит, ненайденные останутся в отчёте ненайденными.
  $notFound = @($transfer.items | Where-Object status -eq "not_found")
  if (-not $AcceptUncertain) {
    Write-Host "Ждёт ревью ($($uncertain.Count) uncertain, $($notFound.Count) not_found). Перезапустите с -AcceptUncertain или решите вручную:" -ForegroundColor Yellow
    Write-Host "  POST $Api/transfers/$transferId/items/<#>/resolve  {chosen_platform, chosen_external_id} или {}"
    Write-Host "  затем POST $Api/transfers/$transferId/write — запись начинается только по ней"
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
  Step "Запускаю запись ($($notFound.Count) ненайденных останутся в отчёте)"
  Api POST "/transfers/$transferId/write" | Out-Null
  Write-Host "Запись началась. Самое время закрыть браузер (сценарий A) или отозвать доступ к music.yandex.ru (сценарий B)." -ForegroundColor Cyan
  $transfer = Wait-Transfer $transferId @("review", "writing", "paused_quota", "paused_client")
}

# --- 5. Итог ------------------------------------------------------------------------
Step "Итог"
Show-Transfer $transfer
Write-Host "плейлисты: $($transfer.resolved_targets -join ', ')"
if ($transfer.status -eq "done") {
  $expected = $transfer.progress.added
  Write-Host "Проверьте на https://music.yandex.ru/users/$($yandex.external_user_id)/playlists: один приватный плейлист «$Title», треков $expected, без дублей, в порядке источника." -ForegroundColor Green
} else {
  Write-Host "Перенос не завершён ($($transfer.status)). Логи: docker compose logs --tail 200 api worker-extension" -ForegroundColor Red
  exit 1
}
