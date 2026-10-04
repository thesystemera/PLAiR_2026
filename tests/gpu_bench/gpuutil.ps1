param([int]$Samples = 4, [string]$Luid = '0x0001529E')
$sums = @()
$data = Get-Counter "\GPU Engine(*$Luid*engtype_3D)\Utilization Percentage" -SampleInterval 1 -MaxSamples $Samples
foreach ($set in $data) {
  $sums += ($set.CounterSamples | Measure-Object -Property CookedValue -Sum).Sum
}
$sums | ConvertTo-Json -Compress
