# =====================================================================
#  Nexvora - jalankan semuanya dengan satu perintah (Windows)
#  Simpan file ini di E:\nexvora lalu jalankan di PowerShell:
#     powershell -ExecutionPolicy Bypass -File .\start.ps1
# =====================================================================
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "`n[GAGAL] $msg" -ForegroundColor Red; exit 1 }

function New-Secret([int]$bytes) {
    $b = New-Object byte[] $bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    return ([Convert]::ToBase64String($b) -replace '[+/=]', '')
}

function Get-EnvValue($key) {
    $line = Get-Content .env | Where-Object { $_ -match "^$key=" } | Select-Object -First 1
    if ($line) { return $line.Substring($key.Length + 1) } else { return $null }
}

# ---------------------------------------------------------------- 1. Docker
Step "Memeriksa Docker"
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    $desktop = Join-Path $Env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $desktop)) { Fail "Docker Desktop belum terinstal. Unduh dari https://www.docker.com/products/docker-desktop" }
    Write-Host "Docker Desktop belum jalan, sedang dinyalakan..."
    Start-Process $desktop
    $ok = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 3
        docker info *> $null
        if ($LASTEXITCODE -eq 0) { $ok = $true; break }
        Write-Host -NoNewline "."
    }
    if (-not $ok) { Fail "Docker Engine tidak menyala dalam 3 menit. Buka Docker Desktop manual dan pastikan statusnya 'Engine running'." }
}
Write-Host "Docker siap."

# ---------------------------------------------------------------- 2. .env
Step "Menyiapkan konfigurasi (.env)"
if (-not (Test-Path .env)) {
    if (-not (Test-Path .env.example)) { Fail "File .env.example tidak ditemukan. Pastikan script ini ada di folder E:\nexvora." }
    $adminPw = "Nx-" + (New-Secret 12) + "!7a"
    $content = Get-Content .env.example
    $content = $content -replace '^POSTGRES_OWNER_PASSWORD=.*', ("POSTGRES_OWNER_PASSWORD=" + (New-Secret 24))
    $content = $content -replace '^APP_DB_PASSWORD=.*',         ("APP_DB_PASSWORD=" + (New-Secret 24))
    $content = $content -replace '^REDIS_PASSWORD=.*',          ("REDIS_PASSWORD=" + (New-Secret 24))
    $content = $content -replace '^JWT_SECRET=.*',              ("JWT_SECRET=" + (New-Secret 48))
    $content = $content -replace '^BOOTSTRAP_ADMIN_EMAIL=.*',   "BOOTSTRAP_ADMIN_EMAIL=admin@nexvora.id"
    $content = $content -replace '^BOOTSTRAP_ADMIN_PASSWORD=.*', "BOOTSTRAP_ADMIN_PASSWORD=$adminPw"
    # ascii = tanpa BOM, supaya docker compose membaca baris pertama dengan benar
    $content | Set-Content .env -Encoding ascii
    Write-Host ".env dibuat dengan password acak."
} else {
    Write-Host ".env sudah ada, dipakai apa adanya."
}

# ---------------------------------------------------------------- 3. line ending
Step "Memastikan script Postgres memakai format Linux (LF)"
$initScript = "infrastructure\docker\postgres\01-app-role.sh"
if (Test-Path $initScript) {
    $text = [IO.File]::ReadAllText((Resolve-Path $initScript))
    if ($text.Contains("`r`n")) {
        [IO.File]::WriteAllText((Resolve-Path $initScript), $text.Replace("`r`n", "`n"), (New-Object System.Text.UTF8Encoding $false))
        Write-Host "Diperbaiki (CRLF -> LF)."
    } else { Write-Host "Sudah benar." }
}

# ---------------------------------------------------------------- 4. build & up
Step "Membangun dan menjalankan container (pertama kali bisa 5-10 menit)"
docker compose up --build -d
if ($LASTEXITCODE -ne 0) { Fail "docker compose up gagal. Cek pesan di atas atau jalankan: docker compose logs" }

# ---------------------------------------------------------------- 5. tunggu siap
Step "Menunggu aplikasi siap di http://localhost"
$ready = $false
for ($i = 0; $i -lt 90; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost/login" -UseBasicParsing -TimeoutSec 5
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
    Start-Sleep -Seconds 3
    Write-Host -NoNewline "."
}
if (-not $ready) {
    docker compose ps
    Fail "Aplikasi belum merespons. Kirim output: docker compose logs migrate api web"
}
Write-Host "`nAplikasi siap."

# ---------------------------------------------------------------- 6. akun admin
Step "Membuat akun admin (aman dijalankan berulang)"
$email = Get-EnvValue "BOOTSTRAP_ADMIN_EMAIL"
$pw    = Get-EnvValue "BOOTSTRAP_ADMIN_PASSWORD"
docker compose run --rm -e "BOOTSTRAP_ADMIN_EMAIL=$email" -e "BOOTSTRAP_ADMIN_PASSWORD=$pw" api python -m scripts.bootstrap --demo
if ($LASTEXITCODE -ne 0) { Fail "Bootstrap gagal. Pastikan BOOTSTRAP_ADMIN_PASSWORD di .env minimal 12 karakter dan cukup kuat." }

# ---------------------------------------------------------------- 7. selesai
Write-Host ""
Write-Host "==============================================================" -ForegroundColor Green
Write-Host "  Nexvora berjalan di http://localhost" -ForegroundColor Green
Write-Host "--------------------------------------------------------------"
Write-Host "  Super admin   : tenant 'platform'  | $email"
Write-Host "  Tenant demo   : tenant 'demo'      | admin@demo.nexvora.id"
Write-Host "  Password      : $pw"
Write-Host "  (tersimpan di E:\nexvora\.env)"
Write-Host "--------------------------------------------------------------"
Write-Host "  Hentikan      : docker compose down"
Write-Host "  Lihat log     : docker compose logs -f api web worker"
Write-Host "==============================================================" -ForegroundColor Green
Start-Process "http://localhost"
