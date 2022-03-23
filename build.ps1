if ((Get-AzContext) -eq $null) {
    Connect-AzAccount -UseDeviceAuthentication
}
Connect-AzContainerRegistry -Name indcr
