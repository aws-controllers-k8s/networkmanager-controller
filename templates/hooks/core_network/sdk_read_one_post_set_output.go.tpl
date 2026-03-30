policyResp, err := rm.sdkapi.GetCoreNetworkPolicy(ctx, &svcsdk.GetCoreNetworkPolicyInput{
	CoreNetworkId: ko.Status.CoreNetworkID,
	Alias:         svcsdktypes.CoreNetworkPolicyAliasLive,
})
if err != nil {
	var awsErr smithy.APIError
	if !errors.As(err, &awsErr) || awsErr.ErrorCode() != "ValidationException" {
		return nil, err
	}
}

if policyResp != nil && policyResp.CoreNetworkPolicy != nil && policyResp.CoreNetworkPolicy.PolicyDocument != nil {
	ko.Spec.PolicyDocument = policyResp.CoreNetworkPolicy.PolicyDocument
	ko.Status.PolicyVersionID = aws.Int64(int64(*policyResp.CoreNetworkPolicy.PolicyVersionId))
}