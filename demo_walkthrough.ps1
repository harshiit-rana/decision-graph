# Decision Graph - Automated Hackathon Video Demo Runner
# Run this script while recording your screen (Win + Alt + R in Windows, or Loom / OBS)

function Pause-Step ($message, $seconds = 6) {
    Write-Host "`n=== [NEXT STEP IN $seconds SECONDS] $message ===" -ForegroundColor Cyan
    Start-Sleep -Seconds $seconds
}

Clear-Host
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "       DECISION GRAPH - HACKATHON DEMO WALKTHROUGH        " -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "Tip: Press Windows Key + Alt + R to start recording your screen now!`n" -ForegroundColor Gray
Start-Sleep -Seconds 3

# Step 1: System Health & Preflight
Clear-Host
Write-Host ">>> [1/6] Preflight Health Check: docker compose run --rm app doctor" -ForegroundColor Magenta
docker compose run --rm app doctor
Pause-Step "Moving to repository status & graph metrics" 5

# Step 2: Repository Status
Clear-Host
Write-Host ">>> [2/6] Ingested Graph Status: docker compose run --rm app status" -ForegroundColor Magenta
docker compose run --rm app status
Pause-Step "Querying: Why was the default redirect code changed to 303?" 5

# Step 3: Causal Reconstruction (Why #5895?)
Clear-Host
Write-Host ">>> [3/6] Causal Reconstruction: docker compose run --rm app query '#5895'" -ForegroundColor Magenta
docker compose run --rm app query "#5895"
Pause-Step "Analyzing downstream impact of issue #5815" 8

# Step 4: Downstream Impact Analysis
Clear-Host
Write-Host ">>> [4/6] Downstream Impact: docker compose run --rm app query '#5815' --mode impact" -ForegroundColor Magenta
docker compose run --rm app query "#5815" --mode impact
Pause-Step "Generating architecture visual diagram (Mermaid)" 8

# Step 5: Visual Graph Diagram
Clear-Host
Write-Host ">>> [5/6] High-Contrast Visual Graph: docker compose run --rm app query '#5895' --format mermaid" -ForegroundColor Magenta
docker compose run --rm app query "#5895" --format mermaid
Pause-Step "Running Natural Language Q&A powered by Nvidia NIM" 6

# Step 6: Natural Language Q&A (Nvidia NIM)
Clear-Host
Write-Host ">>> [6/6] Natural Language Q&A: docker compose run --rm app ask 'why did we change the redirect code?'" -ForegroundColor Magenta
docker compose run --rm app ask "why did we change the redirect code?"
Write-Host "`n==========================================================" -ForegroundColor Green
Write-Host "              DEMO COMPLETE - READY TO SUBMIT!            " -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Green
