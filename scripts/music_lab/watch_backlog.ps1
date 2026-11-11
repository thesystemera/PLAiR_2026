param([string]$Log = "backlog.log")
Get-Content -Path "E:\AI_RADIO\data\logs\$Log" -Tail 40 -Wait | Where-Object { $_ -match '\[Lane [0-9b]+\]|Complete -> Catalog|\[Backlog\]|\[ERROR\]|Traceback|Backlog:' }
