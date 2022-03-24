if ((Get-AzContext) -eq $null) {
    Connect-AzAccount -UseDeviceAuthentication
}
Connect-AzContainerRegistry -Name indcr
Import-AzAksCredential -ResourceGroupName ind -Name indaks -Force
