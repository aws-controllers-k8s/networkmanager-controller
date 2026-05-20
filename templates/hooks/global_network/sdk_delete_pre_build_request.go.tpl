if r.ko.Status.State != nil && (*r.ko.Status.State == string(svcapitypes.GlobalNetworkState_DELETING) ) {
	return r, requeueWaitWhileDeleting
}