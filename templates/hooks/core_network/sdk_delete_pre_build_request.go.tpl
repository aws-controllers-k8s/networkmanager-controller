if r.ko.Status.State != nil && (*r.ko.Status.State == string(svcapitypes.CoreNetworkState_DELETING) ) {
	return r, requeueWaitWhileDeleting
}