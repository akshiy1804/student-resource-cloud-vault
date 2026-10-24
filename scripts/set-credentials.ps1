# Usage (dot-source is optional; env vars persist for this PowerShell window either way):
#   1. AWS Academy > Start Lab > AWS Details > Show > copy the "AWS CLI" block
#   2. .\scripts\set-credentials.ps1
# Reads the clipboard, sets 3 environment variables for THIS window only, never prints values.
$text = Get-Clipboard -Raw
$map = @{ aws_access_key_id = 'AWS_ACCESS_KEY_ID'; aws_secret_access_key = 'AWS_SECRET_ACCESS_KEY'; aws_session_token = 'AWS_SESSION_TOKEN' }
$found = 0
foreach ($line in ($text -split "`r?`n")) {
    if ($line -match '^\s*(aws_access_key_id|aws_secret_access_key|aws_session_token)\s*=\s*(\S+)\s*$') {
        Set-Item -Path ("Env:" + $map[$Matches[1]]) -Value $Matches[2]
        $found++
    }
}
if ($found -eq 3) {
    $env:AWS_REGION = 'us-east-1'
    Write-Host 'AWS credentials set for this PowerShell window (values not shown). Start the app from THIS window.'
} else {
    Write-Warning "Expected 3 credential lines on the clipboard, found $found. Copy the AWS CLI block first."
}
