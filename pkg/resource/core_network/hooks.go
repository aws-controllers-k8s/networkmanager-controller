package core_network

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"

	"github.com/aws-controllers-k8s/networkmanager-controller/pkg/tags"
	"github.com/aws-controllers-k8s/runtime/pkg/requeue"
	"github.com/aws/aws-sdk-go-v2/aws"
	svcsdk "github.com/aws/aws-sdk-go-v2/service/networkmanager"
	svcsdktypes "github.com/aws/aws-sdk-go-v2/service/networkmanager/types"
	smithy "github.com/aws/smithy-go"
)

var syncTags = tags.SyncTags

type coreNetworkPolicyClient interface {
	GetCoreNetworkPolicy(context.Context, *svcsdk.GetCoreNetworkPolicyInput, ...func(*svcsdk.Options)) (*svcsdk.GetCoreNetworkPolicyOutput, error)
	PutCoreNetworkPolicy(context.Context, *svcsdk.PutCoreNetworkPolicyInput, ...func(*svcsdk.Options)) (*svcsdk.PutCoreNetworkPolicyOutput, error)
	ExecuteCoreNetworkChangeSet(context.Context, *svcsdk.ExecuteCoreNetworkChangeSetInput, ...func(*svcsdk.Options)) (*svcsdk.ExecuteCoreNetworkChangeSetOutput, error)
}

var (
	requeueWaitWhileDeleting = requeue.NeededAfter(
		errors.New(GroupKind.Kind+" is deleting"),
		requeue.DefaultRequeueAfterDuration,
	)
	requeueWaitWhilePolicyDocumentUpdating = requeue.NeededAfter(
		errors.New(GroupKind.Kind+" policy document is updating"),
		requeue.DefaultRequeueAfterDuration,
	)
	requeueWaitWhilePolicyDocumentExecuting = requeue.NeededAfter(
		errors.New(GroupKind.Kind+" policy document is executing"),
		requeue.DefaultRequeueAfterDuration,
	)
	requeueWaitWhilePolicyDocumentGenerating = requeue.NeededAfter(
		errors.New(GroupKind.Kind+" policy document changeset is pending generation"),
		requeue.DefaultRequeueAfterDuration,
	)
)

func syncPolicyDocument(
	ctx context.Context,
	client coreNetworkPolicyClient,
	desired *resource,
	live *resource,
) error {
	coreNetworkID := live.ko.Status.CoreNetworkID

	// Grab latest policy document.
	// For the resource to be in sync, latest, live and desired policy documents all need to be the same.
	latestPolicyResp, err := client.GetCoreNetworkPolicy(ctx, &svcsdk.GetCoreNetworkPolicyInput{
		CoreNetworkId: coreNetworkID,
		Alias:         svcsdktypes.CoreNetworkPolicyAliasLatest,
	})
	// If there are no policies at all, GetCoreNetworkPolicy will throw a ValidationException.
	// We can ignore this error and treat it as if there is no latest policy.
	if err != nil && !isValidationException(err) {
		return err
	}

	latestPolicyVersionId := int32(0)
	var latestPolicyDocument *string
	latestPolicyChangeSetState := svcsdktypes.ChangeSetState("")
	if latestPolicyResp != nil && latestPolicyResp.CoreNetworkPolicy != nil {
		latestPolicyVersionId = *latestPolicyResp.CoreNetworkPolicy.PolicyVersionId
		latestPolicyDocument = latestPolicyResp.CoreNetworkPolicy.PolicyDocument
		latestPolicyChangeSetState = latestPolicyResp.CoreNetworkPolicy.ChangeSetState
	}

	livePolicyVersionID := int32(0)
	if live.ko.Status.PolicyVersionID != nil {
		livePolicyVersionID = int32(*live.ko.Status.PolicyVersionID)
	}

	// If desired is different from latest, we need to submit the new policy document for execution.
	if !arePolicyDocumentsEqual(desired.ko.Spec.PolicyDocument, latestPolicyDocument) {
		putInput := &svcsdk.PutCoreNetworkPolicyInput{
			CoreNetworkId:  coreNetworkID,
			PolicyDocument: desired.ko.Spec.PolicyDocument,
		}
		_, err := client.PutCoreNetworkPolicy(ctx, putInput)
		if err != nil {
			return err
		}
		// Requeue immediately to start polling for READY_TO_EXECUTE status
		return requeueWaitWhilePolicyDocumentUpdating
	}

	// At this point, desired and latest policy documents are the same,
	// so we can check latest and walk the state machine until
	// it concides with live.

	if latestPolicyVersionId == livePolicyVersionID {
		// desired, latest and live are all the same, we are in sync.
		return nil
	}

	switch latestPolicyChangeSetState {
	case svcsdktypes.ChangeSetStatePendingGeneration:
		return requeueWaitWhilePolicyDocumentGenerating

	case svcsdktypes.ChangeSetStateReadyToExecute:
		_, err := client.ExecuteCoreNetworkChangeSet(ctx, &svcsdk.ExecuteCoreNetworkChangeSetInput{
			CoreNetworkId:   coreNetworkID,
			PolicyVersionId: aws.Int32(latestPolicyVersionId),
		})
		if err != nil {
			return err
		}
		return requeueWaitWhilePolicyDocumentExecuting

	case svcsdktypes.ChangeSetStateExecuting:
		return requeueWaitWhilePolicyDocumentExecuting
	}
	return nil
}

// arePolicyDocumentsEqual performs a semantic JSON comparison of the two provided policy documents.
// If either document is not valid JSON, it falls back to a string comparison. Nil and empty are considered equal.
func arePolicyDocumentsEqual(a, b *string) bool {
	var jsonA, jsonB any
	sA := normalizePolicyDocument(a)
	sB := normalizePolicyDocument(b)
	errA := json.Unmarshal([]byte(sA), &jsonA)
	errB := json.Unmarshal([]byte(sB), &jsonB)

	if errA != nil || errB != nil {
		return sA == sB
	}

	return reflect.DeepEqual(jsonA, jsonB)
}

func normalizePolicyDocument(policyDocument *string) string {
	if policyDocument == nil {
		return ""
	}
	return *policyDocument
}

func isValidationException(err error) bool {
	if err == nil {
		return false
	}
	var awsErr smithy.APIError
	return errors.As(err, &awsErr) && awsErr.ErrorCode() == "ValidationException"
}
