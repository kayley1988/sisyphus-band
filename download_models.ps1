$ErrorActionPreference = "Stop"
$P = "http://127.0.0.1:7993"
$KwsUrl = "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01.tar.bz2"
$VoskUrl = "https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip"

$dir = "E:\音乐\档3实现\models"
New-Item -ItemType Directory -Force -Path $dir | Out-Null

function Get-Utf8Url {
    param([string]$u)
    $b = [System.Text.Encoding]::UTF8.GetBytes($u)
    $f = [System.Net.WebClient]::new()
    $f.Headers.Add("User-Agent","Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    $f.Proxy = [System.Net.WebProxy]::new($P, $false)
    return $f
}

$wc = Get-Utf8Url $KwsUrl
$K = Join-Path $dir "kws_model.tar.bz2"
Write-Host "[1/3] 下载 KWS 模型 (sherpa-onnx 唤醒词) ..."
$wc.DownloadFile($KwsUrl, $K)
Write-Host ("    大小 = " + ((Get-Item $K).Length / 1MB).ToString("F2") + " MB")

$wc2 = Get-Utf8Url $VoskUrl
$V = Join-Path $dir "vosk-model-small-cn-0.22.zip"
Write-Host "[2/3] 下载 Vosk 模型 (中文离线识别) ..."
$wc2.DownloadFile($VoskUrl, $V)
Write-Host ("    大小 = " + ((Get-Item $V).Length / 1MB).ToString("F2") + " MB")

Write-Host "[3/3] 两个文件已下载到 $dir"
Get-ChildItem $dir
