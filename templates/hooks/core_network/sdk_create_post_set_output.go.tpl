policyResp, err := rm.sdkapi.GetCoreNetworkPolicy(ctx, &svcsdk.GetCoreNetworkPolicyInput{
		CoreNetworkId: ko.Status.CoreNetworkID,
		Alias:         svcsdktypes.CoreNetworkPolicyAliasLive,
})
// Immediately after creating the Core Network, the live policy may not be available yet. If we get a ResourceNotFoundException,
// we can ignore it and assume that the policy will be available on the next read.
if err != nil {
	var awsErr smithy.APIError
	if !errors.As(err, &awsErr) || awsErr.ErrorCode() != "ValidationException" {
		return nil, err
	}
}

if policyResp != nil && policyResp.CoreNetworkPolicy != nil {
	ko.Status.PolicyVersionID = aws.Int64(int64(*policyResp.CoreNetworkPolicy.PolicyVersionId))
} else {
	ko.Status.PolicyVersionID = nil
}
