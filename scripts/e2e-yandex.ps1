<#
.SYNOPSIS
  Ручной e2e переноса (Яндекс или SoundCloud) → новый плейлист на Яндексе одной командой.

.DESCRIPTION
  Регистрирует свежего пользователя, подключает Яндекс токеном YANDEX_LIVE_TOKEN из .env
  (токен не печатается), запускает перенос по ссылке, ждёт окончания матчинга и печатает
  сводку matched / uncertain / not_found со списком проблемных треков.
  Если перенос остановился в REVIEW: с -AcceptUncertain принимает первого кандидата у всех
  uncertain и доводит перенос до записи; без него — печатает, как решить вручную.

  Приложение должно быть запущено (make up или make e2e — он поднимает его сам).

.EXAMPLE
  .\scripts\e2e-yandex.ps1 -Link "https://music.yandex.ru/users/someone/playlists/1000"
.EXAMPLE
  .\scripts\e2e-yandex.ps1 -Link "..." -AcceptUncertain
#>
param(
  [Parameter(Mandatory = $true)][string]$Link,
  [string]$Title = "",
  [switch]$AcceptUncertain,
  [string]$Api = "http://localhost:8000",
  [int]$TimeoutMinutes = 30
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

function Wait-Api {
  for ($i = 0; $i -lt 60; $i++) {
    try {
      Invoke-WebRequest "$Api/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
      return
    } catch { Start-Sleep 2 }
  }
  throw "API не отвечает на $Api/health — запущено ли приложение (make up)?"
}

function Wait-Transfer($transferId, [string[]]$activeStatuses) {
  $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
  $lastState = ""
  $lastChange = Get-Date
  $hinted = $false
  do {
    $transfer = Api GET "/transfers/$transferId"
    $counts = ($transfer.items | Group-Object status | ForEach-Object { "$($_.Name)=$($_.Count)" }) -join " "
    $eta = ""
    if ($transfer.progress -and $null -ne $transfer.progress.eta_seconds) {
      $eta = "  (осталось ~$($transfer.progress.eta_seconds) с)"
    }
    Write-Host ("{0}  {1,-8} {2}{3}" -f (Get-Date -Format HH:mm:ss), $transfer.status, $counts, $eta)
    $state = "$($transfer.status) $counts"
    if ($state -ne $lastState) {
      $lastState = $state
      $lastChange = Get-Date
      $hinted = $false
    } elseif (-not $hinted -and ((Get-Date) - $lastChange).TotalSeconds -gt 30) {
      Write-Host "  Прогресса нет больше 30 с — вероятно, площадка попросила паузу (429)." -ForegroundColor Yellow
      Write-Host "  Задачи продолжат сами после паузы; подробности: make logs" -ForegroundColor Yellow
      $hinted = $true
    }
    if ($transfer.status -notin $activeStatuses) { return $transfer }
    Start-Sleep 3
  } while ((Get-Date) -lt $deadline)
  throw "Перенос не закончился за $TimeoutMinutes мин. Логи: make logs"
}

function Show-Summary($transferId) {
  $pgUser = Get-EnvValue "POSTGRES_USER"
  $pgDb = Get-EnvValue "POSTGRES_DB"
  $countsSql = "SELECT status, count(*) AS tracks FROM transfer_items WHERE transfer_id = '$transferId' GROUP BY status ORDER BY status;"
  # Оценки отдельных кандидатов не хранятся — показываем первого: кандидаты той же
  # версии стоят первыми, дальше — порядок поиска площадки.
  # Без двойных кавычек: PowerShell 5.1 выбрасывает их из аргументов внешних программ
  # (psql получил бы битый запрос) — поэтому подписи колонок латиницей без кавычек.
  $listSql = @"
SELECT ti.position AS pos, ti.status,
       pt.raw_artist || ' - ' || pt.raw_title AS source_track,
       COALESCE((ti.candidates->0->>'artist') || ' - ' || (ti.candidates->0->>'title'), '-') AS first_candidate,
       jsonb_array_length(ti.candidates) AS candidates
FROM transfer_items ti
JOIN platform_tracks pt ON pt.id = ti.source_pt_id
WHERE ti.transfer_id = '$transferId' AND ti.status IN ('uncertain', 'not_found')
ORDER BY ti.status, ti.position;
"@
  Step "Сводка по статусам"
  docker compose exec -T postgres psql -U $pgUser -d $pgDb -c $countsSql
  Step "uncertain / not_found"
  docker compose exec -T postgres psql -U $pgUser -d $pgDb -c $listSql
}

# --- 1. API -------------------------------------------------------------------------
Step "Проверка API"
Wait-Api
Write-Host "API отвечает: $Api"

# --- 2. Пользователь ----------------------------------------------------------------
Step "Регистрация"
$email = "e2e-$(Get-Date -Format yyyyMMddHHmmss)@example.com"
Api POST "/auth/register" @{ email = $email; password = "correct horse battery" } | Out-Null
Write-Host "пользователь: $email"

# --- 3. Аккаунт Яндекса -------------------------------------------------------------
Step "Подключение Яндекса токеном из .env"
$token = Get-EnvValue "YANDEX_LIVE_TOKEN"
if (-not $token) { throw "В .env нет YANDEX_LIVE_TOKEN (см. docs/YANDEX_TOKEN.md)" }
try {
  $account = Api POST "/accounts" @{ platform = "yandex"; access_token = $token }
} finally {
  Remove-Variable token
}
Write-Host "аккаунт: $($account.display_name), статус $($account.status)"

# Источник на SoundCloud (сет или лайки soundcloud.com/you/likes) — нужен и его аккаунт.
$scToken = Get-EnvValue "SOUNDCLOUD_LIVE_TOKEN"
if ($scToken) {
  Step "Подключение SoundCloud токеном из .env"
  try {
    $body = @{ platform = "soundcloud"; access_token = $scToken }
    $scRefresh = Get-EnvValue "SOUNDCLOUD_LIVE_REFRESH_TOKEN"
    if ($scRefresh) { $body.refresh_token = $scRefresh }
    $scAccount = Api POST "/accounts" $body
  } finally {
    Remove-Variable scToken, scRefresh -ErrorAction SilentlyContinue
    Remove-Variable body -ErrorAction SilentlyContinue
  }
  Write-Host "аккаунт: $($scAccount.display_name), статус $($scAccount.status)"
}

# --- 4. Ссылка ----------------------------------------------------------------------
Step "Разбор ссылки"
$resolved = Api POST "/links/resolve" @{ url = $Link }
Write-Host "площадка: $($resolved.platform), вид: $($resolved.kind), название: $($resolved.title), треков: $($resolved.track_count)"

# --- 5. Перенос ---------------------------------------------------------------------
Step "Запуск переноса"
if (-not $Title) { $Title = "SyncPlaylists e2e $(Get-Date -Format 'dd.MM HH:mm')" }
$transfer = Api POST "/transfers" @{
  source      = @{ kind = "link"; url = $Link }
  destination = @{ kind = "new"; platform = "yandex"; title = $Title; description = "Ручной e2e SyncPlaylists" }
}
$transferId = $transfer.id
Write-Host "перенос: $transferId → новый плейлист «$Title»"

Step "Матчинг"
$transfer = Wait-Transfer $transferId @("queued", "running", "writing")
Show-Summary $transferId

# --- 6. Ревью -----------------------------------------------------------------------
if ($transfer.status -eq "review") {
  $uncertain = @($transfer.items | Where-Object status -eq "uncertain")
  if ($AcceptUncertain) {
    Step "Принимаю первого кандидата у $($uncertain.Count) uncertain"
    foreach ($item in $uncertain) {
      if ($item.candidates.Count -gt 0) {
        $c = $item.candidates[0]
        $body = @{ chosen_platform = $c.platform; chosen_external_id = $c.external_id }
      } else {
        $body = @{}
      }
      Api POST "/transfers/$transferId/items/$($item.position)/resolve" $body | Out-Null
    }
    Step "Запись"
    $transfer = Wait-Transfer $transferId @("review", "writing")
  } else {
    Step "Перенос ждёт ручного решения ($($uncertain.Count) uncertain)"
    Write-Host "Принять всех первых кандидатов и дописать плейлист — перезапустите с -AcceptUncertain"
    Write-Host "(make e2e ... ACCEPT=1) на новом переносе, либо решите вручную:"
    Write-Host "  POST $Api/transfers/$transferId/items/<#>/resolve  {chosen_platform, chosen_external_id} или {}"
  }
}

Step "Итог"
$final = Api GET "/transfers/$transferId"
$counts = ($final.items | Group-Object status | ForEach-Object { "$($_.Name)=$($_.Count)" }) -join " "
Write-Host "статус: $($final.status)   $counts"
if ($final.status -eq "done") {
  Write-Host "Плейлист «$Title» должен появиться в Яндекс Музыке (приватный)." -ForegroundColor Green
} elseif ($final.status -eq "failed") {
  Write-Host "Перенос FAILED — причина в логах: make logs" -ForegroundColor Red
  exit 1
}
