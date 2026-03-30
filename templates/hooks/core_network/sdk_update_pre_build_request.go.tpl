if delta.DifferentAt("Spec.Tags") {
	err := syncTags(
		ctx, rm.sdkapi, rm.metrics, 
		string(*desired.ko.Status.ACKResourceMetadata.ARN), 
		desired.ko.Spec.Tags, latest.ko.Spec.Tags,
	)
	if err != nil {
		return nil, err
	}
}
if !delta.DifferentExcept("Spec.Tags") {
    return desired, nil
}
if delta.DifferentAt("Spec.PolicyDocument") && desired.ko.Spec.PolicyDocument != nil {
	if err := rm.syncPolicyDocument(ctx, desired, latest); err != nil {
		return nil, err
	}
}
