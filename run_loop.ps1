# run_loop.ps1
# Этот скрипт дожидается окончания работы search.py (вступления в новые группы),
# после чего запускает основную рассылку run.py. Цикл повторяется каждые 2 часа.

$cooldownSeconds = 7200  # Интервал повтора рассылки (2 часа)

Write-Host "Запуск управляющего цикла рассылки рекламы..." -ForegroundColor Cyan

while ($true) {
    # 1. Проверяем, запущен ли процесс search.py
    $searchRunning = Get-CimInstance Win32_Process -Filter "name = 'python.exe'" | Where-Object { $_.CommandLine -like "*search.py*" }
    if ($searchRunning) {
        Write-Host "Обнаружен активный процесс поиска/вступления в группы (search.py). Ожидаем завершения..." -ForegroundColor Yellow
        Start-Sleep -Seconds 60
        continue
    }

    # 2. Запускаем основную рассылку (run.py)
    Write-Host "Запуск рассылки рекламы (run.py)..." -ForegroundColor Green
    $process = Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "run.py" -NoNewWindow -PassThru -Wait
    
    if ($process.ExitCode -eq 0) {
        Write-Host "Текущий цикл рассылки успешно завершен." -ForegroundColor Green
        
        # 3. Сбрасываем прогресс для следующего запуска
        Write-Host "Сброс прогресса рассылки..." -ForegroundColor Yellow
        & .venv\Scripts\python.exe run.py --reset-progress --dry-run > $null
        
        Write-Host "Ожидание $($cooldownSeconds) секунд (2 часа) перед началом следующего цикла рассылки..." -ForegroundColor Cyan
        Start-Sleep -Seconds $cooldownSeconds
    } else {
        Write-Host "Произошла ошибка при выполнении run.py (код выхода: $($process.ExitCode)). Повторный запуск через 5 минут..." -ForegroundColor Red
        Start-Sleep -Seconds 300
    }
}
