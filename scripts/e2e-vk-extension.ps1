<#
.SYNOPSIS
  Ручной e2e 4c-5: плейлист (Яндекс/SoundCloud/VK по ссылке) → VK Музыка через браузерное расширение.

.DESCRIPTION
  Входит под пользователем SyncPlaylists, к которому привязано расширение (пароль
  спрашивается и не печатается). Проверяет, что VK подключён через расширение
  (transport=extension), и переносит плейлист по ссылке -Link в новый плейлист VK или, с
  -ToLibrary, в «Мою музыку».

  Начинаем с малого: источник не больше -MaxTracks треков (по умолчанию 10, потом 50), в
  «Мою музыку» — не больше 20 за прогон (VK отвечает «flood control» на частые
  добавления). Источник больше лимита — скрипт откажется. Для страховки на сервере на время
  проверки можно поставить в .env PLATFORMS__VK__LIBRARY_DAILY_LIMIT=20 и пересоздать
  контейнеры: больше 20 добавлений за сутки перенос не сделает (встанет на паузу
  paused_quota с причиной vk_library_daily_limit).

  Раз в 3 с печатает статус, а на паузе — её причину: paused_client (offline,
  no_permission, logged_out, captcha, session_mismatch) или paused_quota
  (vk_library_daily_limit, vk_flood_control). Посреди записи можно закрыть браузер или
  отозвать доступ к vk.ru и увидеть, как перенос встал и продолжился сам.

  Чтобы запись шла несколькими пачками, поставьте в .env QUEUE__CLIENT_WRITE_BATCH=10.

.EXAMPLE
  .\scripts\e2e-vk-extension.ps1 -Email me@example.com -Link "https://music.yandex.ru/users/<login>/playlists/<n>"
.EXAMPLE
  .\scripts\e2e-vk-extension.ps1 -Email me@example.com -Link "..." -MaxTracks 50 -AcceptUncertain
.EXAMPLE
  .\scripts\e2e-vk-extension.ps1 -Email me@example.com -Link "..." -ToLibrary -MaxTracks 20
#>
param(
  [Parameter(Mandatory = $true)][string]$Email,
  [Parameter(Mandatory = $true)][string]$Link,
  [string]$Title = "",
  [int]$MaxTracks = 10,
  [switch]$ToLibrary,
  [switch]$AcceptUncertain,
  [string]$Api = "http://localhost:8000",
  [int]$TimeoutMinutes = 90
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$LibraryCap = 20

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
    if ($p.resume_at) { $line += "  [до $($p.resume_at)]" }
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
        Write-Host "  VK просит подождать (лимит суток или flood control) — перенос продолжится сам в указанное время." -ForegroundColor Magenta
      }
      $last = $state
    }
    if ($transfer.status -notin $activeStatuses) { return $transfer }
    Start-Sleep 3
  } while ((Get-Date) -lt $deadline)
  throw "Перенос не закончился за $TimeoutMinutes мин. Логи: docker compose logs --tail 200 api worker-extension"
}

if ($ToLibrary -and $MaxTracks -gt $LibraryCap) {
  throw "В «Мою музыку» — не больше $LibraryCap треков за прогон (сейчас -MaxTracks $MaxTracks)."
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
foreach ($account in @($accounts | Where-Object { $_.status -eq "active" })) {
  Write-Host ("{0,-10} {1} (id {2}, {3})" -f $account.platform, $account.display_name, $account.external_user_id, $account.transport)
}
$vk = $accounts | Where-Object { $_.platform -eq "vk" -and $_.status -eq "active" -and $_.transport -eq "extension" } | Select-Object -First 1
if (-not $vk) {
  throw "VK не подключён через расширение. В окне расширения включите «VK Музыка», выдайте доступ к vk.ru и нажмите «Проверить вход»."
}

# --- 3. Источник --------------------------------------------------------------------
Step "Источник"
$resolved = Api POST "/links/resolve" @{ url = $Link }
Write-Host "$($resolved.platform) / $($resolved.kind): «$($resolved.title)», треков: $($resolved.track_count)"
if ($null -eq $resolved.track_count) {
  throw "Площадка не сообщила число треков — для первых прогонов возьмите плейлист, где оно известно."
}
if ([int]$resolved.track_count -gt $MaxTracks) {
  throw "В источнике $($resolved.track_count) треков, а лимит прогона -MaxTracks $MaxTracks. Начинаем с малого: 10, потом 50."
}

# --- 4. Перенос ---------------------------------------------------------------------
if ($ToLibrary) {
  Step "Перенос в «Мою музыку» VK (не больше $LibraryCap)"
  $destination = @{ kind = "library"; platform = "vk"; account_id = $vk.id }
  $Title = "«Моя музыка»"
} else {
  Step "Перенос в новый плейлист VK"
  if (-not $Title) { $Title = "SP test 4c-5 $(Get-Date -Format 'dd.MM HH:mm')" }
  $destination = @{ kind = "new"; platform = "vk"; title = $Title }
}
$transfer = Api POST "/transfers" @{ source = @{ kind = "link"; url = $Link }; destination = $destination }
$transferId = $transfer.id
Write-Host "перенос: $transferId → $Title"
$transfer = Wait-Transfer $transferId @("queued", "running", "writing", "paused_quota", "paused_client")

if ($transfer.status -eq "review") {
  $uncertain = @($transfer.items | Where-Object status -eq "uncertain")
  # Ненайденные тоже ждут решения (пропустить или выбрать вручную) — без этого запись не начнётся.
  $notFound = @($transfer.items | Where-Object status -eq "not_found")
  if (-not $AcceptUncertain) {
    Write-Host "Ждёт ревью ($($uncertain.Count) uncertain, $($notFound.Count) not_found). Перезапустите с -AcceptUncertain или решите вручную:" -ForegroundColor Yellow
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
  if ($notFound.Count -gt 0) {
    Step "Пропускаю $($notFound.Count) ненайденных"
    foreach ($item in $notFound) {
      Api POST "/transfers/$transferId/items/$($item.position)/resolve" @{} | Out-Null
    }
  }
  Write-Host "Запись началась. Можно закрыть браузер или отозвать доступ к vk.ru и посмотреть на паузу." -ForegroundColor Cyan
  $transfer = Wait-Transfer $transferId @("review", "writing", "paused_quota", "paused_client")
}

# --- 5. Итог ------------------------------------------------------------------------
Step "Итог"
Show-Transfer $transfer
if ($transfer.status -eq "done") {
  $expected = $transfer.progress.added
  if ($ToLibrary) {
    Write-Host "Проверьте https://vk.ru/audios$($vk.external_user_id): сверху $expected новых треков, без дублей; последний трек источника — самый верхний." -ForegroundColor Green
  } else {
    Write-Host "плейлисты: $($transfer.resolved_targets -join ', ')"
    Write-Host "Проверьте https://vk.ru/audios$($vk.external_user_id)?section=playlists: один плейлист «$Title», треков $expected, без дублей, в порядке источника." -ForegroundColor Green
  }
  Write-Host "После проверки удалите тестовый плейлист / добавленные треки в VK вручную."
} else {
  Write-Host "Перенос не завершён ($($transfer.status)). Логи: docker compose logs --tail 200 api worker-extension" -ForegroundColor Red
  exit 1
}
