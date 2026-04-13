policyResp, err := rm.sdkapi.GetCoreNetworkPolicy(ctx, &svcsdk.GetCoreNetworkPolicyInput{
	CoreNetworkId: ko.Status.CoreNetworkID,
	Alias:         svcsdktypes.CoreNetworkPolicyAliasLive,
})
// If the core network has no policy in "live" state yet the request returns a ValidationException
// error, we can ignore it.
if ignoreValidationException(err) != nil {
	return nil, err
}

if policyResp != nil && policyResp.CoreNetworkPolicy != nil && policyResp.CoreNetworkPolicy.PolicyDocument != nil {
	ko.Spec.PolicyDocument = policyResp.CoreNetworkPolicy.PolicyDocument
	ko.Status.PolicyVersionID = aws.Int64(int64(*policyResp.CoreNetworkPolicy.PolicyVersionId))
} else {
	ko.Spec.PolicyDocument = nil
	ko.Status.PolicyVersionID = nil
}