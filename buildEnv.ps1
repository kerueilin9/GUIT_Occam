$ErrorActionPreference = "Stop"

$credentialPath = "D:\Ker\Downloads\gen-lang-client-0582494559-67ad47221f6d.json"
$projectId = "gen-lang-client-0582494559"
$location = "us-central1"
$targetCondaEnv = "agentoccam"

if (Get-Command deactivate -ErrorAction SilentlyContinue) {
	deactivate
}

if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
	throw "Conda is not available in PATH. Open an Anaconda/Miniconda shell first."
}

$condaHook = conda shell.powershell hook | Out-String
Invoke-Expression $condaHook
conda activate $targetCondaEnv

$env:GOOGLE_APPLICATION_CREDENTIALS = $credentialPath
$env:GOOGLE_CLOUD_PROJECT = $projectId
$env:GOOGLE_CLOUD_LOCATION = $location
$env:GOOGLE_GENAI_USE_VERTEXAI = "true"

Write-Host "Environment ready." -ForegroundColor Green
Write-Host "CONDA_DEFAULT_ENV=$env:CONDA_DEFAULT_ENV"
Write-Host "GOOGLE_APPLICATION_CREDENTIALS=$env:GOOGLE_APPLICATION_CREDENTIALS"
Write-Host "GOOGLE_CLOUD_PROJECT=$env:GOOGLE_CLOUD_PROJECT"
Write-Host "GOOGLE_CLOUD_LOCATION=$env:GOOGLE_CLOUD_LOCATION"
Write-Host "GOOGLE_GENAI_USE_VERTEXAI=$env:GOOGLE_GENAI_USE_VERTEXAI"

if (-not (Test-Path $env:GOOGLE_APPLICATION_CREDENTIALS)) {
	Write-Warning "Credential file not found: $env:GOOGLE_APPLICATION_CREDENTIALS"
}
