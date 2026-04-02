# upload_github.ps1 - Upload files to GitHub
# Usage: .\upload_github.ps1

$GH = "C:\Program Files\GitHub CLI\gh.exe"
$OWNER = "douglscustodio"
$REPO = "Agent"
$BRANCH = "main"

# Get current SHA of the branch
$branchInfo = & $GH api repos/$OWNER/$REPO/branches/$BRANCH --jq '.commit.sha'

Write-Host "Branch SHA: $branchInfo"

# Get list of files to upload
$files = Get-ChildItem -Path . -Filter "*.py"
$files += Get-ChildItem -Path . -Filter "*.txt"
$files += Get-ChildItem -Path . -Filter "*.sql"
$files += Get-ChildItem -Path . -Filter "*.example"
$files += Get-ChildItem -Path . -Filter "Procfile"

# Skip .pyc files
$files = $files | Where-Object { $_.Extension -ne ".pyc" }

Write-Host "Files to upload: $($files.Count)"

# Get current tree
$treeUrl = "repos/$OWNER/$REPO/git/trees/$branchInfo"
$currentTree = & $GH api $treeUrl --jq '.tree[] | {path: .path, sha: .sha}'

foreach ($file in $files) {
    Write-Host "Processing: $($file.Name)..."
    
    # Read file content
    $content = Get-Content $file.FullName -Raw -Encoding UTF8
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($content)
    $base64 = [Convert]::ToBase64String($bytes)
    
    # Get SHA if file exists
    $existingSha = ($currentTree | Where-Object { $_.path -eq $file.Name }).sha
    
    # Create blob
    $blobResult = & $GH api repos/$OWNER/$REPO/git/blobs --input - <<< (ConvertTo-Json @{
        content = $content
        encoding = "utf-8"
    })
    
    $blobSha = ($blobResult | ConvertFrom-Json).sha
    Write-Host "  Blob SHA: $blobSha"
}

Write-Host "Done! Note: Full commit requires tree creation and branch update."
Write-Host "Please use Git to push changes manually or use GitHub web interface."
